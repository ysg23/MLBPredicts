"""
Pitcher daily feature snapshot builder.

Phase 2B goals:
- Build pitcher_daily_features for probable starters on one game_date
- No lookahead bias via stat_date < game_date
- Write partial rows when some source data is missing
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from db.database import query, upsert_many


MAX_BATCH_SIZE = 500


def _to_date(game_date: date | str) -> date:
    if isinstance(game_date, date):
        return game_date
    return datetime.strptime(game_date, "%Y-%m-%d").date()


def _chunked(rows: list[dict[str, Any]], size: int = MAX_BATCH_SIZE):
    for idx in range(0, len(rows), size):
        yield rows[idx : idx + size]


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _probable_starters(game_dt: date) -> dict[int, dict[str, Any]]:
    rows = query(
        """
        SELECT home_pitcher_id, away_pitcher_id, home_team, away_team
        FROM games
        WHERE game_date = ?
        """,
        (game_dt.strftime("%Y-%m-%d"),),
    )
    starters: dict[int, dict[str, Any]] = {}
    for row in rows:
        home_pitcher_id = row.get("home_pitcher_id")
        away_pitcher_id = row.get("away_pitcher_id")
        home_team = row.get("home_team")
        away_team = row.get("away_team")

        if home_pitcher_id:
            starters[int(home_pitcher_id)] = {"team_id": home_team, "opponent_team_id": away_team}
        if away_pitcher_id:
            starters[int(away_pitcher_id)] = {"team_id": away_team, "opponent_team_id": home_team}
    return starters


def _latest_pitcher_windows(
    pitcher_ids: list[int],
    game_dt: date,
) -> dict[int, dict[int, dict[str, Any]]]:
    if not pitcher_ids:
        return {}

    placeholders = ", ".join(["?"] * len(pitcher_ids))
    sql = f"""
        SELECT *
        FROM pitcher_stats
        WHERE stat_date < ?
          AND window_days IN (14, 30)
          AND player_id IN ({placeholders})
        ORDER BY player_id, window_days, stat_date DESC
    """
    params = [game_dt.strftime("%Y-%m-%d"), *pitcher_ids]
    rows = query(sql, tuple(params))

    latest: dict[int, dict[int, dict[str, Any]]] = {}
    for row in rows:
        pitcher_id = int(row["player_id"])
        window = int(row["window_days"])
        if pitcher_id not in latest:
            latest[pitcher_id] = {}
        if window not in latest[pitcher_id]:
            latest[pitcher_id][window] = row
    return latest


def _starter_role_confidence(row14: dict[str, Any], row30: dict[str, Any]) -> float:
    bf14 = _to_float(row14.get("batters_faced")) if row14 else None
    bf30 = _to_float(row30.get("batters_faced")) if row30 else None

    if bf14 is None and bf30 is None:
        return 0.2
    if bf30 is not None:
        if bf30 >= 80:
            return 0.9
        if bf30 >= 50:
            return 0.75
        if bf30 >= 20:
            return 0.55
        return 0.35
    if bf14 is not None:
        if bf14 >= 40:
            return 0.7
        if bf14 >= 20:
            return 0.5
    return 0.35


def _build_pitcher_row(
    game_dt: date,
    pitcher_id: int,
    team_context: dict[str, Any],
    window_rows: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    row14 = window_rows.get(14, {})
    row30 = window_rows.get(30, {})

    team_id = row30.get("team") or row14.get("team") or team_context.get("team_id")
    throws = row30.get("pitch_hand") or row14.get("pitch_hand")

    return {
        "game_date": game_dt.strftime("%Y-%m-%d"),
        "pitcher_id": pitcher_id,
        "team_id": team_id,
        "throws": throws,
        "batters_faced_14": _to_float(row14.get("batters_faced")),
        "batters_faced_30": _to_float(row30.get("batters_faced")),
        "k_pct_14": _to_float(row14.get("k_pct")),
        "k_pct_30": _to_float(row30.get("k_pct")),
        "bb_pct_14": _to_float(row14.get("bb_pct")),
        "bb_pct_30": _to_float(row30.get("bb_pct")),
        "hr_per_9_14": _to_float(row14.get("hr_per_9")),
        "hr_per_9_30": _to_float(row30.get("hr_per_9")),
        "hr_per_fb_14": _to_float(row14.get("hr_per_fb")),
        "hr_per_fb_30": _to_float(row30.get("hr_per_fb")),
        "hard_hit_pct_allowed_14": _to_float(row14.get("hard_hit_pct_against")),
        "hard_hit_pct_allowed_30": _to_float(row30.get("hard_hit_pct_against")),
        "barrel_pct_allowed_14": _to_float(row14.get("barrel_pct_against")),
        "barrel_pct_allowed_30": _to_float(row30.get("barrel_pct_against")),
        "avg_exit_velo_allowed_14": _to_float(row14.get("avg_exit_velo_against")),
        "avg_exit_velo_allowed_30": _to_float(row30.get("avg_exit_velo_against")),
        "fly_ball_pct_allowed_14": _to_float(row14.get("fly_ball_pct")),
        "fly_ball_pct_allowed_30": _to_float(row30.get("fly_ball_pct")),
        "whiff_pct_14": _to_float(row14.get("whiff_pct")),
        "whiff_pct_30": _to_float(row30.get("whiff_pct")),
        "chase_pct_14": _to_float(row14.get("chase_pct")),
        "chase_pct_30": _to_float(row30.get("chase_pct")),
        "avg_fastball_velo_14": _to_float(row14.get("avg_fastball_velo")),
        "avg_fastball_velo_30": _to_float(row30.get("avg_fastball_velo")),
        "fastball_velo_trend_14": _to_float(row14.get("fastball_velo_trend")),
        # Not consistently available from current upstream fetchers; leave null, do not invent data.
        "outs_recorded_avg_last_5": None,
        "pitches_avg_last_5": None,
        "starter_role_confidence": _starter_role_confidence(row14, row30),
        "split_k_pct_vs_lhh": _to_float(row30.get("k_pct_vs_lhb") or row14.get("k_pct_vs_lhb")),
        "split_k_pct_vs_rhh": _to_float(row30.get("k_pct_vs_rhb") or row14.get("k_pct_vs_rhb")),
        "split_hr_allowed_rate_vs_lhh": _to_float(
            row30.get("hr_per_9_vs_lhb") or row14.get("hr_per_9_vs_lhb")
        ),
        "split_hr_allowed_rate_vs_rhh": _to_float(
            row30.get("hr_per_9_vs_rhb") or row14.get("hr_per_9_vs_rhb")
        ),
    }


def build_pitcher_daily_features(game_date: date | str) -> dict[str, Any]:
    """
    Build pitcher_daily_features snapshot for probable starters on a date.
    """
    game_dt = _to_date(game_date)
    print(f"\n🔧 Building pitcher_daily_features for {game_dt} (as_of < {game_dt})")

    starters = _probable_starters(game_dt)
    if not starters:
        print("  ⚠️ No probable starters found in games table")
        return {
            "game_date": game_dt.strftime("%Y-%m-%d"),
            "rows_upserted": 0,
            "warnings": ["No probable starters found for date"],
        }

    pitcher_ids = sorted(starters.keys())
    latest_windows = _latest_pitcher_windows(pitcher_ids, game_dt=game_dt)

    rows: list[dict[str, Any]] = []
    missing_stats = 0
    partial_rows = 0
    for pitcher_id in pitcher_ids:
        window_rows = latest_windows.get(pitcher_id, {})
        if not window_rows:
            missing_stats += 1
            continue
        if 14 not in window_rows or 30 not in window_rows:
            partial_rows += 1
        rows.append(_build_pitcher_row(game_dt, pitcher_id, starters[pitcher_id], window_rows))

    if not rows:
        return {
            "game_date": game_dt.strftime("%Y-%m-%d"),
            "rows_upserted": 0,
            "warnings": ["No pitcher rows built due to missing historical pitcher_stats"],
        }

    upserted = 0
    for batch in _chunked(rows, size=MAX_BATCH_SIZE):
        upserted += upsert_many(
            "pitcher_daily_features",
            batch,
            conflict_cols=["game_date", "pitcher_id"],
        )

    print(
        "  ✅ Pitcher features built: "
        f"generated={len(rows)}, upserted={upserted}, partial_rows={partial_rows}, missing_stats={missing_stats}"
    )

    warnings: list[str] = []
    if missing_stats:
        warnings.append(f"{missing_stats} probable starter(s) had no historical pitcher_stats before date")
    if partial_rows:
        warnings.append(f"{partial_rows} row(s) missing 14d or 30d window and were stored as partial")

    return {
        "game_date": game_dt.strftime("%Y-%m-%d"),
        "rows_generated": len(rows),
        "rows_upserted": upserted,
        "partial_rows": partial_rows,
        "missing_stats": missing_stats,
        "warnings": warnings,
    }
