
"""
HR market scoring module.

Uses:
- batter_stats (window 14, plus 7/30 for hot_cold)
- pitcher_stats (window 14)
- weather
- park factors
- hr_odds (best available odds per player)
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from config import HR_FACTOR_WEIGHTS, SIGNAL_THRESHOLDS
from db.database import query
from .base_engine import GameContext, get_best_hr_odds, percentile_rank


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


def score_game(game: GameContext, weather: dict | None, park_factor: float, season: int) -> list[dict]:
    """
    Score all HR-prop players for this game based on hr_odds rows.
    """
    # HR props define the "player universe" for this game/date
    players = query(
        """
        SELECT DISTINCT player_id, player_name
        FROM hr_odds
        WHERE game_id=? AND game_date=?
        """,
        (game.game_id, game.game_date),
    )
    if not players:
        return []

    # Preload distributions for percentile ranks
    barrel_vals = _all_batter_values(game.game_date, 14, "barrel_pct")
    iso_vals = _all_batter_values(game.game_date, 14, "iso_power")
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
    for p in players:
        pid = int(p["player_id"])
        pname = p["player_name"]

        batter14 = _get_batter_stat(pid, game.game_date, 14)
        batter7 = _get_batter_stat(pid, game.game_date, 7)
        batter30 = _get_batter_stat(pid, game.game_date, 30)
        if not batter14:
            continue

        # Determine opposing pitcher based on team
        # We don't have batter team reliably; use odds table only has name.
        # Fallback: score against both pitchers? We'll use both and take worse matchup (conservative).
        opp_pitchers = []
        if game.home_pitcher_id:
            opp_pitchers.append(("home", game.home_pitcher_id, game.home_pitcher_name, game.home_pitcher_hand))
        if game.away_pitcher_id:
            opp_pitchers.append(("away", game.away_pitcher_id, game.away_pitcher_name, game.away_pitcher_hand))

        matchup_scores = []
        pitcher_vuln_scores = []
        for _, opp_id, _, opp_hand in opp_pitchers:
            pit14 = _get_pitcher_stat(opp_id, game.game_date, 14)
            # matchup: batter ISO split vs pitcher hand + pitcher HR/9
            iso_split = None
            if opp_hand == "L":
                iso_split = batter14.get("iso_vs_lhp")
            elif opp_hand == "R":
                iso_split = batter14.get("iso_vs_rhp")
            # normalize iso_split ~ 0.120 poor to 0.280 elite
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

        # composite score
        factors = {
            "barrel_score": float(barrel_score),
            "matchup_score": float(matchup_score),
            "park_weather_score": float(park_weather_score),
            "pitcher_vuln_score": float(pitcher_vuln_score),
            "hot_cold_score": float(hot_cold_score),
        }
        composite = 0.0
        for k, w in HR_FACTOR_WEIGHTS.items():
            composite += factors.get(k, 50.0) * float(w)

        # odds + edge
        odds = get_best_hr_odds(game.game_id, pid)
        book_prob = float(odds["implied_prob"]) if odds and odds.get("implied_prob") is not None else None

        # model probability: map 0-100 score to 0.02-0.35 (rough HR range)
        model_prob = 0.02 + (composite / 100.0) * 0.33
        edge = (model_prob - book_prob) if (book_prob is not None) else None
        sig = _signal(composite, edge)

        results.append(
            {
                "market": MARKET,
                "game_id": game.game_id,
                "game_date": game.game_date,
                "player_id": pid,
                "player_name": pname,
                "team_abbr": None,
                "opponent_team_abbr": None,
                "bet_type": BET_TYPE_DEFAULT,
                "line": None,
                "model_score": float(round(composite, 2)),
                "model_prob": float(round(model_prob, 4)),
                "model_projection": None,
                "book_implied_prob": float(round(book_prob, 4)) if book_prob is not None else None,
                "edge": float(round(edge, 4)) if edge is not None else None,
                "signal": sig,
                "factors_json": json.dumps({**factors, "odds": odds, "park_weather_mult": park_weather_mult}),
            }
        )

    return results
