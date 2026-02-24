"""
Lineups fetcher (MLB Stats API).

Stores lineup snapshots in `lineups` with active/inactive versioning.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

from config import MLB_STATS_BASE, TEAM_ABBRS
from db.database import get_connection, query


def _today_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _team_abbr(team_name: str | None) -> str | None:
    if not team_name:
        return None
    return TEAM_ABBRS.get(team_name, team_name)


def _fetch_schedule(date_str: str) -> list[dict[str, Any]]:
    resp = requests.get(
        f"{MLB_STATS_BASE}/schedule",
        params={"date": date_str, "sportId": 1},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    games: list[dict[str, Any]] = []
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            games.append(
                {
                    "game_id": int(game["gamePk"]),
                    "status": (game.get("status", {}).get("detailedState") or "").lower(),
                    "home_team": _team_abbr(game.get("teams", {}).get("home", {}).get("team", {}).get("name")),
                    "away_team": _team_abbr(game.get("teams", {}).get("away", {}).get("team", {}).get("name")),
                }
            )
    return games


def _safe_player_id(raw_value: Any) -> int | None:
    if raw_value is None:
        return None
    try:
        return int(str(raw_value).replace("ID", ""))
    except (TypeError, ValueError):
        return None


def _batting_order_to_int(raw_value: Any) -> int | None:
    if raw_value is None:
        return None
    try:
        value = int(str(raw_value))
    except (TypeError, ValueError):
        return None
    if value >= 100:
        return value // 100
    return value


def _extract_lineup_rows(team_payload: dict[str, Any], team_id_fallback: str | None) -> list[dict[str, Any]]:
    players = team_payload.get("players", {}) or {}
    batting_order = team_payload.get("battingOrder", []) or []
    team_obj = team_payload.get("team", {}) or {}
    team_id = _team_abbr(team_obj.get("name")) or team_obj.get("abbreviation") or team_id_fallback

    rows: list[dict[str, Any]] = []
    seen: set[int] = set()

    if batting_order:
        for idx, raw_pid in enumerate(batting_order, start=1):
            pid = _safe_player_id(raw_pid)
            if pid is None or pid in seen:
                continue
            seen.add(pid)
            player_key = f"ID{pid}"
            player = players.get(player_key, {}) or {}
            rows.append(
                {
                    "team_id": team_id,
                    "player_id": pid,
                    "batting_order": idx,
                    "position": (player.get("position", {}) or {}).get("abbreviation"),
                    "is_starter": 1,
                }
            )
        return rows

    # Fallback for pregame payloads where battingOrder list is absent:
    # infer order from players[*].battingOrder when available.
    provisional: list[dict[str, Any]] = []
    for player in players.values():
        person = player.get("person", {}) or {}
        pid = _safe_player_id(person.get("id"))
        if pid is None:
            continue
        order = _batting_order_to_int(player.get("battingOrder"))
        if order is None:
            continue
        if pid in seen:
            continue
        seen.add(pid)
        provisional.append(
            {
                "team_id": team_id,
                "player_id": pid,
                "batting_order": order,
                "position": (player.get("position", {}) or {}).get("abbreviation"),
                "is_starter": 1,
            }
        )

    provisional.sort(key=lambda r: (r["batting_order"], r["player_id"]))
    return provisional


def _lineup_signature(rows: list[dict[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    signature_rows = [
        (
            int(r["player_id"]),
            int(r["batting_order"]) if r.get("batting_order") is not None else None,
            r.get("position"),
            int(r.get("is_starter", 0)),
            int(r.get("confirmed", 0)),
        )
        for r in rows
    ]
    signature_rows.sort()
    return tuple(signature_rows)


def _get_active_snapshot(game_date: str, game_id: int, team_id: str) -> list[dict[str, Any]]:
    return query(
        """
        SELECT player_id, batting_order, position, is_starter, confirmed
        FROM lineups
        WHERE game_date = ?
          AND game_id = ?
          AND team_id = ?
          AND COALESCE(active_version, 1) = 1
        ORDER BY batting_order, player_id
        """,
        (game_date, game_id, team_id),
    )


def _deactivate_active_version(conn, game_date: str, game_id: int, team_id: str) -> None:
    conn.execute(
        """
        UPDATE lineups
        SET active_version = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE game_date = ?
          AND game_id = ?
          AND team_id = ?
          AND COALESCE(active_version, 1) = 1
        """,
        (game_date, game_id, team_id),
    )


def _insert_snapshot(conn, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    cols = [
        "game_date",
        "game_id",
        "team_id",
        "player_id",
        "batting_order",
        "position",
        "is_starter",
        "confirmed",
        "source",
        "fetched_at",
        "active_version",
    ]
    placeholders = ", ".join(["?"] * len(cols))
    sql = f"INSERT INTO lineups ({', '.join(cols)}) VALUES ({placeholders})"
    payload = [tuple(r.get(c) for c in cols) for r in rows]
    cursor = conn.executemany(sql, payload)
    return cursor.rowcount if cursor.rowcount is not None else len(rows)


def fetch_lineups_for_date(date_str: str | None = None) -> dict[str, Any]:
    """
    Fetch lineup snapshots and return changed game/team entries.
    """
    game_date = date_str or _today_str()
    fetched_at = datetime.utcnow().isoformat()
    print(f"\n🧾 Fetching lineups for {game_date}...")

    games = _fetch_schedule(game_date)
    if not games:
        print("  ⚠️ No games found for date")
        return {
            "game_date": game_date,
            "games_seen": 0,
            "rows_inserted": 0,
            "changed": [],
        }

    changed: list[dict[str, Any]] = []
    rows_inserted = 0
    snapshots_checked = 0

    conn = get_connection()
    try:
        for game in games:
            game_id = int(game["game_id"])
            status = game.get("status", "")
            try:
                resp = requests.get(f"{MLB_STATS_BASE}/game/{game_id}/boxscore", timeout=20)
                resp.raise_for_status()
                boxscore = resp.json()
            except Exception as exc:
                print(f"  ⚠️  Game {game_id}: failed to fetch boxscore ({exc})")
                continue

            for side, team_fallback in (("home", game.get("home_team")), ("away", game.get("away_team"))):
                team_payload = (boxscore.get("teams", {}) or {}).get(side, {}) or {}
                lineup_rows = _extract_lineup_rows(team_payload, team_fallback)
                if not lineup_rows:
                    continue

                snapshots_checked += 1
                # Treat lineup as confirmed once full batting order (>=9) is present.
                confirmed = 1 if len(lineup_rows) >= 9 else 0
                if "final" in status or "in progress" in status or "warmup" in status:
                    confirmed = 1

                team_id = lineup_rows[0]["team_id"]
                for row in lineup_rows:
                    row.update(
                        {
                            "game_date": game_date,
                            "game_id": game_id,
                            "confirmed": confirmed,
                            "source": "mlb_stats_api",
                            "fetched_at": fetched_at,
                            "active_version": 1,
                        }
                    )

                existing = _get_active_snapshot(game_date, game_id, team_id)
                if _lineup_signature(existing) == _lineup_signature(lineup_rows):
                    continue

                _deactivate_active_version(conn, game_date, game_id, team_id)
                rows_inserted += _insert_snapshot(conn, lineup_rows)
                changed.append(
                    {
                        "game_id": game_id,
                        "team_id": team_id,
                        "confirmed": bool(confirmed),
                        "players": len(lineup_rows),
                    }
                )

        conn.commit()
    finally:
        conn.close()

    print(
        f"  ✅ Lineup fetch complete: games={len(games)} "
        f"snapshots_checked={snapshots_checked} changed={len(changed)} rows_inserted={rows_inserted}"
    )
    return {
        "game_date": game_date,
        "games_seen": len(games),
        "snapshots_checked": snapshots_checked,
        "rows_inserted": rows_inserted,
        "changed": changed,
    }
