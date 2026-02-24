"""
Umpire assignment fetcher (MLB Stats API).

Stores normalized plate-ump assignments for each game/date snapshot.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

from config import MLB_STATS_BASE
from db.database import get_connection, insert_many


def _today_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _normalize_umpire_name(name: str | None) -> str | None:
    if not name:
        return None
    collapsed = " ".join(name.strip().split())
    return collapsed if collapsed else None


def _ensure_umpire_assignments_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS umpire_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_date DATE NOT NULL,
                game_id INTEGER NOT NULL,
                umpire_name TEXT NOT NULL,
                fetched_at DATETIME NOT NULL,
                source TEXT NOT NULL DEFAULT 'mlb_stats_api',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(game_date, game_id, umpire_name, fetched_at)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_umpire_assignments_date ON umpire_assignments(game_date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_umpire_assignments_game ON umpire_assignments(game_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_umpire_assignments_fetched_at ON umpire_assignments(fetched_at)"
        )
        conn.commit()
    finally:
        conn.close()


def _extract_plate_umpire(game_payload: dict[str, Any]) -> str | None:
    officials = game_payload.get("officials", []) or []
    for official in officials:
        if (official.get("officialType") or "").lower() == "home plate":
            name = ((official.get("official") or {}).get("fullName")) or official.get("fullName")
            return _normalize_umpire_name(name)
    return None


def fetch_umpires_for_date(date_str: str | None = None) -> dict[str, Any]:
    """
    Fetch plate ump assignments for target date and store snapshots.
    """
    game_date = date_str or _today_str()
    fetched_at = datetime.utcnow().isoformat()
    _ensure_umpire_assignments_table()

    print(f"\n👨‍⚖️ Fetching umpire assignments for {game_date}...")
    try:
        resp = requests.get(
            f"{MLB_STATS_BASE}/schedule",
            params={"date": game_date, "sportId": 1, "hydrate": "officials"},
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        print(f"  ⚠️ Umpire schedule fetch failed: {exc}")
        return {
            "game_date": game_date,
            "games_seen": 0,
            "rows_inserted": 0,
            "updated_games": 0,
            "warnings": [f"schedule_fetch_failed: {exc}"],
        }

    rows: list[dict[str, Any]] = []
    games_seen = 0
    missing = 0
    for date_entry in payload.get("dates", []):
        for game in date_entry.get("games", []):
            games_seen += 1
            game_id = int(game["gamePk"])
            umpire_name = _extract_plate_umpire(game)
            if not umpire_name:
                missing += 1
                continue
            rows.append(
                {
                    "game_date": game_date,
                    "game_id": game_id,
                    "umpire_name": umpire_name,
                    "fetched_at": fetched_at,
                    "source": "mlb_stats_api",
                }
            )

    inserted = insert_many("umpire_assignments", rows) if rows else 0

    updated_games = 0
    if rows:
        conn = get_connection()
        try:
            for row in rows:
                cursor = conn.execute(
                    """
                    UPDATE games
                    SET umpire_name = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE game_id = ?
                      AND game_date = ?
                    """,
                    (row["umpire_name"], row["game_id"], game_date),
                )
                if cursor.rowcount and cursor.rowcount > 0:
                    updated_games += int(cursor.rowcount)
            conn.commit()
        finally:
            conn.close()

    print(
        f"  ✅ Umpire fetch complete: games={games_seen}, found={len(rows)}, "
        f"inserted={inserted}, updated_games={updated_games}, missing={missing}"
    )
    return {
        "game_date": game_date,
        "games_seen": games_seen,
        "rows_inserted": inserted,
        "updated_games": updated_games,
        "missing_assignments": missing,
    }
