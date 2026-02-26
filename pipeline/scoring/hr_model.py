"""
HR market scoring module.

Scores HR 1+ props using batter_daily_features and pitcher_daily_features.
Universe built from feature tables; odds are optional enrichment.
"""
from __future__ import annotations

from typing import Any

from db.database import query
from .base_engine import (
    GameContext,
    assign_signal,
    build_reasons,
    build_risk_flags,
    get_batter_universe,
    get_market_odds_rows,
    choose_best_odds_row,
    implied_prob_from_american,
    probability_edge_pct,
)


MARKET = "HR"
BET_TYPE_DEFAULT = "HR_1PLUS"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


def _scale_between(x: float | None, lo: float, hi: float) -> float:
    if x is None:
        return 50.0
    if hi == lo:
        return 50.0
    return _clamp((x - lo) / (hi - lo) * 100.0)


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


def _opp_pitcher_hand(game: GameContext, batting_team: str | None) -> str | None:
    if batting_team is None:
        return None
    if batting_team == game.home_team:
        return game.away_pitcher_hand
    if batting_team == game.away_team:
        return game.home_pitcher_hand
    return None


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


def _score_from_factors(factors: dict[str, float]) -> float:
    weights = {
        "barrel_score": 0.22,
        "matchup_score": 0.18,
        "park_weather_score": 0.20,
        "pitcher_vuln_score": 0.15,
        "hot_cold_score": 0.08,
        "lineup_order_score": 0.10,
        "tto_score": 0.07,
    }
    return _clamp(sum(factors.get(k, 50.0) * w for k, w in weights.items()))


def score_game(game: GameContext, weather: dict | None, park_factor: float, season: int) -> list[dict]:
    del season

    # Build player universe from features (not odds)
    universe = get_batter_universe(game.game_date, game)
    if not universe:
        return []

    context = _context(game.game_date, game.game_id)
    is_day_game = (context or {}).get("is_day_game")
    weather_hr_mult = _to_float((context or {}).get("weather_hr_multiplier")) or 1.0
    lineups_confirmed = bool(
        (context or {}).get("lineups_confirmed_home") and (context or {}).get("lineups_confirmed_away")
    )

    # Weather/park environment multiplier
    wind_mult = float(weather["wind_hr_impact"]) if weather and weather.get("wind_hr_impact") is not None else 1.0
    temp = float(weather["temperature_f"]) if weather and weather.get("temperature_f") is not None else None
    if temp is None:
        temp_mult = 1.0
    else:
        temp_mult = 0.95 + (max(0.0, min(40.0, temp - 50.0)) / 40.0) * 0.12
    park_weather_mult = park_factor * wind_mult * temp_mult * weather_hr_mult
    park_weather_score = _scale_between(park_weather_mult, 0.85, 1.20)

    # Pre-fetch all odds for optional enrichment
    all_odds = get_market_odds_rows(game_date=game.game_date, market=MARKET, game_id=game.game_id)
    odds_by_player: dict[int, list[dict[str, Any]]] = {}
    for o in all_odds:
        pid = o.get("player_id")
        if pid is not None:
            odds_by_player.setdefault(int(pid), []).append(o)

    results: list[dict[str, Any]] = []
    for entity in universe:
        player_id = int(entity["player_id"])
        batting_team = entity["team_id"]

        batter = _player_features(game.game_date, player_id)
        if batter is None:
            continue

        opp_pitcher_id = _infer_opp_pitcher(game, batting_team)
        opp_pitcher = _pitcher_features(game.game_date, opp_pitcher_id)
        opp_hand = _opp_pitcher_hand(game, batting_team)
        batting_order, lineup_confirmed_player = _batting_order_for_player(
            game.game_date, game.game_id, player_id
        )

        # Batter power metrics from feature store
        barrel_14 = _to_float(batter.get("barrel_pct_14"))
        iso_14 = _to_float(batter.get("iso_14"))
        iso_7 = _to_float(batter.get("iso_7"))
        iso_30 = _to_float(batter.get("iso_30"))
        hr_rate_14 = _to_float(batter.get("hr_rate_14"))

        # Platoon ISO split
        iso_split = None
        if opp_hand == "L":
            iso_split = _to_float(batter.get("iso_vs_lhp"))
        elif opp_hand == "R":
            iso_split = _to_float(batter.get("iso_vs_rhp"))

        if barrel_14 is None and iso_14 is None and hr_rate_14 is None:
            continue

        # --- Factor scores ---
        # Barrel quality
        barrel_score = _scale_between(barrel_14, 4.0, 16.0)

        # Matchup: batter ISO split vs pitcher HR vulnerability
        iso_component = _scale_between(iso_split, 0.10, 0.30)
        pitcher_hr9 = _to_float((opp_pitcher or {}).get("hr_per_9_14"))
        pitcher_hr9_score = _scale_between(pitcher_hr9, 0.6, 1.8)
        matchup_score = 0.65 * iso_component + 0.35 * pitcher_hr9_score

        # Pitcher vulnerability: HR/9 + barrel% allowed
        pitcher_barrel_allow = _to_float((opp_pitcher or {}).get("barrel_pct_allowed_14"))
        pitcher_vuln_score = (
            0.6 * _scale_between(pitcher_hr9, 0.6, 1.8)
            + 0.4 * _scale_between(pitcher_barrel_allow, 5.0, 14.0)
        )

        # Hot/cold: 7-day ISO vs 30-day ISO
        hot_cold_delta = _to_float(batter.get("hot_cold_delta_iso"))
        hot_cold_score = _scale_between(hot_cold_delta, -0.08, 0.08)

        # Lineup order
        order_scores = {1: 72, 2: 78, 3: 85, 4: 82, 5: 70, 6: 58, 7: 45, 8: 35, 9: 28}
        lineup_order_score = float(order_scores.get(batting_order or 5, 50))

        # TTO endurance: low endurance pitcher = good for batter HR
        tto_score = 50.0
        if opp_pitcher and opp_pitcher.get("tto_endurance_score") is not None:
            tto_score = 100.0 - float(opp_pitcher["tto_endurance_score"])

        # Day/night adjustment to score
        day_night_adj = 0.0
        if is_day_game == 1:
            day_night_adj = 4.0  # day games ~4-5% higher HR rate
        elif is_day_game == 0:
            day_night_adj = -2.0

        factors = {
            "barrel_score": _clamp(barrel_score),
            "matchup_score": _clamp(matchup_score),
            "park_weather_score": _clamp(park_weather_score),
            "pitcher_vuln_score": _clamp(pitcher_vuln_score),
            "hot_cold_score": _clamp(hot_cold_score),
            "lineup_order_score": _clamp(lineup_order_score),
            "tto_score": _clamp(tto_score),
        }

        model_score = _clamp(_score_from_factors(factors) + day_night_adj)

        # Model probability: map 0-100 score to ~2-35% HR range
        model_prob = max(0.02, min(0.40, 0.02 + (model_score / 100.0) * 0.33))

        missing_inputs: list[str] = []
        if opp_pitcher is None:
            missing_inputs.append("opposing_pitcher_features")
        risk_flags = build_risk_flags(
            missing_inputs=missing_inputs,
            lineup_pending=not bool(lineups_confirmed and lineup_confirmed_player),
            weather_pending=context is None,
        )
        reasons = build_reasons(factors)

        # Optionally enrich with odds if available
        player_odds = odds_by_player.get(player_id, [])
        if player_odds:
            best_odds = choose_best_odds_row(player_odds)
            book_prob = None
            if best_odds:
                book_prob = (
                    _to_float(best_odds.get("implied_probability"))
                    or implied_prob_from_american(best_odds.get("price_american"))
                )
            edge_pct = probability_edge_pct(model_prob, book_prob)

            results.append(
                {
                    "market": MARKET,
                    "entity_type": "batter",
                    "game_id": game.game_id,
                    "event_id": best_odds.get("event_id") if best_odds else None,
                    "player_id": player_id,
                    "player_name": (best_odds or {}).get("player_name"),
                    "team_id": batting_team,
                    "opponent_team_id": entity["opponent_team_id"],
                    "team_abbr": (best_odds or {}).get("team_abbr") or entity["team_abbr"],
                    "opponent_team_abbr": (best_odds or {}).get("opponent_team_abbr") or entity["opponent_team_abbr"],
                    "selection_key": (best_odds or {}).get("selection_key"),
                    "side": "YES",
                    "bet_type": BET_TYPE_DEFAULT,
                    "line": None,
                    "model_score": round(model_score, 2),
                    "model_prob": round(model_prob, 4),
                    "model_projection": None,
                    "book_implied_prob": round(book_prob, 4) if book_prob is not None else None,
                    "edge": round(edge_pct, 3) if edge_pct is not None else None,
                    "signal": assign_signal(MARKET, model_score, edge_pct),
                    "factors_json": factors,
                    "reasons_json": reasons,
                    "risk_flags_json": risk_flags,
                    "lineup_confirmed": 1 if (lineups_confirmed and lineup_confirmed_player) else 0,
                    "weather_final": 1 if (context and context.get("weather_temp_f") is not None) else 0,
                }
            )
        else:
            # No odds — emit YES row with score-only signal
            results.append(
                {
                    "market": MARKET,
                    "entity_type": "batter",
                    "game_id": game.game_id,
                    "event_id": None,
                    "player_id": player_id,
                    "player_name": None,
                    "team_id": batting_team,
                    "opponent_team_id": entity["opponent_team_id"],
                    "team_abbr": entity["team_abbr"],
                    "opponent_team_abbr": entity["opponent_team_abbr"],
                    "selection_key": None,
                    "side": "YES",
                    "bet_type": BET_TYPE_DEFAULT,
                    "line": None,
                    "model_score": round(model_score, 2),
                    "model_prob": round(model_prob, 4),
                    "model_projection": None,
                    "book_implied_prob": None,
                    "edge": None,
                    "signal": assign_signal(MARKET, model_score, None),
                    "factors_json": factors,
                    "reasons_json": reasons,
                    "risk_flags_json": risk_flags,
                    "lineup_confirmed": 1 if (lineups_confirmed and lineup_confirmed_player) else 0,
                    "weather_final": 1 if (context and context.get("weather_temp_f") is not None) else 0,
                }
            )

    return results
