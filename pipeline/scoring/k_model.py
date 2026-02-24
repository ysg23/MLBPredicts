
"""
Strikeouts (K) market scoring module (initial version).

Scores starting pitchers using pitcher_stats (14/30d) + simple context.
Once team K% and pitcher prop odds are wired, edge calculations will be enabled.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from config import K_FACTOR_WEIGHTS, SIGNAL_THRESHOLDS
from db.database import query
from .base_engine import GameContext, percentile_rank


MARKET = "K"
BET_TYPE_DEFAULT = "K_OVER"  # placeholder until we fetch actual prop lines


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


def _scale_between(x: float, lo: float, hi: float) -> float:
    if x is None:
        return 50.0
    if hi == lo:
        return 50.0
    return _clamp((x - lo) / (hi - lo) * 100.0)


def _signal(score: float, edge: float | None) -> str:
    edge = edge if edge is not None else 0.0
    if score >= SIGNAL_THRESHOLDS["BET"]["min_score"] and edge >= SIGNAL_THRESHOLDS["BET"]["min_edge"]:
        return "BET"
    if score >= SIGNAL_THRESHOLDS["LEAN"]["min_score"] and edge >= SIGNAL_THRESHOLDS["LEAN"]["min_edge"]:
        return "LEAN"
    if score <= SIGNAL_THRESHOLDS["FADE"]["max_score"] and edge <= SIGNAL_THRESHOLDS["FADE"]["max_edge"]:
        return "FADE"
    return "SKIP"


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


def _all_pitcher_values(stat_date: str, window: int, col: str) -> list[float]:
    rows = query(
        f"SELECT {col} AS v FROM pitcher_stats WHERE stat_date=? AND window_days=? AND {col} IS NOT NULL",
        (stat_date, window),
    )
    return [float(r["v"]) for r in rows]


def score_game(game: GameContext, weather: dict | None, park_factor: float, season: int) -> list[dict]:
    # Starting pitchers for this game (both sides)
    starters = []
    if game.home_pitcher_id:
        starters.append((game.home_pitcher_id, game.home_pitcher_name, game.home_team, game.away_team))
    if game.away_pitcher_id:
        starters.append((game.away_pitcher_id, game.away_pitcher_name, game.away_team, game.home_team))
    if not starters:
        return []

    k_pct_vals = _all_pitcher_values(game.game_date, 14, "k_pct")
    whiff_vals = _all_pitcher_values(game.game_date, 14, "whiff_pct")
    chase_vals = _all_pitcher_values(game.game_date, 14, "chase_pct")

    # Context: colder temps can slightly help Ks; wind/park irrelevant mostly
    temp = float(weather["temperature_f"]) if weather and weather.get("temperature_f") is not None else None
    if temp is None:
        context_score = 50.0
    else:
        # 40F -> 60, 70F -> 50, 90F -> 45
        context_score = _clamp(60 - (max(0.0, min(50.0, temp - 40.0)) / 50.0) * 15)

    results = []
    for pid, pname, team, opp in starters:
        pit14 = _get_pitcher_stat(pid, game.game_date, 14)
        pit30 = _get_pitcher_stat(pid, game.game_date, 30)
        if not pit14:
            continue

        k_form = percentile_rank(k_pct_vals, float(pit14["k_pct"])) if pit14.get("k_pct") is not None else 50.0

        whiff = percentile_rank(whiff_vals, float(pit14["whiff_pct"])) if pit14.get("whiff_pct") is not None else 50.0
        chase = percentile_rank(chase_vals, float(pit14["chase_pct"])) if pit14.get("chase_pct") is not None else 50.0
        whiff_chase = 0.6 * whiff + 0.4 * chase

        # Pitch count / role security proxy: batters faced in last 14 days
        bf = float(pit14["batters_faced"]) if pit14.get("batters_faced") is not None else None
        pitch_count_role = _scale_between(bf, 40, 120)  # low->40bf, high usage->120bf

        # Contact quality: higher EV/hard-hit can reduce Ks slightly (hard contact often implies more balls in play)
        ev = float(pit14["avg_exit_velo_against"]) if pit14.get("avg_exit_velo_against") is not None else None
        contact_quality = 100.0 - _scale_between(ev, 85, 95) if ev is not None else 50.0

        factors = {
            "k_form_score": float(k_form),
            "whiff_chase_score": float(whiff_chase),
            "pitch_count_role_score": float(pitch_count_role),
            "contact_quality_score": float(contact_quality),
            "context_score": float(context_score),
        }
        composite = sum(factors[k] * float(w) for k, w in K_FACTOR_WEIGHTS.items())

        # projection (very rough): map composite to 3.5 - 9.0 Ks
        proj_k = 3.5 + (composite / 100.0) * 5.5

        results.append(
            {
                "market": MARKET,
                "game_id": game.game_id,
                "game_date": game.game_date,
                "player_id": int(pid),
                "player_name": pname,
                "team_abbr": team,
                "opponent_team_abbr": opp,
                "bet_type": BET_TYPE_DEFAULT,
                "line": None,
                "model_score": float(round(composite, 2)),
                "model_prob": None,
                "model_projection": float(round(proj_k, 2)),
                "book_implied_prob": None,
                "edge": None,
                "signal": _signal(composite, None),
                "factors_json": json.dumps({**factors, "notes": "Odds/lines not wired yet for K props"}),
            }
        )

    return results
