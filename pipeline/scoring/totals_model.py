"""
Full game totals model.
"""
from __future__ import annotations

import math
from typing import Any

from db.database import query
from .base_engine import (
    GameContext,
    assign_signal,
    build_reasons,
    build_risk_flags,
    get_market_odds_rows,
    probability_edge_pct,
    projection_edge_pct,
)


MARKET = "TOTAL"
BET_TYPE_DEFAULT = "TOTAL"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _team_features(game_date: str, team_id: str | None) -> dict[str, Any] | None:
    if not team_id:
        return None
    rows = query(
        """
        SELECT *
        FROM team_daily_features
        WHERE game_date = ? AND team_id = ?
        LIMIT 1
        """,
        (game_date, team_id),
    )
    return rows[0] if rows else None


def _pitcher_features(game_date: str, pitcher_id: int | None) -> dict[str, Any] | None:
    if pitcher_id is None:
        return None
    rows = query(
        """
        SELECT *
        FROM pitcher_daily_features
        WHERE game_date = ? AND pitcher_id = ?
        LIMIT 1
        """,
        (game_date, pitcher_id),
    )
    return rows[0] if rows else None


def _context(game_date: str, game_id: int) -> dict[str, Any] | None:
    rows = query(
        """
        SELECT *
        FROM game_context_features
        WHERE game_date = ? AND game_id = ?
        LIMIT 1
        """,
        (game_date, game_id),
    )
    return rows[0] if rows else None


def _starter_ra9(pitcher: dict[str, Any] | None) -> float:
    if not pitcher:
        return 4.4
    k = _to_float(pitcher.get("k_pct_30") or pitcher.get("k_pct_14")) or 22.0
    bb = _to_float(pitcher.get("bb_pct_30") or pitcher.get("bb_pct_14")) or 8.0
    hr9 = _to_float(pitcher.get("hr_per_9_30") or pitcher.get("hr_per_9_14")) or 1.1
    hard_hit = _to_float(pitcher.get("hard_hit_pct_allowed_30") or pitcher.get("hard_hit_pct_allowed_14")) or 35.0
    ra9 = 4.15 + ((hr9 - 1.1) * 1.05) + ((hard_hit - 35.0) * 0.03) + ((bb - 8.0) * 0.10) - ((k - 22.0) * 0.06)
    return max(2.2, min(7.2, ra9))


def _starter_innings(pitcher: dict[str, Any] | None) -> float:
    if not pitcher:
        return 5.2
    role = _to_float(pitcher.get("starter_role_confidence")) or 0.6
    pitches = _to_float(pitcher.get("pitches_avg_last_5")) or 90.0
    innings = 4.7 + ((role - 0.5) * 2.0) + ((pitches - 90.0) * 0.015)
    return max(3.8, min(7.0, innings))


def _team_offense_base(team: dict[str, Any] | None) -> float:
    if not team:
        return 4.4
    runs = _to_float(team.get("runs_per_game_30") or team.get("runs_per_game_14")) or 4.4
    iso = _to_float(team.get("offense_iso_30") or team.get("offense_iso_14")) or 0.160
    obp = _to_float(team.get("offense_obp_30") or team.get("offense_obp_14")) or 0.320
    return max(2.8, min(6.8, runs + ((iso - 0.160) * 8.0) + ((obp - 0.320) * 10.0)))


def _team_bullpen_ra9(team: dict[str, Any] | None) -> float:
    if not team:
        return 4.2
    era = _to_float(team.get("bullpen_era_proxy_14")) or 4.2
    whip = _to_float(team.get("bullpen_whip_proxy_14")) or 1.30
    hr9 = _to_float(team.get("bullpen_hr9_14")) or 1.1
    ra9 = era + ((whip - 1.30) * 0.8) + ((hr9 - 1.1) * 0.7)
    return max(2.6, min(6.5, ra9))


def _team_expected_runs(
    *,
    offense_team: dict[str, Any] | None,
    opposing_starter: dict[str, Any] | None,
    opposing_bullpen_team: dict[str, Any] | None,
    env_multiplier: float,
) -> float:
    offense_base = _team_offense_base(offense_team)
    starter_ra = _starter_ra9(opposing_starter)
    starter_ip = _starter_innings(opposing_starter)
    bullpen_ra = _team_bullpen_ra9(opposing_bullpen_team)

    runs_allowed_profile = (starter_ra * (starter_ip / 9.0)) + (bullpen_ra * ((9.0 - starter_ip) / 9.0))
    expected = (offense_base * 0.55) + (runs_allowed_profile * 0.45)
    expected *= env_multiplier
    return max(1.2, min(8.0, expected))


def score_game(game: GameContext, weather: dict | None, park_factor: float, season: int) -> list[dict]:
    del weather, season
    odds_rows = get_market_odds_rows(game_date=game.game_date, market=MARKET, game_id=game.game_id)
    if not odds_rows:
        return []

    context = _context(game.game_date, game.game_id)
    lineup_confirmed = bool((context or {}).get("lineups_confirmed_home") and (context or {}).get("lineups_confirmed_away"))
    weather_multiplier = _to_float((context or {}).get("weather_run_multiplier")) or 1.0
    ump_run_env = _to_float((context or {}).get("umpire_run_env")) or 1.0
    env = max(0.82, min(1.25, weather_multiplier * ump_run_env * park_factor))

    home_team = _team_features(game.game_date, game.home_team)
    away_team = _team_features(game.game_date, game.away_team)
    home_pitcher = _pitcher_features(game.game_date, game.home_pitcher_id)
    away_pitcher = _pitcher_features(game.game_date, game.away_pitcher_id)

    home_runs_exp = _team_expected_runs(
        offense_team=home_team,
        opposing_starter=away_pitcher,
        opposing_bullpen_team=away_team,
        env_multiplier=env,
    )
    away_runs_exp = _team_expected_runs(
        offense_team=away_team,
        opposing_starter=home_pitcher,
        opposing_bullpen_team=home_team,
        env_multiplier=env,
    )
    total_projection = max(3.5, min(16.0, home_runs_exp + away_runs_exp))

    factors = {
        "offense_pace_score": max(0.0, min(100.0, 50.0 + (((_team_offense_base(home_team) + _team_offense_base(away_team)) / 2.0 - 4.4) * 14.0))),
        "starter_run_prevention_score": max(0.0, min(100.0, 70.0 - (((_starter_ra9(home_pitcher) + _starter_ra9(away_pitcher)) / 2.0 - 4.2) * 12.0))),
        "bullpen_run_prevention_score": max(0.0, min(100.0, 70.0 - (((_team_bullpen_ra9(home_team) + _team_bullpen_ra9(away_team)) / 2.0 - 4.2) * 14.0))),
        "park_weather_score": max(0.0, min(100.0, 50.0 + ((env - 1.0) * 180.0))),
        "umpire_run_env_score": max(0.0, min(100.0, 50.0 + ((ump_run_env - 1.0) * 200.0))),
    }

    results: list[dict[str, Any]] = []
    for odds in odds_rows:
        side = (odds.get("side") or "").upper()
        if side not in {"OVER", "UNDER"}:
            continue
        line = _to_float(odds.get("line"))
        if line is None:
            continue
        prob_over = _sigmoid((total_projection - line) / 1.85)
        model_prob = prob_over if side == "OVER" else (1.0 - prob_over)
        implied_prob = _to_float(odds.get("implied_probability"))
        edge_prob = probability_edge_pct(model_prob, implied_prob)
        edge_proj = projection_edge_pct(total_projection, line)
        edge_pct = edge_prob if edge_prob is not None else edge_proj

        model_score = (
            (factors["offense_pace_score"] * 0.30)
            + (factors["starter_run_prevention_score"] * 0.23)
            + (factors["bullpen_run_prevention_score"] * 0.20)
            + (factors["park_weather_score"] * 0.17)
            + (factors["umpire_run_env_score"] * 0.10)
        )
        if side == "UNDER":
            # under likes stronger run prevention and lower environment.
            model_score = 100.0 - model_score
        if edge_pct is not None:
            model_score += max(-8.0, min(8.0, edge_pct * 0.35))
        model_score = max(0.0, min(100.0, model_score))

        risk_flags = build_risk_flags(
            missing_inputs=[],
            lineup_pending=not lineup_confirmed,
            weather_pending=context is None,
        )
        reasons = build_reasons(factors)

        results.append(
            {
                "market": MARKET,
                "entity_type": "game",
                "game_id": game.game_id,
                "event_id": odds.get("event_id"),
                "selection_key": odds.get("selection_key"),
                "side": side,
                "bet_type": odds.get("bet_type") or f"TOTAL_{side}",
                "line": line,
                "model_score": round(model_score, 2),
                "model_prob": round(model_prob, 4),
                "model_projection": round(total_projection, 3),
                "book_implied_prob": round(implied_prob, 4) if implied_prob is not None else None,
                "edge": round(edge_pct, 3) if edge_pct is not None else None,
                "signal": assign_signal(MARKET, model_score, edge_pct),
                "factors_json": factors,
                "reasons_json": reasons,
                "risk_flags_json": risk_flags,
                "lineup_confirmed": 1 if lineup_confirmed else 0,
                "weather_final": 1 if (context and context.get("weather_temp_f") is not None) else 0,
            }
        )
    return results
