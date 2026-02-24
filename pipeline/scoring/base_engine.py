
"""
Market-agnostic scoring engine.

Each market module implements:
- MARKET (str)
- BET_TYPE_DEFAULT (str)
- compute_projection_and_factors(game_ctx) -> dict with:
    - model_prob (float|None)
    - model_projection (float|None)
    - factors (dict[str, float])  # each 0-100
    - line (float|None)
    - bet_type (str)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

import numpy as np

from db.database import query, upsert_many


@dataclass
class GameContext:
    game_id: int
    game_date: str
    home_team: str
    away_team: str
    home_pitcher_id: Optional[int]
    home_pitcher_name: Optional[str]
    away_pitcher_id: Optional[int]
    away_pitcher_name: Optional[str]
    stadium_id: Optional[int]


def percentile_rank(values: list[float], x: float) -> float:
    """Return percentile rank (0-100)."""
    if not values:
        return 50.0
    arr = np.array([v for v in values if v is not None], dtype=float)
    if arr.size == 0:
        return 50.0
    return float((arr < x).mean() * 100.0)


def implied_prob_from_american(american: int) -> float:
    """Convert American odds to implied probability."""
    if american is None:
        return None
    if american > 0:
        return 100.0 / (american + 100.0)
    return (-american) / ((-american) + 100.0)


def american_to_decimal(american: int) -> float:
    if american is None:
        return None
    if american > 0:
        return 1.0 + (american / 100.0)
    return 1.0 + (100.0 / (-american))


def get_best_hr_odds(game_id: int, player_id: int) -> dict[str, Any] | None:
    """Return best available HR 'over' odds for the player (highest decimal)."""
    rows = query(
        """
        SELECT sportsbook, over_price, implied_prob_over, fetch_time
        FROM hr_odds
        WHERE game_id=? AND player_id=? AND over_price IS NOT NULL
        ORDER BY fetch_time DESC
        """,
        (game_id, player_id),
    )
    if not rows:
        return None
    best = None
    for r in rows:
        dec = american_to_decimal(int(r["over_price"]))
        if dec is None:
            continue
        if best is None or dec > best["odds_decimal"]:
            best = {
                "sportsbook": r["sportsbook"],
                "over_price": int(r["over_price"]),
                "odds_decimal": dec,
                "implied_prob": float(r["implied_prob_over"]) if r["implied_prob_over"] is not None else implied_prob_from_american(int(r["over_price"])),
                "fetch_time": r["fetch_time"],
            }
    return best


def save_model_scores(rows: list[dict]) -> int:
    return upsert_many(
        "model_scores",
        rows,
        conflict_cols=["market", "game_id", "player_id", "team_abbr", "bet_type", "line"],
    )


def load_today_games(game_date: str) -> list[GameContext]:
    games = query(
        """
        SELECT game_id, game_date, home_team, away_team, home_pitcher_id, home_pitcher_name,
               away_pitcher_id, away_pitcher_name, stadium_id
        FROM games
        WHERE game_date=?
        """,
        (game_date,),
    )
    return [
        GameContext(
            game_id=g["game_id"],
            game_date=g["game_date"],
            home_team=g["home_team"],
            away_team=g["away_team"],
            home_pitcher_id=g.get("home_pitcher_id"),
            home_pitcher_name=g.get("home_pitcher_name"),
            away_pitcher_id=g.get("away_pitcher_id"),
            away_pitcher_name=g.get("away_pitcher_name"),
            stadium_id=g.get("stadium_id"),
        )
        for g in games
    ]


def get_weather(game_id: int) -> dict[str, Any] | None:
    rows = query(
        "SELECT * FROM weather WHERE game_id=? ORDER BY fetch_time DESC LIMIT 1",
        (game_id,),
    )
    return rows[0] if rows else None


def get_park_factor(stadium_id: int, season: int) -> float:
    if stadium_id is None:
        return 1.0
    rows = query(
        "SELECT hr_factor FROM park_factors WHERE stadium_id=? AND season=?",
        (stadium_id, season),
    )
    if rows and rows[0]["hr_factor"] is not None:
        return float(rows[0]["hr_factor"])
    # fallback to stadium default
    rows2 = query("SELECT hr_park_factor FROM stadiums WHERE stadium_id=?", (stadium_id,))
    if rows2 and rows2[0]["hr_park_factor"] is not None:
        return float(rows2[0]["hr_park_factor"])
    return 1.0


def score_market_for_date(
    market_module,
    game_date: str,
    season: int,
    only_game_id: int | None = None,
) -> int:
    """
    Run a market module's scoring for all games on a date.
    market_module must provide compute_projection_and_factors(ctx, side) where side is 'home' or 'away' for player markets.
    """
    games = load_today_games(game_date)
    if only_game_id is not None:
        games = [g for g in games if g.game_id == only_game_id]

    total_saved = 0
    for g in games:
        weather = get_weather(g.game_id)
        park = get_park_factor(g.stadium_id, season)

        # For player markets we need both teams' batters; for team/game markets module decides.
        results = market_module.score_game(g, weather=weather, park_factor=park, season=season)
        if not results:
            continue
        total_saved += save_model_scores(results)

    return total_saved
