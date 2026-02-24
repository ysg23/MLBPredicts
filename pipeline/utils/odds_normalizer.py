"""
Normalize The Odds API responses into a single internal market-odds shape.
"""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any


SUPPORTED_MARKET_MAP: dict[str, dict[str, str]] = {
    "batter_home_runs": {"market": "HR", "entity_type": "batter"},
    "pitcher_strikeouts": {"market": "K", "entity_type": "pitcher"},
    "pitcher_outs": {"market": "OUTS_RECORDED", "entity_type": "pitcher"},
    "pitcher_total_outs": {"market": "OUTS_RECORDED", "entity_type": "pitcher"},
    "batter_hits": {"market": "HITS_LINE", "entity_type": "batter"},
    "batter_hits_1_plus": {"market": "HITS_1P", "entity_type": "batter"},
    "batter_total_bases": {"market": "TB_LINE", "entity_type": "batter"},
    "h2h": {"market": "ML", "entity_type": "game"},
    "totals": {"market": "TOTAL", "entity_type": "game"},
    "h2h_1st_5_innings": {"market": "F5_ML", "entity_type": "game"},
    "totals_1st_5_innings": {"market": "F5_TOTAL", "entity_type": "game"},
    "team_totals": {"market": "TEAM_TOTAL", "entity_type": "team"},
}

SUPPORTED_ODDS_API_MARKETS: tuple[str, ...] = tuple(SUPPORTED_MARKET_MAP.keys())


def american_to_decimal(american: int | float | None) -> float | None:
    """Convert American odds to decimal odds."""
    if american is None:
        return None
    value = float(american)
    if value == 0:
        return None
    if value > 0:
        return 1.0 + (value / 100.0)
    return 1.0 + (100.0 / abs(value))


def american_to_implied_prob(american: int | float | None) -> float | None:
    """Convert American odds to implied probability (0-1)."""
    if american is None:
        return None
    value = float(american)
    if value == 0:
        return None
    if value > 0:
        return 100.0 / (value + 100.0)
    return abs(value) / (abs(value) + 100.0)


def decimal_to_implied_prob(decimal_odds: int | float | None) -> float | None:
    """Convert decimal odds to implied probability (0-1)."""
    if decimal_odds is None:
        return None
    value = float(decimal_odds)
    if value <= 1.0:
        return None
    return 1.0 / value


def _slug(value: str | None) -> str:
    cleaned = (value or "").strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "_", cleaned)
    cleaned = cleaned.strip("_")
    return cleaned or "unknown"


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date_from_commence(commence_time: str | None) -> str:
    if not commence_time:
        return datetime.utcnow().strftime("%Y-%m-%d")
    normalized = commence_time.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).strftime("%Y-%m-%d")
    except ValueError:
        return datetime.utcnow().strftime("%Y-%m-%d")


def _normalize_side(
    raw_name: str | None,
    home_team: str | None = None,
    away_team: str | None = None,
) -> str | None:
    name = (raw_name or "").strip().lower()
    if name == "over":
        return "OVER"
    if name == "under":
        return "UNDER"
    if name == "yes":
        return "YES"
    if name == "no":
        return "NO"
    if home_team and name == home_team.strip().lower():
        return "HOME"
    if away_team and name == away_team.strip().lower():
        return "AWAY"
    return None


def _effective_market(source_market_key: str, line: float | None) -> dict[str, str] | None:
    key = (source_market_key or "").strip().lower()
    base = SUPPORTED_MARKET_MAP.get(key)
    if not base:
        return None

    # Some books expose batter_hits with a 0.5 line that effectively means 1+ hit.
    if key == "batter_hits":
        if line is not None and line <= 0.5:
            return {"market": "HITS_1P", "entity_type": "batter"}
        return {"market": "HITS_LINE", "entity_type": "batter"}

    return base


def _line_token(line: float | None) -> str | None:
    if line is None:
        return None
    if line.is_integer():
        return str(int(line))
    return f"{line:.1f}".rstrip("0").rstrip(".")


def build_selection_key(
    *,
    market: str,
    entity_type: str,
    game_id: str | int | None,
    event_id: str | int | None,
    player_id: int | None,
    player_name: str | None,
    team_id: str | int | None,
    team_name: str | None,
    side: str | None,
    line: float | None,
) -> str:
    """Create stable normalized selection keys for all market/entity combinations."""
    game_ref = str(game_id or event_id or "unknown")
    normalized_side = side
    if market == "HR":
        if side == "OVER":
            normalized_side = "YES"
        elif side == "UNDER":
            normalized_side = "NO"

    if entity_type in {"batter", "pitcher"}:
        player_ref = str(player_id) if player_id is not None else f"name:{_slug(player_name)}"
        key = f"{market}|player:{player_ref}"
        if market != "HR":
            token = _line_token(line)
            if token is not None:
                key = f"{key}|line:{token}"
        if normalized_side:
            key = f"{key}|{normalized_side}"
        return key

    if entity_type == "team":
        team_ref = str(team_id) if team_id is not None else f"name:{_slug(team_name)}"
        key = f"{market}|game:{game_ref}|team:{team_ref}"
        token = _line_token(line)
        if token is not None:
            key = f"{key}|line:{token}"
        if normalized_side:
            key = f"{key}|{normalized_side}"
        return key

    key = f"{market}|game:{game_ref}"
    token = _line_token(line)
    if token is not None and market not in {"ML", "F5_ML"}:
        key = f"{key}|line:{token}"
    if normalized_side:
        key = f"{key}|{normalized_side}"
    return key


def _bet_type(market: str, side: str | None) -> str:
    if side:
        return f"{market}_{side}"
    return market


def normalize_event_odds(
    event_payload: dict[str, Any],
    fetched_at: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Normalize one Odds API event odds payload into market_odds-ready rows.

    Returns:
        rows: normalized rows for supported markets
        summary: counts for logging/monitoring
    """
    fetched_at = fetched_at or datetime.utcnow().isoformat()
    event_id = event_payload.get("id")
    game_id = event_payload.get("id")
    game_date = _date_from_commence(event_payload.get("commence_time"))
    home_team = event_payload.get("home_team")
    away_team = event_payload.get("away_team")

    rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "total_outcomes": 0,
        "normalized_rows": 0,
        "skipped_unsupported_market": 0,
        "skipped_invalid_price": 0,
        "skipped_missing_required": 0,
        "unsupported_market_counts": {},
    }

    for bookmaker in event_payload.get("bookmakers", []):
        sportsbook = bookmaker.get("key")
        for market_block in bookmaker.get("markets", []):
            source_market_key = market_block.get("key", "")
            for outcome in market_block.get("outcomes", []):
                summary["total_outcomes"] += 1

                line = _as_float(outcome.get("point"))
                market_info = _effective_market(source_market_key, line)
                if not market_info:
                    summary["skipped_unsupported_market"] += 1
                    unsupported_counts = summary["unsupported_market_counts"]
                    unsupported_counts[source_market_key] = unsupported_counts.get(source_market_key, 0) + 1
                    continue

                price_american = _as_int(outcome.get("price"))
                if price_american is None:
                    summary["skipped_invalid_price"] += 1
                    continue
                if not sportsbook:
                    summary["skipped_missing_required"] += 1
                    continue

                market = market_info["market"]
                entity_type = market_info["entity_type"]
                side = _normalize_side(outcome.get("name"), home_team=home_team, away_team=away_team)

                player_id = _as_int(outcome.get("player_id"))
                player_name = outcome.get("description")
                team_id = outcome.get("team_id")
                team_name = None
                opponent_team_id = None

                if entity_type == "team":
                    team_name = outcome.get("description") or outcome.get("name")
                    if home_team and team_name and team_name.strip().lower() == home_team.strip().lower():
                        team_id = home_team
                        opponent_team_id = away_team
                    elif away_team and team_name and team_name.strip().lower() == away_team.strip().lower():
                        team_id = away_team
                        opponent_team_id = home_team

                if entity_type in {"batter", "pitcher"}:
                    if player_name is None:
                        player_name = outcome.get("name")

                selection_key = build_selection_key(
                    market=market,
                    entity_type=entity_type,
                    game_id=game_id,
                    event_id=event_id,
                    player_id=player_id,
                    player_name=player_name,
                    team_id=team_id,
                    team_name=team_name,
                    side=side,
                    line=line,
                )

                decimal_price = american_to_decimal(price_american)
                implied_prob = american_to_implied_prob(price_american)
                if implied_prob is None and decimal_price is not None:
                    implied_prob = decimal_to_implied_prob(decimal_price)

                row = {
                    "game_date": game_date,
                    "game_id": game_id,
                    "event_id": event_id,
                    "market": market,
                    "entity_type": entity_type,
                    "player_id": player_id,
                    "team_id": team_id,
                    "opponent_team_id": opponent_team_id,
                    "selection_key": selection_key,
                    "side": side,
                    "line": line,
                    "price_american": price_american,
                    "price_decimal": decimal_price,
                    "implied_probability": implied_prob,
                    "sportsbook": sportsbook,
                    "source_market_key": source_market_key,
                    "fetched_at": fetched_at,
                    # Backward-compatible fields for existing table consumers.
                    "bet_type": _bet_type(market, side),
                    "odds_decimal": decimal_price,
                    "team_abbr": team_id if isinstance(team_id, str) else None,
                    "opponent_team_abbr": (
                        opponent_team_id if isinstance(opponent_team_id, str) else None
                    ),
                    "is_best_available": 0,
                }
                rows.append(row)
                summary["normalized_rows"] += 1

    return rows, summary
