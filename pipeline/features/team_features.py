"""
Team daily feature snapshot builder.

Phase 2C goals:
- Build team_daily_features for all teams on a target game_date
- No lookahead bias (source stat_date/game_date strictly < target date)
- Compute offense 14d/30d windows + bullpen 14d proxies from available data
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


def _teams_on_date(game_dt: date) -> dict[str, str | None]:
    rows = query(
        """
        SELECT home_team, away_team
        FROM mlb_games
        WHERE game_date = ?
        """,
        (game_dt.strftime("%Y-%m-%d"),),
    )
    mapping: dict[str, str | None] = {}
    for row in rows:
        home = row.get("home_team")
        away = row.get("away_team")
        if home:
            mapping[str(home)] = str(away) if away else None
        if away:
            mapping[str(away)] = str(home) if home else None
    return mapping


def _latest_batter_rows(team_id: str, game_dt: date, window: int) -> list[dict[str, Any]]:
    rows = query(
        """
        SELECT *
        FROM mlb_batter_stats
        WHERE team = ?
          AND window_days = ?
          AND stat_date < ?
        ORDER BY player_id, stat_date DESC
        """,
        (team_id, window, game_dt.strftime("%Y-%m-%d")),
    )
    latest_by_player: dict[int, dict[str, Any]] = {}
    for row in rows:
        player_id = int(row["player_id"])
        if player_id not in latest_by_player:
            latest_by_player[player_id] = row
    return list(latest_by_player.values())


def _runs_per_game(team_id: str, game_dt: date, window_days: int) -> float | None:
    start = (game_dt - timedelta(days=window_days)).strftime("%Y-%m-%d")
    rows = query(
        """
        SELECT home_team, away_team, home_score, away_score
        FROM mlb_games
        WHERE game_date >= ?
          AND game_date < ?
          AND status = 'final'
          AND (home_team = ? OR away_team = ?)
        """,
        (start, game_dt.strftime("%Y-%m-%d"), team_id, team_id),
    )
    if not rows:
        return None

    runs: list[float] = []
    for row in rows:
        if row.get("home_team") == team_id and row.get("home_score") is not None:
            runs.append(float(row["home_score"]))
        elif row.get("away_team") == team_id and row.get("away_score") is not None:
            runs.append(float(row["away_score"]))
    if not runs:
        return None
    return sum(runs) / len(runs)


def _aggregate_offense(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    if not rows:
        return {
            "offense_k_pct": None,
            "offense_bb_pct": None,
            "offense_iso": None,
            "offense_ba": None,
            "offense_obp": None,
            "offense_slg": None,
            "offense_hit_rate": None,
            "offense_tb_per_pa": None,
            "hr_rate": None,
        }

    total_pa = 0.0
    total_ab = 0.0
    total_hits = 0.0
    total_tb = 0.0
    total_walks = 0.0
    total_hr = 0.0

    weighted_k = 0.0
    weighted_bb = 0.0
    weighted_iso = 0.0
    weighted_slg = 0.0
    weight_pa_sum = 0.0
    weight_ab_sum = 0.0

    for row in rows:
        pa = _to_float(row.get("pa")) or 0.0
        ab = _to_float(row.get("ab")) or 0.0
        slg = _to_float(row.get("slg"))
        iso = _to_float(row.get("iso_power"))
        k_pct = _to_float(row.get("k_pct"))
        bb_pct = _to_float(row.get("bb_pct"))
        hrs = _to_float(row.get("hrs")) or 0.0

        # Approximate AVG from SLG - ISO.
        ba = max(0.0, (slg - iso)) if slg is not None and iso is not None else None
        hits = (ba * ab) if ba is not None else 0.0
        tb = (slg * ab) if slg is not None else 0.0

        bb_rate = None
        if bb_pct is not None:
            bb_rate = bb_pct / 100.0 if bb_pct > 1 else bb_pct
        walks = (bb_rate * pa) if bb_rate is not None else 0.0

        total_pa += pa
        total_ab += ab
        total_hits += hits
        total_tb += tb
        total_walks += walks
        total_hr += hrs

        if pa > 0:
            weight_pa_sum += pa
            if k_pct is not None:
                weighted_k += k_pct * pa
            if bb_pct is not None:
                weighted_bb += bb_pct * pa
            if iso is not None:
                weighted_iso += iso * pa
        if ab > 0 and slg is not None:
            weight_ab_sum += ab
            weighted_slg += slg * ab

    offense_ba = (total_hits / total_ab) if total_ab > 0 else None
    offense_obp = ((total_hits + total_walks) / total_pa) if total_pa > 0 else None

    return {
        "offense_k_pct": (weighted_k / weight_pa_sum) if weight_pa_sum > 0 else None,
        "offense_bb_pct": (weighted_bb / weight_pa_sum) if weight_pa_sum > 0 else None,
        "offense_iso": (weighted_iso / weight_pa_sum) if weight_pa_sum > 0 else None,
        "offense_ba": offense_ba,
        "offense_obp": offense_obp,
        "offense_slg": (weighted_slg / weight_ab_sum) if weight_ab_sum > 0 else None,
        "offense_hit_rate": offense_ba,
        "offense_tb_per_pa": (total_tb / total_pa) if total_pa > 0 else None,
        "hr_rate": (total_hr / total_pa) if total_pa > 0 else None,
    }


def _latest_pitcher_rows(team_id: str, game_dt: date, window: int = 14) -> list[dict[str, Any]]:
    rows = query(
        """
        SELECT *
        FROM mlb_pitcher_stats
        WHERE team = ?
          AND window_days = ?
          AND stat_date < ?
        ORDER BY player_id, stat_date DESC
        """,
        (team_id, window, game_dt.strftime("%Y-%m-%d")),
    )
    latest_by_pitcher: dict[int, dict[str, Any]] = {}
    for row in rows:
        player_id = int(row["player_id"])
        if player_id not in latest_by_pitcher:
            latest_by_pitcher[player_id] = row
    return list(latest_by_pitcher.values())


def _aggregate_bullpen(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    if not rows:
        return {
            "bullpen_era_proxy_14": None,
            "bullpen_whip_proxy_14": None,
            "bullpen_k_pct_14": None,
            "bullpen_hr9_14": None,
        }

    weighted_hr9 = 0.0
    weighted_k = 0.0
    weighted_bb = 0.0
    weight = 0.0

    for row in rows:
        bf = _to_float(row.get("batters_faced")) or 0.0
        hr9 = _to_float(row.get("hr_per_9"))
        k_pct = _to_float(row.get("k_pct"))
        bb_pct = _to_float(row.get("bb_pct"))
        if bf <= 0:
            bf = 1.0
        weight += bf
        if hr9 is not None:
            weighted_hr9 += hr9 * bf
        if k_pct is not None:
            weighted_k += k_pct * bf
        if bb_pct is not None:
            weighted_bb += bb_pct * bf

    if weight <= 0:
        return {
            "bullpen_era_proxy_14": None,
            "bullpen_whip_proxy_14": None,
            "bullpen_k_pct_14": None,
            "bullpen_hr9_14": None,
        }

    hr9_avg = weighted_hr9 / weight if weighted_hr9 else None
    k_avg = weighted_k / weight if weighted_k else None
    bb_avg = weighted_bb / weight if weighted_bb else None
    bb_rate = None
    if bb_avg is not None:
        bb_rate = bb_avg / 100.0 if bb_avg > 1 else bb_avg

    return {
        # Proxy derived from available HR-suppression data.
        "bullpen_era_proxy_14": hr9_avg,
        "bullpen_whip_proxy_14": (1.0 + (bb_rate * 1.5)) if bb_rate is not None else None,
        "bullpen_k_pct_14": k_avg,
        "bullpen_hr9_14": hr9_avg,
    }


def _build_team_row(game_dt: date, team_id: str, opponent_team_id: str | None) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []

    bat14 = _latest_batter_rows(team_id, game_dt=game_dt, window=14)
    bat30 = _latest_batter_rows(team_id, game_dt=game_dt, window=30)
    if not bat14:
        warnings.append("no_14d_batter_stats")
    if not bat30:
        warnings.append("no_30d_batter_stats")

    off14 = _aggregate_offense(bat14)
    off30 = _aggregate_offense(bat30)
    bp14_rows = _latest_pitcher_rows(team_id, game_dt=game_dt, window=14)
    if not bp14_rows:
        warnings.append("no_14d_pitcher_stats_for_bullpen_proxy")
    bp14 = _aggregate_bullpen(bp14_rows)

    row = {
        "game_date": game_dt.strftime("%Y-%m-%d"),
        "team_id": team_id,
        "opponent_team_id": opponent_team_id,
        "offense_k_pct_14": off14["offense_k_pct"],
        "offense_k_pct_30": off30["offense_k_pct"],
        "offense_bb_pct_14": off14["offense_bb_pct"],
        "offense_bb_pct_30": off30["offense_bb_pct"],
        "offense_iso_14": off14["offense_iso"],
        "offense_iso_30": off30["offense_iso"],
        "offense_ba_14": off14["offense_ba"],
        "offense_ba_30": off30["offense_ba"],
        "offense_obp_14": off14["offense_obp"],
        "offense_obp_30": off30["offense_obp"],
        "offense_slg_14": off14["offense_slg"],
        "offense_slg_30": off30["offense_slg"],
        "offense_hit_rate_14": off14["offense_hit_rate"],
        "offense_hit_rate_30": off30["offense_hit_rate"],
        "offense_tb_per_pa_14": off14["offense_tb_per_pa"],
        "offense_tb_per_pa_30": off30["offense_tb_per_pa"],
        "runs_per_game_14": _runs_per_game(team_id, game_dt=game_dt, window_days=14),
        "runs_per_game_30": _runs_per_game(team_id, game_dt=game_dt, window_days=30),
        "hr_rate_14": off14["hr_rate"],
        "hr_rate_30": off30["hr_rate"],
        "bullpen_era_proxy_14": bp14["bullpen_era_proxy_14"],
        "bullpen_whip_proxy_14": bp14["bullpen_whip_proxy_14"],
        "bullpen_k_pct_14": bp14["bullpen_k_pct_14"],
        "bullpen_hr9_14": bp14["bullpen_hr9_14"],
    }
    return row, warnings


def build_team_daily_features(game_date: date | str) -> dict[str, Any]:
    """
    Build team_daily_features snapshot for all teams on a date.
    """
    game_dt = _to_date(game_date)
    print(f"\n🔧 Building team_daily_features for {game_dt} (as_of < {game_dt})")

    teams = _teams_on_date(game_dt)
    if not teams:
        print("  ⚠️ No scheduled teams found for date")
        return {
            "game_date": game_dt.strftime("%Y-%m-%d"),
            "rows_upserted": 0,
            "warnings": ["No games/teams found for date"],
        }

    rows: list[dict[str, Any]] = []
    missing_data_warnings: list[str] = []
    for team_id, opp_id in sorted(teams.items()):
        row, warnings = _build_team_row(game_dt, team_id, opponent_team_id=opp_id)
        rows.append(row)
        if warnings:
            missing_data_warnings.append(f"{team_id}: {','.join(warnings)}")

    upserted = 0
    for batch in _chunked(rows, size=MAX_BATCH_SIZE):
        upserted += upsert_many(
            "mlb_team_daily_features",
            batch,
            conflict_cols=["game_date", "team_id"],
        )

    print(f"  ✅ Team features built: generated={len(rows)}, upserted={upserted}")
    if missing_data_warnings:
        print(f"  ⚠️ Missing-data warnings: {len(missing_data_warnings)} team(s)")

    return {
        "game_date": game_dt.strftime("%Y-%m-%d"),
        "rows_generated": len(rows),
        "rows_upserted": upserted,
        "missing_data_warnings": missing_data_warnings,
    }
