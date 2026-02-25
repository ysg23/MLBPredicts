
"""
HR market scoring module.

Uses:
- batter_stats (window 14, plus 7/30 for hot_cold)
- pitcher_stats (window 14)
- weather
- park factors
- market_odds (best available odds per player, market='HR')
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from config import HR_FACTOR_WEIGHTS, SIGNAL_THRESHOLDS
from db.database import query
from .base_engine import (
    GameContext,
    choose_best_odds_row,
    get_market_odds_rows,
    implied_prob_from_american,
    percentile_rank,
)


MARKET = "HR"
BET_TYPE_DEFAULT = "HR_1PLUS"


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


def _scale_between(x: float, lo: float, hi: float) -> float:
    if x is None:
        return 50.0
    if hi == lo:
        return 50.0
    return _clamp((x - lo) / (hi - lo) * 100.0)


def _get_batter_stat(player_id: int, stat_date: str, window: int) -> dict[str, Any] | None:
    rows = query(
        """
        SELECT * FROM batter_stats
        WHERE player_id=? AND stat_date=? AND window_days=?
        """,
        (player_id, stat_date, window),
    )
    return rows[0] if rows else None


def _get_pitcher_stat(player_id: int, stat_date: str, window: int) -> dict[str, Any] | None:
    if player_id is None:
        return None
    rows = query(
        """
        SELECT * FROM pitcher_stats
        WHERE player_id=? AND stat_date=? AND window_days=?
        """,
        (player_id, stat_date, window),
    )
    return rows[0] if rows else None


def _all_batter_values(stat_date: str, window: int, col: str) -> list[float]:
    rows = query(
        f"SELECT {col} AS v FROM batter_stats WHERE stat_date=? AND window_days=? AND {col} IS NOT NULL",
        (stat_date, window),
    )
    return [float(r["v"]) for r in rows]


def _all_pitcher_values(stat_date: str, window: int, col: str) -> list[float]:
    rows = query(
        f"SELECT {col} AS v FROM pitcher_stats WHERE stat_date=? AND window_days=? AND {col} IS NOT NULL",
        (stat_date, window),
    )
    return [float(r["v"]) for r in rows]


def _signal(score: float, edge: float | None) -> str:
    # Defaults if odds missing
    edge = edge if edge is not None else 0.0
    if score >= SIGNAL_THRESHOLDS["BET"]["min_score"] and edge >= SIGNAL_THRESHOLDS["BET"]["min_edge"]:
        return "BET"
    if score >= SIGNAL_THRESHOLDS["LEAN"]["min_score"] and edge >= SIGNAL_THRESHOLDS["LEAN"]["min_edge"]:
        return "LEAN"
    if score <= SIGNAL_THRESHOLDS["FADE"]["max_score"] and edge <= SIGNAL_THRESHOLDS["FADE"]["max_edge"]:
        return "FADE"
    return "SKIP"


def _get_player_name(player_id: int) -> str | None:
    rows = query(
        "SELECT player_name FROM batter_stats WHERE player_id=? LIMIT 1",
        (player_id,),
    )
    return rows[0]["player_name"] if rows else None


def _get_game_context(game_date: str, game_id: int) -> dict[str, Any] | None:
    rows = query(
        "SELECT * FROM game_context_features WHERE game_date = ? AND game_id = ? LIMIT 1",
        (game_date, game_id),
    )
    return rows[0] if rows else None


def _get_pitcher_features(game_date: str, pitcher_id: int | None) -> dict[str, Any] | None:
    if pitcher_id is None:
        return None
    rows = query(
        "SELECT * FROM pitcher_daily_features WHERE game_date = ? AND pitcher_id = ? LIMIT 1",
        (game_date, pitcher_id),
    )
    return rows[0] if rows else None


def _get_batting_order(game_date: str, game_id: int, player_id: int) -> int | None:
    rows = query(
        """
        SELECT batting_order FROM lineups
        WHERE game_date = ? AND game_id = ? AND player_id = ?
          AND COALESCE(active_version, 1) = 1
        ORDER BY fetched_at DESC LIMIT 1
        """,
        (game_date, game_id, player_id),
    )
    if rows and rows[0].get("batting_order") is not None:
        return int(rows[0]["batting_order"])
    return None


def _lineup_order_score(batting_order: int | None) -> float:
    """Score 0-100 based on batting order position. Higher = more PA, better protection."""
    if batting_order is None:
        return 50.0
    slot = int(batting_order)
    # Top of order gets more PA and better protection; heart of order gets RBI chances
    scores = {1: 72, 2: 78, 3: 85, 4: 82, 5: 70, 6: 58, 7: 45, 8: 35, 9: 28}
    return float(scores.get(slot, 50))


def _day_night_hr_adj(is_day_game: int | None) -> float:
    """Day games historically have slightly higher HR rates due to visibility/shadows.

    Day game HR rate is ~4-5% higher than night games league-wide.
    """
    if is_day_game is None:
        return 0.0
    return 0.02 if is_day_game == 1 else -0.01


def _tto_hr_adj(pitcher_features: dict[str, Any] | None, batting_order: int | None) -> float:
    """Adjust HR probability based on pitcher TTO vulnerability.

    Batters in slots 3-6 face the pitcher on their 2nd/3rd time through,
    when TTO decay is most pronounced.
    """
    if pitcher_features is None:
        return 0.0
    tto_hr_inc = pitcher_features.get("tto_hr_increase_pct")
    if tto_hr_inc is None:
        return 0.0
    # Batters 3-6 are most likely facing pitcher on 2nd+ time through
    if batting_order is not None and 3 <= batting_order <= 6:
        return (float(tto_hr_inc) - 40.0) * 0.001  # above avg TTO decay = boost
    return (float(tto_hr_inc) - 40.0) * 0.0005


def _determine_opposing_pitcher(game: GameContext, batter_team: str | None):
    """Return (pitcher_id, pitcher_name, pitcher_hand) for the opposing pitcher."""
    if batter_team is None:
        return None, None, None
    if batter_team == game.home_team:
        return game.away_pitcher_id, game.away_pitcher_name, game.away_pitcher_hand
    if batter_team == game.away_team:
        return game.home_pitcher_id, game.home_pitcher_name, game.home_pitcher_hand
    return None, None, None


def score_game(game: GameContext, weather: dict | None, park_factor: float, season: int) -> list[dict]:
    """
    Score all HR-prop players for this game based on market_odds rows.
    """
    # Build player universe from market_odds (HR market for this game)
    odds_rows = get_market_odds_rows(
        game_date=game.game_date, market="HR", game_id=game.game_id,
    )
    if not odds_rows:
        return []

    # Deduplicate to unique players
    player_info: dict[int, dict[str, Any]] = {}
    for row in odds_rows:
        pid = row.get("player_id")
        if pid is None:
            continue
        pid = int(pid)
        if pid not in player_info:
            player_info[pid] = {
                "team_abbr": row.get("team_abbr") or row.get("team_id"),
                "opponent_team_abbr": row.get("opponent_team_abbr") or row.get("opponent_team_id"),
            }

    if not player_info:
        return []

    # Load game context for day/night and enhanced features
    context = _get_game_context(game.game_date, game.game_id)
    is_day_game = (context or {}).get("is_day_game")

    # Preload distributions for percentile ranks
    barrel_vals = _all_batter_values(game.game_date, 14, "barrel_pct")
    pitcher_hr9_vals = _all_pitcher_values(game.game_date, 14, "hr_per_9")
    pitcher_barrel_vals = _all_pitcher_values(game.game_date, 14, "barrel_pct_against")

    wind_mult = float(weather["wind_hr_impact"]) if weather and weather.get("wind_hr_impact") is not None else 1.0
    temp = float(weather["temperature_f"]) if weather and weather.get("temperature_f") is not None else None
    # simple temp multiplier: 50F->0.95, 70F->1.0, 90F->1.07
    if temp is None:
        temp_mult = 1.0
    else:
        temp_mult = 0.95 + (max(0.0, min(40.0, temp - 50.0)) / 40.0) * 0.12

    park_weather_mult = park_factor * wind_mult * temp_mult
    park_weather_score = _scale_between(park_weather_mult, 0.85, 1.20)

    results: list[dict] = []
    for pid, info in player_info.items():
        pname = _get_player_name(pid) or f"Player {pid}"
        batter_team = info["team_abbr"]

        batter14 = _get_batter_stat(pid, game.game_date, 14)
        batter7 = _get_batter_stat(pid, game.game_date, 7)
        batter30 = _get_batter_stat(pid, game.game_date, 30)
        if not batter14:
            continue

        # Determine the actual opposing pitcher using team info from market_odds
        opp_pid, opp_name, opp_hand = _determine_opposing_pitcher(game, batter_team)

        # Enhanced features: lineup order, TTO, day/night
        batting_order = _get_batting_order(game.game_date, game.game_id, pid)
        opp_pitcher_feats = _get_pitcher_features(game.game_date, opp_pid)
        lineup_score = _lineup_order_score(batting_order)
        day_night_adj = _day_night_hr_adj(is_day_game)
        tto_adj = _tto_hr_adj(opp_pitcher_feats, batting_order)

        matchup_scores = []
        pitcher_vuln_scores = []

        if opp_pid is not None:
            pit14 = _get_pitcher_stat(opp_pid, game.game_date, 14)
            # matchup: batter ISO split vs pitcher hand + pitcher HR/9
            iso_split = None
            if opp_hand == "L":
                iso_split = batter14.get("iso_vs_lhp")
            elif opp_hand == "R":
                iso_split = batter14.get("iso_vs_rhp")
            iso_component = _scale_between(float(iso_split) if iso_split is not None else None, 0.10, 0.30)
            if pit14 and pit14.get("hr_per_9") is not None:
                hr9_pr = percentile_rank(pitcher_hr9_vals, float(pit14["hr_per_9"]))
            else:
                hr9_pr = 50.0
            matchup_scores.append(0.65 * iso_component + 0.35 * hr9_pr)

            # pitcher vulnerability: hr_per_9 + barrel% allowed
            if pit14:
                hr9 = float(pit14["hr_per_9"]) if pit14.get("hr_per_9") is not None else None
                brl = float(pit14["barrel_pct_against"]) if pit14.get("barrel_pct_against") is not None else None
                hr9_s = percentile_rank(pitcher_hr9_vals, hr9) if hr9 is not None else 50.0
                brl_s = percentile_rank(pitcher_barrel_vals, brl) if brl is not None else 50.0
                pitcher_vuln_scores.append(0.6 * hr9_s + 0.4 * brl_s)
            else:
                pitcher_vuln_scores.append(50.0)
        else:
            # No opposing pitcher identified; use neutral defaults
            matchup_scores.append(50.0)
            pitcher_vuln_scores.append(50.0)

        matchup_score = float(np.mean(matchup_scores)) if matchup_scores else 50.0
        pitcher_vuln_score = float(np.mean(pitcher_vuln_scores)) if pitcher_vuln_scores else 50.0

        # barrel score percentile
        barrel_score = percentile_rank(barrel_vals, float(batter14["barrel_pct"])) if batter14.get("barrel_pct") is not None else 50.0

        # hot/cold: 7-day ISO vs 30-day ISO
        if batter7 and batter30 and batter7.get("iso_power") is not None and batter30.get("iso_power") is not None:
            diff = float(batter7["iso_power"]) - float(batter30["iso_power"])
            hot_cold_score = _scale_between(diff, -0.08, 0.08)
        else:
            hot_cold_score = 50.0

        # TTO endurance score: high endurance = pitcher less vulnerable
        tto_score = 50.0
        if opp_pitcher_feats and opp_pitcher_feats.get("tto_endurance_score") is not None:
            # Invert: low endurance (pitcher degrades) = good for batter
            tto_score = 100.0 - float(opp_pitcher_feats["tto_endurance_score"])

        # composite score with enhanced factors
        factors = {
            "barrel_score": float(barrel_score),
            "matchup_score": float(matchup_score),
            "park_weather_score": float(park_weather_score),
            "pitcher_vuln_score": float(pitcher_vuln_score),
            "hot_cold_score": float(hot_cold_score),
            "lineup_order_score": float(lineup_score),
            "tto_score": float(tto_score),
        }
        # Enhanced weights: redistribute to include new factors
        enhanced_weights = {
            "barrel_score": 0.22,
            "matchup_score": 0.18,
            "park_weather_score": 0.20,
            "pitcher_vuln_score": 0.15,
            "hot_cold_score": 0.08,
            "lineup_order_score": 0.10,
            "tto_score": 0.07,
        }
        composite = 0.0
        for k, w in enhanced_weights.items():
            composite += factors.get(k, 50.0) * float(w)

        # Best available odds from market_odds
        player_odds_rows = get_market_odds_rows(
            game_date=game.game_date, market="HR", game_id=game.game_id, player_id=pid,
        )
        best_odds = choose_best_odds_row(player_odds_rows)
        book_prob = None
        odds_detail = None
        if best_odds:
            book_prob = (
                float(best_odds["implied_probability"])
                if best_odds.get("implied_probability") is not None
                else implied_prob_from_american(best_odds.get("price_american"))
            )
            odds_detail = {
                "sportsbook": best_odds.get("sportsbook"),
                "price_american": best_odds.get("price_american"),
                "price_decimal": best_odds.get("price_decimal"),
            }

        # model probability: map 0-100 score to 0.02-0.35 (rough HR range)
        model_prob = 0.02 + (composite / 100.0) * 0.33
        # Apply day/night and TTO probability adjustments
        model_prob = max(0.02, min(0.40, model_prob + day_night_adj + tto_adj))
        edge = (model_prob - book_prob) if (book_prob is not None) else None
        sig = _signal(composite, edge)

        results.append(
            {
                "market": MARKET,
                "game_id": game.game_id,
                "game_date": game.game_date,
                "player_id": pid,
                "player_name": pname,
                "team_abbr": batter_team,
                "opponent_team_abbr": info["opponent_team_abbr"],
                "bet_type": BET_TYPE_DEFAULT,
                "line": None,
                "model_score": float(round(composite, 2)),
                "model_prob": float(round(model_prob, 4)),
                "model_projection": None,
                "book_implied_prob": float(round(book_prob, 4)) if book_prob is not None else None,
                "edge": float(round(edge, 4)) if edge is not None else None,
                "signal": sig,
                "factors_json": json.dumps({
                    **factors,
                    "odds": odds_detail,
                    "park_weather_mult": park_weather_mult,
                }),
            }
        )

    return results
