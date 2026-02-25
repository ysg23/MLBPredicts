"""
Batter daily feature snapshot builder.

Phase 2A goals:
- Build batter_daily_features for one game_date
- No lookahead bias: only use source rows with stat_date < game_date
- Incremental by date
- Fallback player pool: lineups -> odds -> recent team stats
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from db.database import query, upsert_many


MAX_BATCH_SIZE = 500


def _to_date(game_date: date | str) -> date:
    if isinstance(game_date, date):
        return game_date
    return datetime.strptime(game_date, "%Y-%m-%d").date()


def _safe_div(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return float(numerator) / float(denominator)


def _chunked(rows: list[dict[str, Any]], size: int = MAX_BATCH_SIZE):
    for idx in range(0, len(rows), size):
        yield rows[idx : idx + size]


def _as_of_bounds(game_dt: date, seasons_back: int) -> tuple[str, str]:
    upper = game_dt.strftime("%Y-%m-%d")
    lower = (game_dt - timedelta(days=seasons_back * 366)).strftime("%Y-%m-%d")
    return lower, upper


def _query_distinct_lineup_batters(game_dt: date) -> dict[int, str | None]:
    rows = query(
        """
        SELECT DISTINCT player_id, team_id
        FROM lineups
        WHERE game_date = ?
          AND player_id IS NOT NULL
          AND COALESCE(active_version, 1) = 1
        """,
        (game_dt.strftime("%Y-%m-%d"),),
    )
    return {int(r["player_id"]): r.get("team_id") for r in rows}


def _query_distinct_odds_batters(game_dt: date) -> dict[int, str | None]:
    rows = query(
        """
        SELECT DISTINCT player_id, team_id
        FROM market_odds
        WHERE game_date = ?
          AND entity_type = 'batter'
          AND player_id IS NOT NULL
        """,
        (game_dt.strftime("%Y-%m-%d"),),
    )
    return {int(r["player_id"]): r.get("team_id") for r in rows}


def _query_game_teams(game_dt: date) -> list[str]:
    rows = query(
        """
        SELECT home_team AS team_id, away_team AS opp_id
        FROM games
        WHERE game_date = ?
        """,
        (game_dt.strftime("%Y-%m-%d"),),
    )
    teams: set[str] = set()
    for row in rows:
        if row.get("team_id"):
            teams.add(str(row["team_id"]))
        if row.get("opp_id"):
            teams.add(str(row["opp_id"]))
    return sorted(teams)


def _query_recent_team_batters(game_dt: date, seasons_back: int) -> dict[int, str | None]:
    teams = _query_game_teams(game_dt)
    if not teams:
        return {}

    lower, upper = _as_of_bounds(game_dt, seasons_back)
    placeholders = ", ".join(["?"] * len(teams))
    sql = f"""
        SELECT DISTINCT player_id, team
        FROM batter_stats
        WHERE stat_date >= ?
          AND stat_date < ?
          AND team IN ({placeholders})
          AND player_id IS NOT NULL
    """
    params = [lower, upper, *teams]
    rows = query(sql, tuple(params))
    return {int(r["player_id"]): r.get("team") for r in rows}


def _query_recent_lineup_slot(player_id: int, game_dt: date) -> int | None:
    """Get the player's most common batting order position from recent lineups."""
    rows = query(
        """
        SELECT batting_order, COUNT(*) as cnt
        FROM lineups
        WHERE player_id = ?
          AND game_date < ?
          AND batting_order IS NOT NULL
          AND COALESCE(active_version, 1) = 1
        GROUP BY batting_order
        ORDER BY cnt DESC
        LIMIT 1
        """,
        (player_id, game_dt.strftime("%Y-%m-%d")),
    )
    if rows and rows[0].get("batting_order") is not None:
        return int(rows[0]["batting_order"])
    return None


def _relevant_batter_pool(game_dt: date, seasons_back: int) -> tuple[dict[int, str | None], dict[str, int]]:
    lineup = _query_distinct_lineup_batters(game_dt)
    odds = _query_distinct_odds_batters(game_dt)
    recent = _query_recent_team_batters(game_dt, seasons_back=seasons_back)

    merged: dict[int, str | None] = {}
    # Source priority: lineups, then odds, then recent team pool.
    for source in (recent, odds, lineup):
        for player_id, team_id in source.items():
            if player_id not in merged:
                merged[player_id] = team_id
            elif merged[player_id] is None and team_id is not None:
                merged[player_id] = team_id

    counts = {
        "lineup_players": len(lineup),
        "odds_players": len(odds),
        "recent_team_players": len(recent),
        "merged_players": len(merged),
    }
    return merged, counts


def _query_latest_windows(
    player_ids: list[int],
    game_dt: date,
    seasons_back: int,
) -> dict[int, dict[int, dict[str, Any]]]:
    if not player_ids:
        return {}

    lower, upper = _as_of_bounds(game_dt, seasons_back)
    placeholders = ", ".join(["?"] * len(player_ids))
    sql = f"""
        SELECT *
        FROM batter_stats
        WHERE stat_date >= ?
          AND stat_date < ?
          AND window_days IN (7, 14, 30)
          AND player_id IN ({placeholders})
        ORDER BY player_id, window_days, stat_date DESC
    """
    params = [lower, upper, *player_ids]
    rows = query(sql, tuple(params))

    latest: dict[int, dict[int, dict[str, Any]]] = {}
    for row in rows:
        pid = int(row["player_id"])
        window = int(row["window_days"])
        if pid not in latest:
            latest[pid] = {}
        if window not in latest[pid]:
            latest[pid][window] = row
    return latest


def _derive_ba(slg: float | None, iso: float | None) -> float | None:
    if slg is None or iso is None:
        return None
    return max(0.0, float(slg) - float(iso))


def _window_value(window_rows: dict[int, dict[str, Any]], window: int, key: str):
    row = window_rows.get(window)
    if not row:
        return None
    return row.get(key)


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_row(
    game_dt: date,
    player_id: int,
    team_hint: str | None,
    window_rows: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    row7 = window_rows.get(7, {})
    row14 = window_rows.get(14, {})
    row30 = window_rows.get(30, {})

    iso7 = _to_float_or_none(_window_value(window_rows, 7, "iso_power"))
    iso14 = _to_float_or_none(_window_value(window_rows, 14, "iso_power"))
    iso30 = _to_float_or_none(_window_value(window_rows, 30, "iso_power"))
    slg7 = _to_float_or_none(_window_value(window_rows, 7, "slg"))
    slg14 = _to_float_or_none(_window_value(window_rows, 14, "slg"))
    slg30 = _to_float_or_none(_window_value(window_rows, 30, "slg"))
    ba7 = _derive_ba(slg7, iso7)
    ba14 = _derive_ba(slg14, iso14)
    ba30 = _derive_ba(slg30, iso30)

    pa7 = _to_float_or_none(_window_value(window_rows, 7, "pa"))
    pa14 = _to_float_or_none(_window_value(window_rows, 14, "pa"))
    pa30 = _to_float_or_none(_window_value(window_rows, 30, "pa"))
    ab7 = _to_float_or_none(_window_value(window_rows, 7, "ab"))
    ab14 = _to_float_or_none(_window_value(window_rows, 14, "ab"))
    ab30 = _to_float_or_none(_window_value(window_rows, 30, "ab"))
    hr7 = _to_float_or_none(_window_value(window_rows, 7, "hrs"))
    hr14 = _to_float_or_none(_window_value(window_rows, 14, "hrs"))
    hr30 = _to_float_or_none(_window_value(window_rows, 30, "hrs"))

    tb_per_pa_7 = _safe_div(_safe_div(slg7 * ab7 if slg7 is not None and ab7 is not None else None, 1), pa7)
    tb_per_pa_14 = _safe_div(_safe_div(slg14 * ab14 if slg14 is not None and ab14 is not None else None, 1), pa14)
    tb_per_pa_30 = _safe_div(_safe_div(slg30 * ab30 if slg30 is not None and ab30 is not None else None, 1), pa30)

    hit_rate_7 = ba7
    hit_rate_14 = ba14
    hit_rate_30 = ba30

    row_team = row30.get("team") or row14.get("team") or row7.get("team") or team_hint
    row_bats = row30.get("bat_hand") or row14.get("bat_hand") or row7.get("bat_hand")

    iso_vs_lhp = _to_float_or_none(row30.get("iso_vs_lhp") or row14.get("iso_vs_lhp") or row7.get("iso_vs_lhp"))
    iso_vs_rhp = _to_float_or_none(row30.get("iso_vs_rhp") or row14.get("iso_vs_rhp") or row7.get("iso_vs_rhp"))

    # Fallback behavior for unavailable split metrics: use overall rates.
    hit_vs_lhp = hit_rate_30
    hit_vs_rhp = hit_rate_30
    k_vs_lhp = _to_float_or_none(row30.get("k_pct")) or _to_float_or_none(row14.get("k_pct"))
    k_vs_rhp = _to_float_or_none(row30.get("k_pct")) or _to_float_or_none(row14.get("k_pct"))

    return {
        "game_date": game_dt.strftime("%Y-%m-%d"),
        "player_id": player_id,
        "team_id": row_team,
        "bats": row_bats,
        "pa_7": _to_float_or_none(row7.get("pa")),
        "pa_14": _to_float_or_none(row14.get("pa")),
        "pa_30": _to_float_or_none(row30.get("pa")),
        "k_pct_7": _to_float_or_none(row7.get("k_pct")),
        "k_pct_14": _to_float_or_none(row14.get("k_pct")),
        "k_pct_30": _to_float_or_none(row30.get("k_pct")),
        "bb_pct_7": _to_float_or_none(row7.get("bb_pct")),
        "bb_pct_14": _to_float_or_none(row14.get("bb_pct")),
        "bb_pct_30": _to_float_or_none(row30.get("bb_pct")),
        "barrel_pct_7": _to_float_or_none(row7.get("barrel_pct")),
        "barrel_pct_14": _to_float_or_none(row14.get("barrel_pct")),
        "barrel_pct_30": _to_float_or_none(row30.get("barrel_pct")),
        "hard_hit_pct_7": _to_float_or_none(row7.get("hard_hit_pct")),
        "hard_hit_pct_14": _to_float_or_none(row14.get("hard_hit_pct")),
        "hard_hit_pct_30": _to_float_or_none(row30.get("hard_hit_pct")),
        "avg_exit_velo_7": _to_float_or_none(row7.get("avg_exit_velo")),
        "avg_exit_velo_14": _to_float_or_none(row14.get("avg_exit_velo")),
        "avg_exit_velo_30": _to_float_or_none(row30.get("avg_exit_velo")),
        "fly_ball_pct_7": _to_float_or_none(row7.get("fly_ball_pct")),
        "fly_ball_pct_14": _to_float_or_none(row14.get("fly_ball_pct")),
        "fly_ball_pct_30": _to_float_or_none(row30.get("fly_ball_pct")),
        "line_drive_pct_7": None,
        "line_drive_pct_14": None,
        "line_drive_pct_30": None,
        "gb_pct_7": None,
        "gb_pct_14": None,
        "gb_pct_30": None,
        "pull_pct_7": _to_float_or_none(row7.get("pull_pct")),
        "pull_pct_14": _to_float_or_none(row14.get("pull_pct")),
        "pull_pct_30": _to_float_or_none(row30.get("pull_pct")),
        "sweet_spot_pct_7": _to_float_or_none(row7.get("sweet_spot_pct")),
        "sweet_spot_pct_14": _to_float_or_none(row14.get("sweet_spot_pct")),
        "sweet_spot_pct_30": _to_float_or_none(row30.get("sweet_spot_pct")),
        "avg_launch_angle_7": _to_float_or_none(row7.get("avg_launch_angle")),
        "avg_launch_angle_14": _to_float_or_none(row14.get("avg_launch_angle")),
        "avg_launch_angle_30": _to_float_or_none(row30.get("avg_launch_angle")),
        "iso_7": iso7,
        "iso_14": iso14,
        "iso_30": iso30,
        "slg_7": slg7,
        "slg_14": slg14,
        "slg_30": slg30,
        "ba_7": ba7,
        "ba_14": ba14,
        "ba_30": ba30,
        "hit_rate_7": hit_rate_7,
        "hit_rate_14": hit_rate_14,
        "hit_rate_30": hit_rate_30,
        "tb_per_pa_7": tb_per_pa_7,
        "tb_per_pa_14": tb_per_pa_14,
        "tb_per_pa_30": tb_per_pa_30,
        "hr_rate_7": _safe_div(hr7, pa7),
        "hr_rate_14": _safe_div(hr14, pa14),
        "hr_rate_30": _safe_div(hr30, pa30),
        "singles_rate_14": None,
        "singles_rate_30": None,
        "doubles_rate_14": None,
        "doubles_rate_30": None,
        "triples_rate_14": None,
        "triples_rate_30": None,
        "rbi_rate_14": None,
        "rbi_rate_30": None,
        "runs_rate_14": None,
        "runs_rate_30": None,
        "walk_rate_14": _safe_div(_to_float_or_none(row14.get("bb_pct")), 100.0),
        "walk_rate_30": _safe_div(_to_float_or_none(row30.get("bb_pct")), 100.0),
        "iso_vs_lhp": iso_vs_lhp if iso_vs_lhp is not None else iso30,
        "iso_vs_rhp": iso_vs_rhp if iso_vs_rhp is not None else iso30,
        "hit_rate_vs_lhp": hit_vs_lhp,
        "hit_rate_vs_rhp": hit_vs_rhp,
        "k_pct_vs_lhp": k_vs_lhp,
        "k_pct_vs_rhp": k_vs_rhp,
        "hot_cold_delta_iso": (iso7 - iso30) if iso7 is not None and iso30 is not None else None,
        "hot_cold_delta_hit_rate": (
            hit_rate_7 - hit_rate_30
            if hit_rate_7 is not None and hit_rate_30 is not None
            else None
        ),
        "recent_lineup_slot": _query_recent_lineup_slot(player_id, game_dt),
    }


def build_batter_daily_features(game_date: date | str, seasons_back: int = 3) -> dict[str, Any]:
    """
    Build batter_daily_features snapshot for one game date.
    """
    game_dt = _to_date(game_date)
    print(f"\n🔧 Building batter_daily_features for {game_dt} (as_of < {game_dt})")

    player_pool, source_counts = _relevant_batter_pool(game_dt, seasons_back=seasons_back)
    if not player_pool:
        print("  ⚠️ No relevant batters found from lineups/odds/recent teams")
        return {
            "game_date": game_dt.strftime("%Y-%m-%d"),
            "rows_upserted": 0,
            "pool_counts": source_counts,
            "warnings": ["No relevant batter pool for date"],
        }

    player_ids = sorted(player_pool.keys())
    print(
        "  📚 Batter pool counts: "
        f"lineups={source_counts['lineup_players']}, "
        f"odds={source_counts['odds_players']}, "
        f"recent={source_counts['recent_team_players']}, "
        f"merged={source_counts['merged_players']}"
    )

    latest_windows = _query_latest_windows(player_ids, game_dt=game_dt, seasons_back=seasons_back)
    rows: list[dict[str, Any]] = []
    missing_window_rows = 0

    for player_id in player_ids:
        window_rows = latest_windows.get(player_id, {})
        if not window_rows:
            missing_window_rows += 1
            continue
        rows.append(_build_row(game_dt, player_id, player_pool.get(player_id), window_rows))

    if not rows:
        return {
            "game_date": game_dt.strftime("%Y-%m-%d"),
            "rows_upserted": 0,
            "pool_counts": source_counts,
            "warnings": ["No batter feature rows generated from available historical data"],
        }

    upserted = 0
    for batch in _chunked(rows, size=MAX_BATCH_SIZE):
        upserted += upsert_many(
            "batter_daily_features",
            batch,
            conflict_cols=["game_date", "player_id"],
        )

    print(
        "  ✅ Batter features built: "
        f"generated={len(rows)}, upserted={upserted}, missing_source_players={missing_window_rows}"
    )

    warnings: list[str] = []
    if missing_window_rows:
        warnings.append(f"{missing_window_rows} player(s) had no prior batter_stats rows before date")

    return {
        "game_date": game_dt.strftime("%Y-%m-%d"),
        "rows_generated": len(rows),
        "rows_upserted": upserted,
        "missing_source_players": missing_window_rows,
        "pool_counts": source_counts,
        "warnings": warnings,
    }
