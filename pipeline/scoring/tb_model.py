"""
Total bases props model.

Supports TB line props and alternate ladder lines through the same TB_LINE market.
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
    get_batter_universe,
    get_market_odds_rows,
    probability_edge_pct,
    projection_edge_pct,
)


MARKET = "TB_LINE"
BET_TYPE_DEFAULT = "TB_LINE"


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


def _player_features(game_date: str, player_id: int) -> dict[str, Any] | None:
    rows = query(
        """
        SELECT *
        FROM mlb_batter_daily_features
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


def _lineup_order(game_date: str, game_id: int, player_id: int) -> tuple[int | None, int]:
    rows = query(
        """
        SELECT batting_order, confirmed
        FROM mlb_lineups
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


def _infer_opp_pitcher(game: GameContext, batting_team: str | None) -> int | None:
    if batting_team is None:
        return None
    if batting_team == game.home_team:
        return game.away_pitcher_id
    if batting_team == game.away_team:
        return game.home_pitcher_id
    return None


def _pa_expect(batting_order: int | None) -> float:
    """Expected PA by lineup slot — refined with actual MLB averages."""
    if batting_order is None:
        return 4.05
    slot = int(batting_order)
    pa_by_slot = {1: 4.8, 2: 4.7, 3: 4.55, 4: 4.45, 5: 4.3, 6: 4.15, 7: 4.0, 8: 3.85, 9: 3.75}
    return pa_by_slot.get(slot, 4.05)


def _score_from_factors(factors: dict[str, float]) -> float:
    weights = {
        "power_form_score": 0.24,
        "tb_rate_score": 0.20,
        "pitcher_damage_allow_score": 0.14,
        "batting_order_score": 0.12,
        "park_weather_score": 0.10,
        "xbh_profile_score": 0.08,
        "tto_score": 0.07,
        "day_night_score": 0.05,
    }
    return max(0.0, min(100.0, sum(factors.get(k, 50.0) * w for k, w in weights.items())))


def score_game(game: GameContext, weather: dict | None, park_factor: float, season: int) -> list[dict]:
    del weather, season

    # Build player universe from features (not odds)
    universe = get_batter_universe(game.game_date, game)
    if not universe:
        return []

    context = _context(game.game_date, game.game_id)
    weather_mult = _to_float((context or {}).get("weather_run_multiplier")) or 1.0
    hr_mult = _to_float((context or {}).get("weather_hr_multiplier")) or 1.0
    is_day_game = (context or {}).get("is_day_game")
    lineups_confirmed = bool((context or {}).get("lineups_confirmed_home") and (context or {}).get("lineups_confirmed_away"))

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

        opp_pitcher = _pitcher_features(game.game_date, _infer_opp_pitcher(game, batting_team))
        batting_order, lineup_confirmed_player = _lineup_order(game.game_date, game.game_id, player_id)

        tb_pa_14 = _to_float(batter.get("tb_per_pa_14"))
        tb_pa_30 = _to_float(batter.get("tb_per_pa_30"))
        iso_14 = _to_float(batter.get("iso_14"))
        slg_14 = _to_float(batter.get("slg_14"))
        doubles_rate = _to_float(batter.get("doubles_rate_14")) or _to_float(batter.get("doubles_rate_30"))
        triples_rate = _to_float(batter.get("triples_rate_14")) or _to_float(batter.get("triples_rate_30"))
        hr_rate = _to_float(batter.get("hr_rate_14"))

        if tb_pa_14 is None and tb_pa_30 is None:
            continue

        base_tb_rate = (0.6 * (tb_pa_14 if tb_pa_14 is not None else 0.0)) + (0.4 * (tb_pa_30 if tb_pa_30 is not None else 0.0))
        base_tb_rate = max(0.10, min(0.95, base_tb_rate))

        pitcher_penalty = 0.0
        if opp_pitcher:
            ev_allow = _to_float(opp_pitcher.get("avg_exit_velo_allowed_14")) or 89.0
            hard_hit_allow = _to_float(opp_pitcher.get("hard_hit_pct_allowed_14")) or 35.0
            pitcher_penalty = ((89.0 - ev_allow) * 0.002) + ((35.0 - hard_hit_allow) * 0.003)

        pa = _pa_expect(batting_order)
        if lineup_confirmed_player == 0:
            pa *= 0.95
        env_mult = max(0.85, min(1.2, weather_mult * hr_mult * park_factor))
        adjusted_tb_rate = max(0.08, min(1.10, (base_tb_rate - pitcher_penalty) * env_mult))
        projection = max(0.1, min(6.0, adjusted_tb_rate * pa))

        # Compute factors (independent of odds)
        power_form_score = 50.0 + (((iso_14 or 0.16) - 0.16) * 260.0) + (((slg_14 or 0.4) - 0.4) * 120.0)
        tb_rate_score = 50.0 + ((base_tb_rate - 0.42) * 150.0)
        pitcher_damage_allow_score = 50.0
        if opp_pitcher:
            pitcher_damage_allow_score += ((_to_float(opp_pitcher.get("hard_hit_pct_allowed_14")) or 35.0) - 35.0) * 1.4
            pitcher_damage_allow_score += ((_to_float(opp_pitcher.get("barrel_pct_allowed_14")) or 8.5) - 8.5) * 2.0
        order_scores = {1: 72, 2: 78, 3: 85, 4: 82, 5: 70, 6: 58, 7: 45, 8: 35, 9: 28}
        batting_order_score = float(order_scores.get(batting_order or 5, 50))
        park_weather_score = 50.0 + ((env_mult - 1.0) * 180.0)
        xbh_profile_score = 50.0 + ((doubles_rate or 0.05) * 200.0) + ((triples_rate or 0.005) * 400.0) + ((hr_rate or 0.04) * 250.0)

        tto_score = 50.0
        if opp_pitcher and opp_pitcher.get("tto_endurance_score") is not None:
            tto_score = 100.0 - float(opp_pitcher["tto_endurance_score"])

        day_night_score = 50.0
        if is_day_game == 1:
            day_night_score = 56.0
        elif is_day_game == 0:
            day_night_score = 47.0

        factors = {
            "power_form_score": max(0.0, min(100.0, power_form_score)),
            "tb_rate_score": max(0.0, min(100.0, tb_rate_score)),
            "pitcher_damage_allow_score": max(0.0, min(100.0, pitcher_damage_allow_score)),
            "batting_order_score": max(0.0, min(100.0, batting_order_score)),
            "park_weather_score": max(0.0, min(100.0, park_weather_score)),
            "xbh_profile_score": max(0.0, min(100.0, xbh_profile_score)),
            "tto_score": max(0.0, min(100.0, tto_score)),
            "day_night_score": max(0.0, min(100.0, day_night_score)),
        }
        model_score = _score_from_factors(factors)
        risk_flags = build_risk_flags(
            missing_inputs=[] if opp_pitcher is not None else ["opposing_pitcher_features"],
            lineup_pending=not bool(lineups_confirmed and lineup_confirmed_player),
            weather_pending=context is None,
        )
        reasons = build_reasons(factors)

        # Optionally enrich with odds if available
        player_odds = odds_by_player.get(player_id, [])
        if player_odds:
            for odds in player_odds:
                line = _to_float(odds.get("line"))
                threshold = int(math.floor(line if line is not None else 0.5))
                prob_over = 1.0 - _poisson_prob_at_most(threshold, projection)
                side = (odds.get("side") or "OVER").upper()
                model_prob = prob_over if side == "OVER" else (1.0 - prob_over)

                implied_prob = _to_float(odds.get("implied_probability"))
                edge_prob = probability_edge_pct(model_prob, implied_prob)
                edge_proj = projection_edge_pct(projection, line)
                edge_pct = edge_prob if edge_prob is not None else edge_proj

                results.append(
                    {
                        "market": MARKET,
                        "entity_type": "batter",
                        "game_id": game.game_id,
                        "event_id": odds.get("event_id"),
                        "player_id": player_id,
                        "player_name": odds.get("player_name"),
                        "team_id": batting_team,
                        "opponent_team_id": entity["opponent_team_id"],
                        "team_abbr": odds.get("team_abbr") or entity["team_abbr"],
                        "opponent_team_abbr": odds.get("opponent_team_abbr") or entity["opponent_team_abbr"],
                        "selection_key": odds.get("selection_key"),
                        "side": side,
                        "bet_type": odds.get("bet_type") or f"TB_LINE_{side}",
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
                        "lineup_confirmed": 1 if (lineups_confirmed and lineup_confirmed_player) else 0,
                        "weather_final": 1 if (context and context.get("weather_temp_f") is not None) else 0,
                    }
                )
        else:
            # No odds — emit OVER row with projection-based default line
            default_line = round(projection * 2.0) / 2.0
            threshold = int(math.floor(default_line))
            prob_over = 1.0 - _poisson_prob_at_most(threshold, projection)

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
                    "side": "OVER",
                    "bet_type": "TB_LINE_OVER",
                    "line": default_line,
                    "model_score": round(model_score, 2),
                    "model_prob": round(prob_over, 4),
                    "model_projection": round(projection, 3),
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
