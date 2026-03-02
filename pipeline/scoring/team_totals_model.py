"""
Team totals model.
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


MARKET = "TEAM_TOTAL"
BET_TYPE_DEFAULT = "TEAM_TOTAL"


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
        FROM mlb_team_daily_features
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
        FROM mlb_pitcher_daily_features
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
        FROM mlb_game_context_features
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
    era_proxy = _to_float(team.get("bullpen_era_proxy_14")) or 4.2
    high_lev_era = _to_float(team.get("bullpen_high_lev_era_14"))
    era = (0.6 * high_lev_era + 0.4 * era_proxy) if high_lev_era is not None else era_proxy
    whip = _to_float(team.get("bullpen_whip_proxy_14")) or 1.30
    hr9 = _to_float(team.get("bullpen_hr9_14")) or 1.1
    ra9 = era + ((whip - 1.30) * 0.8) + ((hr9 - 1.1) * 0.7)
    return max(2.6, min(6.5, ra9))


def _infer_target_team(game: GameContext, odds_row: dict[str, Any]) -> tuple[str | None, str | None]:
    team_id = odds_row.get("team_id")
    if team_id in {game.home_team, game.away_team}:
        opponent = game.away_team if team_id == game.home_team else game.home_team
        return team_id, opponent
    side = (odds_row.get("selection_key") or "").upper()
    if "|HOME" in side:
        return game.home_team, game.away_team
    if "|AWAY" in side:
        return game.away_team, game.home_team
    return None, None


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
    expected = ((offense_base * 0.56) + (runs_allowed_profile * 0.44)) * env_multiplier
    return max(1.1, min(9.0, expected))


def score_game(game: GameContext, weather: dict | None, park_factor: float, season: int) -> list[dict]:
    del weather, season

    context = _context(game.game_date, game.game_id)
    lineup_confirmed = bool((context or {}).get("lineups_confirmed_home") and (context or {}).get("lineups_confirmed_away"))
    weather_multiplier = _to_float((context or {}).get("weather_run_multiplier")) or 1.0
    ump_run_env = _to_float((context or {}).get("umpire_run_env")) or 1.0
    env = max(0.82, min(1.25, weather_multiplier * ump_run_env * park_factor))

    home_team = _team_features(game.game_date, game.home_team)
    away_team = _team_features(game.game_date, game.away_team)
    home_pitcher = _pitcher_features(game.game_date, game.home_pitcher_id)
    away_pitcher = _pitcher_features(game.game_date, game.away_pitcher_id)

    team_lookup = {
        game.home_team: {"team": home_team, "starter": home_pitcher},
        game.away_team: {"team": away_team, "starter": away_pitcher},
    }

    # Build odds lookup by (team_id, side) for optional enrichment
    odds_rows = get_market_odds_rows(game_date=game.game_date, market=MARKET, game_id=game.game_id)
    odds_map: dict[tuple[str, str], dict[str, Any]] = {}
    for o in odds_rows:
        tid, oid = _infer_target_team(game, o)
        s = (o.get("side") or "").upper()
        if tid and s in {"OVER", "UNDER"}:
            odds_map[(tid, s)] = o

    # Score both teams x both sides from feature tables
    results: list[dict[str, Any]] = []
    for target_team_id, opp_team_id in [
        (game.home_team, game.away_team),
        (game.away_team, game.home_team),
    ]:
        target_team = team_lookup[target_team_id]["team"]
        opp_team = team_lookup[opp_team_id]["team"]
        opp_starter = team_lookup[opp_team_id]["starter"]

        projection = _team_expected_runs(
            offense_team=target_team,
            opposing_starter=opp_starter,
            opposing_bullpen_team=opp_team,
            env_multiplier=env,
        )

        factors = {
            "offense_strength_score": max(0.0, min(100.0, 50.0 + ((_team_offense_base(target_team) - 4.4) * 16.0))),
            "opponent_starter_suppress_score": max(0.0, min(100.0, 70.0 - ((_starter_ra9(opp_starter) - 4.2) * 12.0))),
            "opponent_bullpen_suppress_score": max(0.0, min(100.0, 70.0 - ((_team_bullpen_ra9(opp_team) - 4.2) * 14.0))),
            "park_weather_score": max(0.0, min(100.0, 50.0 + ((env - 1.0) * 180.0))),
        }

        default_line = round(projection * 2.0) / 2.0

        for side in ["OVER", "UNDER"]:
            odds = odds_map.get((target_team_id, side))
            line = _to_float(odds.get("line")) if odds else default_line
            if line is None:
                continue

            prob_over = _sigmoid((projection - line) / 1.20)
            model_prob = prob_over if side == "OVER" else (1.0 - prob_over)

            # Odds enrichment (optional)
            implied_prob = _to_float(odds.get("implied_probability")) if odds else None
            edge_prob = probability_edge_pct(model_prob, implied_prob)
            edge_proj = projection_edge_pct(projection, line) if odds else None
            edge_pct = edge_prob if edge_prob is not None else edge_proj

            model_score = (
                (factors["offense_strength_score"] * 0.38)
                + (factors["opponent_starter_suppress_score"] * 0.24)
                + (factors["opponent_bullpen_suppress_score"] * 0.22)
                + (factors["park_weather_score"] * 0.16)
            )
            if side == "UNDER":
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
                    "entity_type": "team",
                    "game_id": game.game_id,
                    "event_id": odds.get("event_id") if odds else None,
                    "team_id": target_team_id,
                    "opponent_team_id": opp_team_id,
                    "team_abbr": (odds.get("team_abbr") if odds else None) or target_team_id,
                    "opponent_team_abbr": (odds.get("opponent_team_abbr") if odds else None) or opp_team_id,
                    "selection_key": odds.get("selection_key") if odds else None,
                    "side": side,
                    "bet_type": (odds.get("bet_type") if odds else None) or f"TEAM_TOTAL_{side}",
                    "line": line,
                    "model_score": round(model_score, 2),
                    "model_prob": round(model_prob, 4),
                    "model_projection": round(projection, 3),
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
