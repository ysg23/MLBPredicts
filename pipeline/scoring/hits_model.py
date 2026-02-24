"""
Hits markets model.

Supports:
- HITS_1P (1+ hit, yes/no)
- HITS_LINE (over/under line)
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


SUPPORTED_MARKETS = {"HITS_1P", "HITS_LINE"}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _poisson_prob_at_most(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k >= 0 else 0.0
    total = 0.0
    for i in range(0, max(0, k) + 1):
        total += math.exp(-lam) * (lam ** i) / math.factorial(i)
    return min(1.0, max(0.0, total))


def _batting_order_for_player(game_date: str, game_id: int, player_id: int) -> tuple[int | None, int]:
    rows = query(
        """
        SELECT batting_order, confirmed
        FROM lineups
        WHERE game_date = ?
          AND game_id = ?
          AND player_id = ?
          AND COALESCE(active_version, 1) = 1
        ORDER BY fetched_at DESC
        LIMIT 1
        """,
        (game_date, game_id, player_id),
    )
    if not rows:
        return None, 0
    return rows[0].get("batting_order"), int(rows[0].get("confirmed") or 0)


def _player_features(game_date: str, player_id: int) -> dict[str, Any] | None:
    rows = query(
        """
        SELECT *
        FROM batter_daily_features
        WHERE game_date = ? AND player_id = ?
        LIMIT 1
        """,
        (game_date, player_id),
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


def _infer_opp_pitcher(game: GameContext, batting_team: str | None) -> int | None:
    if batting_team is None:
        return None
    if batting_team == game.home_team:
        return game.away_pitcher_id
    if batting_team == game.away_team:
        return game.home_pitcher_id
    return None


def _pa_expectation(batting_order: int | None) -> float:
    if batting_order is None:
        return 4.1
    slot = int(batting_order)
    if slot <= 2:
        return 4.7
    if slot <= 4:
        return 4.5
    if slot <= 6:
        return 4.2
    return 3.9


def _score_from_factors(factors: dict[str, float]) -> float:
    weights = {
        "contact_score": 0.25,
        "hit_form_score": 0.25,
        "pitcher_contact_allow_score": 0.18,
        "batting_order_score": 0.12,
        "context_score": 0.10,
        "platoon_fit_score": 0.05,
        "hot_cold_score": 0.05,
    }
    score = 0.0
    for key, weight in weights.items():
        score += factors.get(key, 50.0) * weight
    return max(0.0, min(100.0, score))


def _build_projection_and_probs(
    market: str,
    line: float | None,
    base_hit_rate: float,
    pa_expect: float,
) -> tuple[float, float]:
    projection = max(0.0, min(3.5, base_hit_rate * pa_expect))
    if market == "HITS_1P":
        prob_yes = 1.0 - ((1.0 - max(0.01, min(0.8, base_hit_rate))) ** pa_expect)
        return projection, max(0.01, min(0.99, prob_yes))

    threshold = int(math.floor(line if line is not None else 0.5))
    prob_over = 1.0 - _poisson_prob_at_most(threshold, projection)
    return projection, max(0.01, min(0.99, prob_over))


def score_game(game: GameContext, weather: dict | None, park_factor: float, season: int) -> list[dict]:
    del weather, park_factor, season
    results: list[dict[str, Any]] = []
    context = _context(game.game_date, game.game_id)
    weather_temp = _to_float((context or {}).get("weather_temp_f"))
    weather_mult = _to_float((context or {}).get("weather_run_multiplier")) or 1.0
    lineups_confirmed_all = bool((context or {}).get("lineups_confirmed_home") and (context or {}).get("lineups_confirmed_away"))

    for market in sorted(SUPPORTED_MARKETS):
        odds_rows = get_market_odds_rows(game_date=game.game_date, market=market, game_id=game.game_id)
        for odds in odds_rows:
            player_id = odds.get("player_id")
            if player_id is None:
                continue

            player_features = _player_features(game.game_date, int(player_id))
            if player_features is None:
                continue

            batting_team = odds.get("team_id")
            opp_pitcher_id = _infer_opp_pitcher(game, batting_team)
            opp_pitcher_features = _pitcher_features(game.game_date, opp_pitcher_id)
            batting_order, lineup_confirmed_player = _batting_order_for_player(game.game_date, game.game_id, int(player_id))

            hit_rate_14 = _to_float(player_features.get("hit_rate_14"))
            hit_rate_30 = _to_float(player_features.get("hit_rate_30"))
            k_pct_14 = _to_float(player_features.get("k_pct_14"))
            hot_cold_delta = _to_float(player_features.get("hot_cold_delta_hit_rate")) or 0.0

            if hit_rate_14 is None and hit_rate_30 is None:
                continue
            base_hit_rate = (
                (0.6 * (hit_rate_14 if hit_rate_14 is not None else 0.0))
                + (0.4 * (hit_rate_30 if hit_rate_30 is not None else 0.0))
            )
            base_hit_rate = max(0.08, min(0.45, base_hit_rate))

            pitcher_suppress = 0.0
            if opp_pitcher_features:
                opp_k = _to_float(opp_pitcher_features.get("k_pct_14")) or 22.0
                opp_hard_hit = _to_float(opp_pitcher_features.get("hard_hit_pct_allowed_14")) or 35.0
                pitcher_suppress = ((opp_k - 22.0) * 0.0025) - ((opp_hard_hit - 35.0) * 0.0015)

            pa_expect = _pa_expectation(batting_order)
            if lineup_confirmed_player == 0:
                pa_expect *= 0.95
            adjusted_hit_rate = max(0.06, min(0.55, base_hit_rate - pitcher_suppress))
            adjusted_hit_rate *= weather_mult
            adjusted_hit_rate = max(0.06, min(0.60, adjusted_hit_rate))

            line = _to_float(odds.get("line"))
            projection, prob_over_or_yes = _build_projection_and_probs(market, line, adjusted_hit_rate, pa_expect)
            side = (odds.get("side") or "OVER").upper()
            if market == "HITS_1P":
                # yes/no shape
                model_prob = prob_over_or_yes if side in {"YES", "OVER"} else (1.0 - prob_over_or_yes)
            else:
                model_prob = prob_over_or_yes if side == "OVER" else (1.0 - prob_over_or_yes)

            implied_prob = _to_float(odds.get("implied_probability"))
            edge_prob = probability_edge_pct(model_prob, implied_prob)
            edge_proj = projection_edge_pct(projection, line)
            edge_pct = edge_prob if edge_prob is not None else edge_proj

            contact_score = 100.0 - ((k_pct_14 or 22.0) * 2.2)
            hit_form_score = 50.0 + (((hit_rate_14 or hit_rate_30 or 0.25) - 0.25) * 220.0)
            pitcher_contact_allow_score = 50.0
            if opp_pitcher_features:
                pitcher_contact_allow_score += ((_to_float(opp_pitcher_features.get("hard_hit_pct_allowed_14")) or 35.0) - 35.0) * 1.5
            batting_order_score = 75.0 - ((batting_order or 5) - 1) * 7.0
            context_score = 50.0 + ((weather_temp - 70.0) * 0.7 if weather_temp is not None else 0.0)
            platoon_fit_score = 50.0
            hot_cold_score = 50.0 + (hot_cold_delta * 220.0)

            factors = {
                "contact_score": max(0.0, min(100.0, contact_score)),
                "hit_form_score": max(0.0, min(100.0, hit_form_score)),
                "pitcher_contact_allow_score": max(0.0, min(100.0, pitcher_contact_allow_score)),
                "batting_order_score": max(0.0, min(100.0, batting_order_score)),
                "context_score": max(0.0, min(100.0, context_score)),
                "platoon_fit_score": max(0.0, min(100.0, platoon_fit_score)),
                "hot_cold_score": max(0.0, min(100.0, hot_cold_score)),
            }

            model_score = _score_from_factors(factors)
            risk_flags = build_risk_flags(
                missing_inputs=[] if opp_pitcher_features is not None else ["opposing_pitcher_features"],
                lineup_pending=not bool(lineups_confirmed_all and lineup_confirmed_player),
                weather_pending=context is None,
            )
            reasons = build_reasons(factors)

            results.append(
                {
                    "market": market,
                    "entity_type": "batter",
                    "game_id": game.game_id,
                    "event_id": odds.get("event_id"),
                    "player_id": int(player_id),
                    "player_name": odds.get("player_name"),
                    "team_id": batting_team,
                    "opponent_team_id": odds.get("opponent_team_id"),
                    "team_abbr": odds.get("team_abbr"),
                    "opponent_team_abbr": odds.get("opponent_team_abbr"),
                    "selection_key": odds.get("selection_key"),
                    "side": side,
                    "bet_type": odds.get("bet_type") or f"{market}_{side}",
                    "line": line,
                    "model_score": round(model_score, 2),
                    "model_prob": round(model_prob, 4),
                    "model_projection": round(projection, 3),
                    "book_implied_prob": round(implied_prob, 4) if implied_prob is not None else None,
                    "edge": round(edge_pct, 3) if edge_pct is not None else None,
                    "signal": assign_signal(market, model_score, edge_pct),
                    "factors_json": factors,
                    "reasons_json": reasons,
                    "risk_flags_json": risk_flags,
                    "lineup_confirmed": 1 if (lineups_confirmed_all and lineup_confirmed_player) else 0,
                    "weather_final": 1 if (context and context.get("weather_temp_f") is not None) else 0,
                }
            )

    return results
