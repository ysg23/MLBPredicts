"""
Strikeout props model (K over/under) using feature store + normalized odds.

Pattern: one model_scores row per odds side row (OVER/UNDER).
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
    percentile_score,
    probability_edge_pct,
    projection_edge_pct,
)


MARKET = "K"
BET_TYPE_DEFAULT = "K_LINE"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _get_pitcher_features(game_date: str, pitcher_id: int) -> dict[str, Any] | None:
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


def _get_team_features(game_date: str, team_id: str | None) -> dict[str, Any] | None:
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


def _get_context(game_date: str, game_id: int) -> dict[str, Any] | None:
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


def _all_pitcher_k_values(game_date: str) -> list[float]:
    rows = query(
        """
        SELECT k_pct_14 AS v
        FROM pitcher_daily_features
        WHERE game_date = ? AND k_pct_14 IS NOT NULL
        """,
        (game_date,),
    )
    return [float(r["v"]) for r in rows if r.get("v") is not None]


def _all_pitcher_whiff_values(game_date: str) -> list[float]:
    rows = query(
        """
        SELECT whiff_pct_14 AS v
        FROM pitcher_daily_features
        WHERE game_date = ? AND whiff_pct_14 IS NOT NULL
        """,
        (game_date,),
    )
    return [float(r["v"]) for r in rows if r.get("v") is not None]


def _project_ks(
    pitcher_features: dict[str, Any],
    opp_team_features: dict[str, Any] | None,
    context: dict[str, Any] | None,
) -> tuple[float, dict[str, float], list[str]]:
    missing_inputs: list[str] = []

    k_pct_14 = _to_float(pitcher_features.get("k_pct_14"))
    whiff_14 = _to_float(pitcher_features.get("whiff_pct_14"))
    chase_14 = _to_float(pitcher_features.get("chase_pct_14"))
    role = _to_float(pitcher_features.get("starter_role_confidence"))
    bf14 = _to_float(pitcher_features.get("batters_faced_14"))
    tto_k_decay = _to_float(pitcher_features.get("tto_k_decay_pct"))
    tto_endurance = _to_float(pitcher_features.get("tto_endurance_score"))

    if k_pct_14 is None:
        missing_inputs.append("pitcher_k_pct_14")
    if whiff_14 is None:
        missing_inputs.append("pitcher_whiff_pct_14")
    if role is None:
        missing_inputs.append("starter_role_confidence")

    opp_k_14 = _to_float((opp_team_features or {}).get("offense_k_pct_14"))
    if opp_k_14 is None:
        missing_inputs.append("opponent_offense_k_pct_14")

    ump_boost = _to_float((context or {}).get("umpire_k_boost")) or 0.0
    weather_temp = _to_float((context or {}).get("weather_temp_f"))
    is_day_game = (context or {}).get("is_day_game")
    weather_adj = 0.0
    if weather_temp is not None:
        # cooler games slightly improve strikeout environment.
        weather_adj = (68.0 - weather_temp) * 0.015

    # Day games historically have slightly lower K rates (better visibility)
    day_night_adj = 0.0
    if is_day_game == 1:
        day_night_adj = -0.008  # ~0.8% lower K rate in day games
    elif is_day_game == 0:
        day_night_adj = 0.003

    # TTO adjustment: pitchers with high K decay lose effectiveness deeper into games
    tto_adj = 0.0
    if tto_k_decay is not None:
        # League avg is 18% decay. Better than avg = positive K projection adj
        tto_adj = (18.0 - tto_k_decay) * 0.001  # low decay = more Ks sustained

    # Expected batters faced proxy. If missing, use a neutral starter volume.
    expected_bf = (bf14 / 3.0) if bf14 is not None else 24.0
    expected_bf = max(16.0, min(30.0, expected_bf))
    expected_role = role if role is not None else 0.55
    expected_bf *= (0.8 + 0.4 * expected_role)

    k_rate = (k_pct_14 / 100.0) if k_pct_14 is not None else 0.22
    opp_adj = ((opp_k_14 - 22.0) / 100.0) if opp_k_14 is not None else 0.0
    chase_adj = ((chase_14 - 30.0) / 100.0) if chase_14 is not None else 0.0
    whiff_adj = ((whiff_14 - 24.0) / 100.0) if whiff_14 is not None else 0.0
    ump_adj = ump_boost / 100.0

    effective_k_rate = max(0.12, min(0.45, k_rate + (0.35 * opp_adj) + (0.2 * whiff_adj) + (0.1 * chase_adj) + ump_adj + weather_adj + day_night_adj + tto_adj))
    projection = max(1.5, min(12.5, effective_k_rate * expected_bf))

    # TTO endurance score: high endurance = pitcher sustains Ks deeper
    tto_score = 50.0
    if tto_endurance is not None:
        tto_score = float(tto_endurance)

    # Day/night context score contribution
    day_night_score = 50.0
    if is_day_game == 0:
        day_night_score = 55.0  # slight K boost for night games
    elif is_day_game == 1:
        day_night_score = 42.0  # slight K suppression for day games

    factors = {
        "k_form_score": 50.0 + ((k_pct_14 or 22.0) - 22.0) * 3.0,
        "opponent_k_score": 50.0 + ((opp_k_14 or 22.0) - 22.0) * 3.0,
        "whiff_chase_score": 50.0 + (((whiff_14 or 24.0) - 24.0) * 2.0) + (((chase_14 or 30.0) - 30.0) * 1.3),
        "role_score": (expected_role * 100.0),
        "context_score": 50.0 + (ump_boost * 2.0) + (weather_adj * 50.0),
        "tto_endurance_score": tto_score,
        "day_night_score": day_night_score,
    }
    factors = {k: max(0.0, min(100.0, float(v))) for k, v in factors.items()}

    return projection, factors, missing_inputs


def score_game(game: GameContext, weather: dict | None, park_factor: float, season: int) -> list[dict]:
    del weather, park_factor, season  # context features table is used instead.
    odds_rows = get_market_odds_rows(game_date=game.game_date, market=MARKET, game_id=game.game_id)
    if not odds_rows:
        return []

    context = _get_context(game.game_date, game.game_id)
    lineup_pending = not bool((context or {}).get("lineups_confirmed_home") and (context or {}).get("lineups_confirmed_away"))
    weather_pending = bool(context is None or context.get("weather_temp_f") is None)

    league_k_values = _all_pitcher_k_values(game.game_date)
    league_whiff_values = _all_pitcher_whiff_values(game.game_date)

    results: list[dict[str, Any]] = []
    for odds in odds_rows:
        pitcher_id = odds.get("player_id")
        if pitcher_id is None:
            continue

        pitcher_features = _get_pitcher_features(game.game_date, int(pitcher_id))
        if pitcher_features is None:
            continue

        opp_team_id = odds.get("opponent_team_id")
        if not opp_team_id:
            # Infer opponent from scheduled game teams when odds row omits opponent.
            if odds.get("team_id") == game.home_team:
                opp_team_id = game.away_team
            elif odds.get("team_id") == game.away_team:
                opp_team_id = game.home_team

        opp_team_features = _get_team_features(game.game_date, opp_team_id)
        projection, factors, missing_inputs = _project_ks(pitcher_features, opp_team_features, context)

        line = _to_float(odds.get("line"))
        model_prob_over = _sigmoid((projection - (line if line is not None else 5.5)) / 1.25)
        side = (odds.get("side") or "").upper() or "OVER"
        model_prob = model_prob_over if side == "OVER" else (1.0 - model_prob_over)

        implied_prob = _to_float(odds.get("implied_probability"))
        edge_prob = probability_edge_pct(model_prob, implied_prob)
        edge_proj = projection_edge_pct(projection, line)
        edge_pct = edge_prob if edge_prob is not None else edge_proj

        # Composite score from factor strengths + directional edge confidence.
        k_form_pct = percentile_score(league_k_values, _to_float(pitcher_features.get("k_pct_14")))
        whiff_pct = percentile_score(league_whiff_values, _to_float(pitcher_features.get("whiff_pct_14")))
        base_score = (
            0.30 * k_form_pct
            + 0.18 * factors["opponent_k_score"]
            + 0.18 * whiff_pct
            + 0.12 * factors["role_score"]
            + 0.08 * factors["context_score"]
            + 0.08 * factors["tto_endurance_score"]
            + 0.06 * factors["day_night_score"]
        )
        edge_component = 0.0
        if edge_pct is not None:
            edge_component = max(-8.0, min(8.0, edge_pct * 0.4))
        model_score = max(0.0, min(100.0, base_score + edge_component))

        risk_flags = build_risk_flags(
            missing_inputs=missing_inputs,
            lineup_pending=lineup_pending,
            weather_pending=weather_pending,
        )
        reasons = build_reasons(factors)
        if odds.get("player_name"):
            pitcher_name = odds.get("player_name")
        elif int(pitcher_id) == (game.home_pitcher_id or -1):
            pitcher_name = game.home_pitcher_name
        else:
            pitcher_name = game.away_pitcher_name

        results.append(
            {
                "market": MARKET,
                "entity_type": "pitcher",
                "game_id": game.game_id,
                "event_id": odds.get("event_id"),
                "player_id": int(pitcher_id),
                "player_name": pitcher_name,
                "team_id": odds.get("team_id"),
                "opponent_team_id": opp_team_id,
                "team_abbr": odds.get("team_abbr"),
                "opponent_team_abbr": odds.get("opponent_team_abbr"),
                "selection_key": odds.get("selection_key"),
                "side": side,
                "bet_type": odds.get("bet_type") or f"K_{side}",
                "line": line,
                "model_score": round(model_score, 2),
                "model_prob": round(model_prob, 4),
                "model_projection": round(projection, 2),
                "book_implied_prob": round(implied_prob, 4) if implied_prob is not None else None,
                "edge": round(edge_pct, 3) if edge_pct is not None else None,
                "signal": assign_signal(MARKET, model_score, edge_pct),
                "factors_json": factors,
                "reasons_json": reasons,
                "risk_flags_json": risk_flags,
                "lineup_confirmed": 0 if lineup_pending else 1,
                "weather_final": 0 if weather_pending else 1,
            }
        )

    return results
