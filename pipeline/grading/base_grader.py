"""
Shared settlement and payout helpers.
"""
from __future__ import annotations

from typing import Any


SUPPORTED_PLAYER_PROP_MARKETS = {"HR", "K", "HITS_1P", "HITS_LINE", "TB_LINE", "OUTS_RECORDED"}
SUPPORTED_GAME_MARKETS = {"ML", "TOTAL", "F5_ML", "F5_TOTAL", "TEAM_TOTAL"}
SUPPORTED_MARKETS = SUPPORTED_PLAYER_PROP_MARKETS | SUPPORTED_GAME_MARKETS


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_side(side: str | None, market: str, bet_type: str | None = None) -> str:
    raw = (side or "").upper().strip()
    if raw:
        return raw
    bet_type_up = (bet_type or "").upper()
    if market == "HR":
        if "NO" in bet_type_up or "UNDER" in bet_type_up:
            return "NO"
        return "YES"
    if "UNDER" in bet_type_up:
        return "UNDER"
    if "OVER" in bet_type_up:
        return "OVER"
    if "AWAY" in bet_type_up:
        return "AWAY"
    if "HOME" in bet_type_up:
        return "HOME"
    return ""


def settle_selection(
    *,
    market: str,
    side: str | None,
    line: float | None,
    outcome_value: float | None,
    bet_type: str | None = None,
) -> str:
    """
    Return one of: win, loss, push, pending.
    """
    if outcome_value is None:
        return "pending"
    normalized_side = _normalize_side(side, market, bet_type)
    value = _to_float(outcome_value)
    threshold = _to_float(line)

    if normalized_side in {"OVER", "UNDER"}:
        if threshold is None or value is None:
            return "pending"
        if value > threshold:
            return "win" if normalized_side == "OVER" else "loss"
        if value < threshold:
            return "win" if normalized_side == "UNDER" else "loss"
        return "push"

    if normalized_side in {"YES", "NO"}:
        if value is None:
            return "pending"
        yes_hit = value >= 1.0
        return "win" if (yes_hit and normalized_side == "YES") or ((not yes_hit) and normalized_side == "NO") else "loss"

    if normalized_side in {"HOME", "AWAY"}:
        # For ML/F5_ML we store 1 for home win, 0 for away win, 0.5 for tie.
        if value is None:
            return "pending"
        if value == 0.5:
            return "push"
        if normalized_side == "HOME":
            return "win" if value == 1.0 else "loss"
        return "win" if value == 0.0 else "loss"

    return "pending"


def payout_for_settlement(
    *,
    stake: float | None,
    american_odds: int | float | None,
    settlement: str,
) -> tuple[float | None, float | None]:
    """
    Returns: (payout, profit)
    """
    if stake is None:
        return None, None
    stake_value = float(stake)
    if settlement in {"pending"}:
        return None, None
    if settlement in {"push", "void"}:
        return round(stake_value, 4), 0.0
    if settlement == "loss":
        return 0.0, round(-stake_value, 4)
    if settlement != "win":
        return None, None

    odds = _to_float(american_odds)
    if odds is None or odds == 0:
        return None, None
    if odds > 0:
        profit = stake_value * (odds / 100.0)
    else:
        profit = stake_value * (100.0 / abs(odds))
    payout = stake_value + profit
    return round(payout, 4), round(profit, 4)


def build_outcome_row(selection: dict[str, Any], outcome_value: float | None, outcome_text: str | None) -> dict[str, Any]:
    return {
        "game_date": selection.get("game_date"),
        "event_id": selection.get("event_id"),
        "market": selection.get("market"),
        "game_id": selection.get("game_id"),
        "entity_type": selection.get("entity_type"),
        "player_id": selection.get("player_id"),
        "team_id": selection.get("team_id"),
        "opponent_team_id": selection.get("opponent_team_id"),
        "team_abbr": selection.get("team_abbr"),
        "selection_key": selection.get("selection_key"),
        "side": selection.get("side"),
        "bet_type": selection.get("bet_type"),
        "line": selection.get("line"),
        "outcome_value": outcome_value,
        "outcome_text": outcome_text,
        "settled_at": None,
    }
