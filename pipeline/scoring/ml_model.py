"""
Moneyline model.

Convention: score both sides (HOME and AWAY).
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
    get_game_sides,
    get_market_odds_rows,
    probability_edge_pct,
)


MARKET = "ML"
BET_TYPE_DEFAULT = "ML"


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


def _starter_strength(pitcher: dict[str, Any] | None) -> float:
    if not pitcher:
        return 0.0
    k = _to_float(pitcher.get("k_pct_30") or pitcher.get("k_pct_14")) or 22.0
    bb = _to_float(pitcher.get("bb_pct_30") or pitcher.get("bb_pct_14")) or 8.0
    hr9 = _to_float(pitcher.get("hr_per_9_30") or pitcher.get("hr_per_9_14")) or 1.1
    role = _to_float(pitcher.get("starter_role_confidence")) or 0.6
    return ((k - bb) * 0.7) - ((hr9 - 1.1) * 12.0) + ((role - 0.6) * 8.0)


def _offense_strength(team: dict[str, Any] | None) -> float:
    if not team:
        return 0.0
    runs = _to_float(team.get("runs_per_game_30") or team.get("runs_per_game_14")) or 4.4
    obp = _to_float(team.get("offense_obp_30") or team.get("offense_obp_14")) or 0.320
    slg = _to_float(team.get("offense_slg_30") or team.get("offense_slg_14")) or 0.405
    hr_rate = _to_float(team.get("hr_rate_30") or team.get("hr_rate_14")) or 0.032
    return ((runs - 4.4) * 2.8) + ((obp - 0.320) * 120.0) + ((slg - 0.405) * 55.0) + ((hr_rate - 0.032) * 180.0)


def _bullpen_strength(team: dict[str, Any] | None) -> float:
    if not team:
        return 0.0
    era_proxy = _to_float(team.get("bullpen_era_proxy_14")) or 4.2
    high_lev_era = _to_float(team.get("bullpen_high_lev_era_14"))
    era = (0.6 * high_lev_era + 0.4 * era_proxy) if high_lev_era is not None else era_proxy
    whip = _to_float(team.get("bullpen_whip_proxy_14")) or 1.30
    k = _to_float(team.get("bullpen_k_pct_14")) or 22.0
    hr9 = _to_float(team.get("bullpen_hr9_14")) or 1.1
    return ((4.2 - era) * 2.0) + ((1.30 - whip) * 14.0) + ((k - 22.0) * 0.55) - ((hr9 - 1.1) * 7.0)


def score_game(game: GameContext, weather: dict | None, park_factor: float, season: int) -> list[dict]:
    del weather, season

    context = _context(game.game_date, game.game_id)
    lineup_confirmed = bool((context or {}).get("lineups_confirmed_home") and (context or {}).get("lineups_confirmed_away"))
    weather_multiplier = _to_float((context or {}).get("weather_run_multiplier")) or 1.0

    home_team = _team_features(game.game_date, game.home_team)
    away_team = _team_features(game.game_date, game.away_team)
    home_pitcher = _pitcher_features(game.game_date, game.home_pitcher_id)
    away_pitcher = _pitcher_features(game.game_date, game.away_pitcher_id)

    home_strength = _offense_strength(home_team) + _bullpen_strength(home_team) + _starter_strength(home_pitcher)
    away_strength = _offense_strength(away_team) + _bullpen_strength(away_team) + _starter_strength(away_pitcher)
    home_field_adv = 1.8
    weather_home_adj = (weather_multiplier - 1.0) * 2.0
    net_home = home_strength - away_strength + home_field_adv + weather_home_adj
    home_win_prob = _sigmoid(net_home / 8.5)
    away_win_prob = 1.0 - home_win_prob

    # Build universe from game sides; enrich with odds if available
    sides = get_game_sides(game)
    odds_rows = get_market_odds_rows(game_date=game.game_date, market=MARKET, game_id=game.game_id)
    odds_by_side: dict[str, dict[str, Any]] = {}
    for o in odds_rows:
        s = (o.get("side") or "").upper()
        if s in {"HOME", "AWAY"}:
            odds_by_side[s] = o

    results: list[dict[str, Any]] = []
    for side_info in sides:
        side = side_info["side"]
        model_prob = home_win_prob if side == "HOME" else away_win_prob

        # Odds enrichment (optional)
        odds = odds_by_side.get(side)
        implied_prob = _to_float(odds.get("implied_probability")) if odds else None
        edge_pct = probability_edge_pct(model_prob, implied_prob)

        side_strength = home_strength if side == "HOME" else away_strength
        opp_strength = away_strength if side == "HOME" else home_strength
        strength_gap = side_strength - opp_strength
        model_score = 50.0 + (model_prob - 0.5) * 90.0 + (strength_gap * 0.4)
        if edge_pct is not None:
            model_score += max(-8.0, min(8.0, edge_pct * 0.35))
        model_score = max(0.0, min(100.0, model_score))

        factors = {
            "starter_edge_score": max(0.0, min(100.0, 50.0 + ((_starter_strength(home_pitcher if side == "HOME" else away_pitcher) - _starter_strength(away_pitcher if side == "HOME" else home_pitcher)) * 2.1))),
            "offense_edge_score": max(0.0, min(100.0, 50.0 + ((_offense_strength(home_team if side == "HOME" else away_team) - _offense_strength(away_team if side == "HOME" else home_team)) * 2.5))),
            "bullpen_edge_score": max(0.0, min(100.0, 50.0 + ((_bullpen_strength(home_team if side == "HOME" else away_team) - _bullpen_strength(away_team if side == "HOME" else home_team)) * 3.0))),
            "home_field_score": 62.0 if side == "HOME" else 38.0,
            "weather_context_score": max(0.0, min(100.0, 50.0 + ((weather_multiplier - 1.0) * 150.0))),
        }
        reasons = build_reasons(factors)
        risk_flags = build_risk_flags(
            missing_inputs=[],
            lineup_pending=not lineup_confirmed,
            weather_pending=context is None,
        )

        team_id = side_info["team_id"]
        opponent_team_id = side_info["opponent_team_id"]
        results.append(
            {
                "market": MARKET,
                "entity_type": "game",
                "game_id": game.game_id,
                "event_id": odds.get("event_id") if odds else None,
                "team_id": team_id,
                "opponent_team_id": opponent_team_id,
                "team_abbr": (odds.get("team_abbr") if odds else None) or team_id,
                "opponent_team_abbr": (odds.get("opponent_team_abbr") if odds else None) or opponent_team_id,
                "selection_key": odds.get("selection_key") if odds else None,
                "side": side,
                "bet_type": (odds.get("bet_type") if odds else None) or f"ML_{side}",
                "line": _to_float(odds.get("line")) if odds else None,
                "model_score": round(model_score, 2),
                "model_prob": round(model_prob, 4),
                "model_projection": None,
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
