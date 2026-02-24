
"""
First 5 Totals (F5 O/U) market scoring module (scaffold)

This module is scaffolded for the multi-market engine.
It currently returns no scores until the supporting data fetchers (team_offense, bullpen, market odds/lines) are wired.

Next steps:
- Add fetchers/team_stats.py (team hitting splits, bullpen metrics)
- Extend fetchers/odds.py to pull ML/TOTAL/F5 markets into market_odds
- Implement projections + edge calculation
"""

from __future__ import annotations
from .base_engine import GameContext

MARKET = "F5_TOTAL"
BET_TYPE_DEFAULT = "F5_TOTAL"

def score_game(game: GameContext, weather: dict | None, park_factor: float, season: int) -> list[dict]:
    return []
