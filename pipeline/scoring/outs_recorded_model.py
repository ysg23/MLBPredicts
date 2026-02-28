"""
Outs recorded props model.
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
    get_pitcher_universe,
    probability_edge_pct,
    projection_edge_pct,
)


MARKET = "OUTS_RECORDED"
BET_TYPE_DEFAULT = "OUTS_RECORDED"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _pitcher_features(game_date: str, pitcher_id: int) -> dict[str, Any] | None:
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


def _project_outs(
    pitcher: dict[str, Any],
    opp_team: dict[str, Any] | None,
    context: dict[str, Any] | None,
) -> tuple[float, dict[str, float], list[str]]:
    missing_inputs: list[str] = []
    role = _to_float(pitcher.get("starter_role_confidence"))
    outs_last5 = _to_float(pitcher.get("outs_recorded_avg_last_5"))
    pitches_last5 = _to_float(pitcher.get("pitches_avg_last_5"))
    k_pct = _to_float(pitcher.get("k_pct_14"))
    bb_pct = _to_float(pitcher.get("bb_pct_14"))

    if role is None:
        missing_inputs.append("starter_role_confidence")
    if outs_last5 is None:
        missing_inputs.append("outs_recorded_avg_last_5")
    if pitches_last5 is None:
        missing_inputs.append("pitches_avg_last_5")

    opp_bb = _to_float((opp_team or {}).get("offense_bb_pct_14"))
    opp_runs = _to_float((opp_team or {}).get("runs_per_game_14"))
    if opp_bb is None:
        missing_inputs.append("opponent_offense_bb_pct_14")

    weather_risk = 0.0
    wind_speed = _to_float((context or {}).get("weather_wind_speed_mph"))
    if wind_speed is not None and wind_speed >= 18:
        weather_risk = 0.3

    role_val = role if role is not None else 0.55
    base_outs = outs_last5 if outs_last5 is not None else 16.5 + (role_val * 2.5)
    pitch_cap = pitches_last5 if pitches_last5 is not None else 88.0
    efficiency_adj = 0.0
    if bb_pct is not None:
        efficiency_adj -= (bb_pct - 8.0) * 0.20
    if k_pct is not None:
        efficiency_adj += (k_pct - 22.0) * 0.12
    if opp_bb is not None:
        efficiency_adj -= (opp_bb - 8.0) * 0.25
    if opp_runs is not None:
        efficiency_adj -= (opp_runs - 4.4) * 0.25
    efficiency_adj -= weather_risk * 1.4

    pitch_adj = (pitch_cap - 88.0) * 0.06
    projection = base_outs + pitch_adj + efficiency_adj
    projection = max(9.0, min(24.0, projection))

    factors = {
        "starter_leash_score": max(0.0, min(100.0, role_val * 100.0)),
        "pitch_count_score": max(0.0, min(100.0, 50.0 + ((pitch_cap - 88.0) * 1.8))),
        "efficiency_score": max(0.0, min(100.0, 50.0 + (efficiency_adj * 4.0))),
        "opponent_patience_score": max(0.0, min(100.0, 70.0 - ((opp_bb or 8.0) * 3.0))),
        "weather_delay_risk_score": max(0.0, min(100.0, 65.0 - (weather_risk * 70.0))),
    }
    return projection, factors, missing_inputs


def score_game(game: GameContext, weather: dict | None, park_factor: float, season: int) -> list[dict]:
    del weather, park_factor, season

    context = _context(game.game_date, game.game_id)
    lineups_confirmed = bool((context or {}).get("lineups_confirmed_home") and (context or {}).get("lineups_confirmed_away"))

    # Build pitcher universe from games table, enrich with odds if available
    pitchers = get_pitcher_universe(game)
    if not pitchers:
        return []

    all_odds = get_market_odds_rows(game_date=game.game_date, market=MARKET, game_id=game.game_id)
    odds_by_pitcher: dict[int, list[dict[str, Any]]] = {}
    for o in all_odds:
        pid = o.get("player_id")
        if pid is not None:
            odds_by_pitcher.setdefault(int(pid), []).append(o)

    results: list[dict[str, Any]] = []
    for p in pitchers:
        pitcher_id = p["player_id"]
        pitcher = _pitcher_features(game.game_date, int(pitcher_id))
        if pitcher is None:
            continue

        opp_team = _team_features(game.game_date, p["opponent_team_id"])
        projection, factors, missing_inputs = _project_outs(pitcher, opp_team, context)

        base_score = max(0.0, min(100.0, (
            (factors["starter_leash_score"] * 0.30)
            + (factors["pitch_count_score"] * 0.22)
            + (factors["efficiency_score"] * 0.24)
            + (factors["opponent_patience_score"] * 0.16)
            + (factors["weather_delay_risk_score"] * 0.08)
        )))

        risk_flags = build_risk_flags(
            missing_inputs=missing_inputs,
            lineup_pending=not lineups_confirmed,
            weather_pending=context is None,
        )
        reasons = build_reasons(factors)
        pitcher_name = p.get("player_name")

        pitcher_odds = odds_by_pitcher.get(int(pitcher_id), [])
        if pitcher_odds:
            for odds in pitcher_odds:
                line = _to_float(odds.get("line"))
                side = (odds.get("side") or "OVER").upper()
                if line is None:
                    line = 15.5
                prob_over = _sigmoid((projection - line) / 1.6)
                model_prob = prob_over if side == "OVER" else (1.0 - prob_over)

                implied_prob = _to_float(odds.get("implied_probability"))
                edge_prob = probability_edge_pct(model_prob, implied_prob)
                edge_proj = projection_edge_pct(projection, line)
                edge_pct = edge_prob if edge_prob is not None else edge_proj

                model_score = base_score
                if edge_pct is not None:
                    model_score = max(0.0, min(100.0, model_score + max(-8.0, min(8.0, edge_pct * 0.35))))

                results.append(
                    {
                        "market": MARKET,
                        "entity_type": "pitcher",
                        "game_id": game.game_id,
                        "event_id": odds.get("event_id"),
                        "player_id": int(pitcher_id),
                        "player_name": odds.get("player_name") or pitcher_name,
                        "team_id": p["team_id"],
                        "opponent_team_id": p["opponent_team_id"],
                        "team_abbr": odds.get("team_abbr") or p["team_abbr"],
                        "opponent_team_abbr": odds.get("opponent_team_abbr") or p["opponent_team_abbr"],
                        "selection_key": odds.get("selection_key"),
                        "side": side,
                        "bet_type": odds.get("bet_type") or f"OUTS_RECORDED_{side}",
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
                        "lineup_confirmed": 1 if lineups_confirmed else 0,
                        "weather_final": 1 if (context and context.get("weather_temp_f") is not None) else 0,
                    }
                )
        else:
            # No odds — emit OVER row with default line
            default_line = round(projection * 2.0) / 2.0
            prob_over = _sigmoid((projection - default_line) / 1.6)

            results.append(
                {
                    "market": MARKET,
                    "entity_type": "pitcher",
                    "game_id": game.game_id,
                    "event_id": None,
                    "player_id": int(pitcher_id),
                    "player_name": pitcher_name,
                    "team_id": p["team_id"],
                    "opponent_team_id": p["opponent_team_id"],
                    "team_abbr": p["team_abbr"],
                    "opponent_team_abbr": p["opponent_team_abbr"],
                    "selection_key": None,
                    "side": "OVER",
                    "bet_type": "OUTS_RECORDED_OVER",
                    "line": default_line,
                    "model_score": round(base_score, 2),
                    "model_prob": round(prob_over, 4),
                    "model_projection": round(projection, 3),
                    "book_implied_prob": None,
                    "edge": None,
                    "signal": assign_signal(MARKET, base_score, None),
                    "factors_json": factors,
                    "reasons_json": reasons,
                    "risk_flags_json": risk_flags,
                    "lineup_confirmed": 1 if lineups_confirmed else 0,
                    "weather_final": 1 if (context and context.get("weather_temp_f") is not None) else 0,
                }
            )
    return results
