"""
Market-agnostic scoring helpers and execution engine.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import numpy as np

from db.database import get_connection, insert_many, query
from scoring.market_specs import get_market_spec


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
    game_time: Optional[str] = None
    home_pitcher_hand: Optional[str] = None
    away_pitcher_hand: Optional[str] = None


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, value)))


def percentile_score(values: list[float], value: float | None) -> float:
    """Return percentile rank (0-100)."""
    if value is None or not values:
        return 50.0
    arr = np.array([v for v in values if v is not None], dtype=float)
    if arr.size == 0:
        return 50.0
    return float((arr < float(value)).mean() * 100.0)


def percentile_rank(values: list[float], x: float) -> float:
    """Backwards-compatible alias."""
    return percentile_score(values, x)


def zscore_to_0_100(zscore: float, z_cap: float = 3.0) -> float:
    if zscore is None:
        return 50.0
    z = clamp(float(zscore), -abs(z_cap), abs(z_cap))
    normalized = (z + abs(z_cap)) / (2 * abs(z_cap))
    return clamp(normalized * 100.0)


def implied_prob_from_american(american: int | float | None) -> float | None:
    if american is None:
        return None
    value = float(american)
    if value == 0:
        return None
    if value > 0:
        return 100.0 / (value + 100.0)
    return abs(value) / (abs(value) + 100.0)


def american_to_decimal(american: int | float | None) -> float | None:
    if american is None:
        return None
    value = float(american)
    if value == 0:
        return None
    if value > 0:
        return 1.0 + (value / 100.0)
    return 1.0 + (100.0 / abs(value))


def probability_edge_pct(model_prob: float | None, implied_prob: float | None) -> float | None:
    if model_prob is None or implied_prob is None:
        return None
    return (float(model_prob) - float(implied_prob)) * 100.0


def projection_edge_pct(model_projection: float | None, line: float | None) -> float | None:
    if model_projection is None or line is None:
        return None
    line_value = float(line)
    if line_value == 0:
        return (float(model_projection) - line_value) * 100.0
    return ((float(model_projection) - line_value) / abs(line_value)) * 100.0


def build_reasons(factors: dict[str, float] | None, top_n: int = 3) -> list[str]:
    if not factors:
        return []
    sorted_items = sorted(factors.items(), key=lambda item: item[1], reverse=True)
    reasons: list[str] = []
    for key, score in sorted_items[:top_n]:
        label = key.replace("_", " ")
        reasons.append(f"{label}: {score:.1f}")
    return reasons


def build_risk_flags(
    *,
    missing_inputs: list[str] | None = None,
    stale_inputs: list[str] | None = None,
    lineup_pending: bool = False,
    weather_pending: bool = False,
) -> list[str]:
    flags: list[str] = []
    for item in missing_inputs or []:
        flags.append(f"missing:{item}")
    for item in stale_inputs or []:
        flags.append(f"stale:{item}")
    if lineup_pending:
        flags.append("lineup_pending")
    if weather_pending:
        flags.append("weather_pending")
    return flags


def choose_best_odds_row(
    rows: list[dict[str, Any]],
    preferred_sportsbook: str | None = None,
) -> dict[str, Any] | None:
    if not rows:
        return None

    # Prefer current run's latest odds timestamp.
    latest_ts = max(str(r.get("fetched_at") or "") for r in rows)
    latest_rows = [r for r in rows if str(r.get("fetched_at") or "") == latest_ts] or rows

    preferred_row = None
    if preferred_sportsbook:
        preferred = [r for r in latest_rows if (r.get("sportsbook") or "").lower() == preferred_sportsbook.lower()]
        if preferred:
            preferred_row = max(preferred, key=lambda r: float(r.get("price_decimal") or r.get("odds_decimal") or 0.0))

    if preferred_row:
        return preferred_row

    return max(latest_rows, key=lambda r: float(r.get("price_decimal") or r.get("odds_decimal") or 0.0))


def get_market_odds_rows(
    *,
    game_date: str,
    market: str,
    game_id: int | None = None,
    player_id: int | None = None,
    team_id: str | None = None,
    side: str | None = None,
) -> list[dict[str, Any]]:
    filters = ["game_date = ?", "market = ?"]
    params: list[Any] = [game_date, market]
    if game_id is not None:
        filters.append("game_id = ?")
        params.append(game_id)
    if player_id is not None:
        filters.append("player_id = ?")
        params.append(player_id)
    if team_id is not None:
        filters.append("team_id = ?")
        params.append(team_id)
    if side is not None:
        filters.append("side = ?")
        params.append(side)

    where_sql = " AND ".join(filters)
    return query(
        f"""
        SELECT *
        FROM mlb_market_odds
        WHERE {where_sql}
        ORDER BY fetched_at DESC
        """,
        tuple(params),
    )


def get_batter_universe(game_date: str, game: GameContext) -> list[dict[str, Any]]:
    """Batters from batter_daily_features for teams in this game."""
    rows = query(
        """
        SELECT DISTINCT player_id, team_id
        FROM mlb_batter_daily_features
        WHERE game_date = ?
          AND team_id IN (?, ?)
          AND player_id IS NOT NULL
        """,
        (game_date, game.home_team, game.away_team),
    )
    results: list[dict[str, Any]] = []
    for r in rows:
        team = r["team_id"]
        opp = game.away_team if team == game.home_team else game.home_team
        results.append({
            "player_id": int(r["player_id"]),
            "team_id": team,
            "team_abbr": team,
            "opponent_team_id": opp,
            "opponent_team_abbr": opp,
        })
    return results


def get_pitcher_universe(game: GameContext) -> list[dict[str, Any]]:
    """Probable starters from the games table."""
    pitchers: list[dict[str, Any]] = []
    if game.home_pitcher_id:
        pitchers.append({
            "player_id": game.home_pitcher_id,
            "player_name": game.home_pitcher_name,
            "team_id": game.home_team,
            "opponent_team_id": game.away_team,
            "team_abbr": game.home_team,
            "opponent_team_abbr": game.away_team,
        })
    if game.away_pitcher_id:
        pitchers.append({
            "player_id": game.away_pitcher_id,
            "player_name": game.away_pitcher_name,
            "team_id": game.away_team,
            "opponent_team_id": game.home_team,
            "team_abbr": game.away_team,
            "opponent_team_abbr": game.home_team,
        })
    return pitchers


def get_game_sides(game: GameContext) -> list[dict[str, Any]]:
    """HOME and AWAY sides for game-level markets (ML, totals, etc.)."""
    return [
        {
            "side": "HOME",
            "team_id": game.home_team,
            "team_abbr": game.home_team,
            "opponent_team_id": game.away_team,
            "opponent_team_abbr": game.away_team,
        },
        {
            "side": "AWAY",
            "team_id": game.away_team,
            "team_abbr": game.away_team,
            "opponent_team_id": game.home_team,
            "opponent_team_abbr": game.home_team,
        },
    ]


def get_best_hr_odds(game_id: int, player_id: int) -> dict[str, Any] | None:
    """
    Backward-compatible HR lookup from legacy hr_odds.
    """
    rows = query(
        """
        SELECT sportsbook, over_price, implied_prob_over, fetch_time
        FROM mlb_hr_odds
        WHERE game_id=? AND player_id=? AND over_price IS NOT NULL
        ORDER BY fetch_time DESC
        """,
        (game_id, player_id),
    )
    if not rows:
        return None
    best = None
    for row in rows:
        dec = american_to_decimal(row.get("over_price"))
        if dec is None:
            continue
        if best is None or dec > best["odds_decimal"]:
            best = {
                "sportsbook": row["sportsbook"],
                "over_price": int(row["over_price"]),
                "odds_decimal": dec,
                "implied_prob": (
                    float(row["implied_prob_over"])
                    if row.get("implied_prob_over") is not None
                    else implied_prob_from_american(row.get("over_price"))
                ),
                "fetch_time": row["fetch_time"],
            }
    return best


def _lineup_weather_flags(game_date: str, game_id: int) -> tuple[int | None, int | None]:
    rows = query(
        """
        SELECT lineups_confirmed_home, lineups_confirmed_away, is_final_context
        FROM mlb_game_context_features
        WHERE game_date = ? AND game_id = ?
        LIMIT 1
        """,
        (game_date, game_id),
    )
    if not rows:
        return None, None
    lineup_confirmed = 1 if (rows[0].get("lineups_confirmed_home") and rows[0].get("lineups_confirmed_away")) else 0
    weather_final = 1 if rows[0].get("is_final_context") else 0
    return lineup_confirmed, weather_final


def _confidence_band(model_score: float, risk_flags: list[str]) -> str:
    score = float(model_score)
    if score >= 78:
        band = "HIGH"
    elif score >= 60:
        band = "MEDIUM"
    else:
        band = "LOW"
    if len(risk_flags) >= 2 and band == "HIGH":
        return "MEDIUM"
    if len(risk_flags) >= 3 and band == "MEDIUM":
        return "LOW"
    return band


def determine_visibility_tier(signal: str, confidence_band: str) -> str:
    """Simple tiering extension point for future monetization."""
    sig = (signal or "").upper()
    conf = (confidence_band or "").upper()
    if sig == "BET" and conf == "HIGH":
        return "FREE"
    return "PRO"


def assign_signal(market: str, model_score: float, edge_pct: float | None) -> str:
    spec = get_market_spec(market)
    thresholds = spec.thresholds
    score = float(model_score)

    if edge_pct is None:
        # Score-only mode (no odds available) — signal based purely on model confidence
        if score >= thresholds["BET"]["min_score"]:
            return "BET"
        if score >= thresholds["LEAN"]["min_score"]:
            return "LEAN"
        if score <= thresholds["FADE"]["max_score"]:
            return "FADE"
        return "SKIP"

    # Full mode (odds available) — require both score and edge
    edge = edge_pct
    if score >= thresholds["BET"]["min_score"] and edge >= thresholds["BET"]["min_edge_pct"]:
        return "BET"
    if score >= thresholds["LEAN"]["min_score"] and edge >= thresholds["LEAN"]["min_edge_pct"]:
        return "LEAN"
    if score <= thresholds["FADE"]["max_score"] and edge <= thresholds["FADE"]["max_edge_pct"]:
        return "FADE"
    return "SKIP"


def mark_previous_scores_inactive(game_date: str, market: str, game_id: int | None = None) -> int:
    conn = get_connection()
    try:
        if game_id is None:
            cursor = conn.execute(
                """
                UPDATE mlb_model_scores
                SET is_active = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE game_date = ? AND market = ? AND COALESCE(is_active, 1) = 1
                """,
                (game_date, market),
            )
        else:
            cursor = conn.execute(
                """
                UPDATE mlb_model_scores
                SET is_active = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE game_date = ? AND market = ? AND game_id = ? AND COALESCE(is_active, 1) = 1
                """,
                (game_date, market, game_id),
            )
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


def save_model_scores(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    return int(insert_many("mlb_model_scores", rows))


def load_today_games(game_date: str) -> list[GameContext]:
    games = query(
        """
        SELECT game_id, game_date, game_time, home_team, away_team, home_pitcher_id, home_pitcher_name,
               away_pitcher_id, away_pitcher_name, stadium_id, home_pitcher_hand, away_pitcher_hand
        FROM mlb_games
        WHERE game_date=?
        """,
        (game_date,),
    )
    return [
        GameContext(
            game_id=int(g["game_id"]),
            game_date=str(g["game_date"]),
            game_time=g.get("game_time"),
            home_team=g["home_team"],
            away_team=g["away_team"],
            home_pitcher_id=g.get("home_pitcher_id"),
            home_pitcher_name=g.get("home_pitcher_name"),
            away_pitcher_id=g.get("away_pitcher_id"),
            away_pitcher_name=g.get("away_pitcher_name"),
            stadium_id=g.get("stadium_id"),
            home_pitcher_hand=g.get("home_pitcher_hand"),
            away_pitcher_hand=g.get("away_pitcher_hand"),
        )
        for g in games
    ]


def get_weather(game_id: int) -> dict[str, Any] | None:
    rows = query(
        "SELECT * FROM mlb_weather WHERE game_id=? ORDER BY fetch_time DESC LIMIT 1",
        (game_id,),
    )
    return rows[0] if rows else None


def get_park_factor(stadium_id: int | None, season: int) -> float:
    if stadium_id is None:
        return 1.0
    rows = query(
        "SELECT hr_factor FROM mlb_park_factors WHERE stadium_id=? AND season=?",
        (stadium_id, season),
    )
    if rows and rows[0].get("hr_factor") is not None:
        return float(rows[0]["hr_factor"])
    fallback = query("SELECT hr_park_factor FROM mlb_stadiums WHERE stadium_id=?", (stadium_id,))
    if fallback and fallback[0].get("hr_park_factor") is not None:
        return float(fallback[0]["hr_park_factor"])
    return 1.0


def _normalize_row_for_storage(
    *,
    row: dict[str, Any],
    game_date: str,
    score_run_id: int | None,
) -> dict[str, Any]:
    normalized = dict(row)
    normalized["game_date"] = game_date
    normalized["score_run_id"] = score_run_id
    normalized["is_active"] = int(normalized.get("is_active", 1))
    normalized["model_score"] = float(normalized.get("model_score", 50.0))

    factors = normalized.get("factors_json")
    reasons = normalized.get("reasons_json")
    risk_flags = normalized.get("risk_flags_json")

    if isinstance(factors, dict):
        factors = json.dumps(factors)
    if isinstance(reasons, list):
        reasons = json.dumps(reasons)
    if isinstance(risk_flags, list):
        risk_flags = json.dumps(risk_flags)

    normalized["factors_json"] = factors if factors is not None else "{}"
    normalized["reasons_json"] = reasons if reasons is not None else "[]"
    normalized["risk_flags_json"] = risk_flags if risk_flags is not None else "[]"

    # Apply standardized signal/confidence if not set by module.
    parsed_risk = json.loads(normalized["risk_flags_json"]) if normalized["risk_flags_json"] else []
    if not isinstance(parsed_risk, list):
        parsed_risk = []

    edge_pct = normalized.get("edge")
    if normalized.get("signal") is None:
        normalized["signal"] = assign_signal(
            normalized["market"],
            float(normalized["model_score"]),
            float(edge_pct) if edge_pct is not None else None,
        )
    if normalized.get("confidence_band") is None:
        normalized["confidence_band"] = _confidence_band(float(normalized["model_score"]), parsed_risk)

    if normalized.get("visibility_tier") is None:
        normalized["visibility_tier"] = determine_visibility_tier(
            str(normalized.get("signal") or ""),
            str(normalized.get("confidence_band") or ""),
        )

    if normalized.get("lineup_confirmed") is None or normalized.get("weather_final") is None:
        lineup_confirmed, weather_final = _lineup_weather_flags(game_date, int(normalized["game_id"]))
        if normalized.get("lineup_confirmed") is None:
            normalized["lineup_confirmed"] = lineup_confirmed
        if normalized.get("weather_final") is None:
            normalized["weather_final"] = weather_final

    return normalized


def score_market_for_date(
    market_module,
    game_date: str,
    season: int,
    only_game_id: int | None = None,
    score_run_id: int | None = None,
    supersede_existing: bool = False,
) -> int:
    """
    Run a market module's scoring for all games on a date.
    """
    games = load_today_games(game_date)
    if only_game_id is not None:
        games = [g for g in games if g.game_id == only_game_id]
    if not games:
        return 0

    if supersede_existing:
        mark_previous_scores_inactive(game_date, market_module.MARKET, game_id=only_game_id)

    rows_to_save: list[dict[str, Any]] = []
    for game in games:
        weather = get_weather(game.game_id)
        park_factor = get_park_factor(game.stadium_id, season)
        rows = market_module.score_game(game, weather=weather, park_factor=park_factor, season=season) or []
        for row in rows:
            row.setdefault("market", market_module.MARKET)
            row.setdefault("game_date", game_date)
            row.setdefault("game_id", game.game_id)
            row.setdefault("entity_type", get_market_spec(market_module.MARKET).entity_type)
            row.setdefault("lineup_confirmed", None)
            row.setdefault("weather_final", None)
            row.setdefault("factors_json", {})
            row.setdefault("reasons_json", [])
            row.setdefault("risk_flags_json", [])
            row.setdefault("signal", None)
            row.setdefault("confidence_band", None)
            rows_to_save.append(
                _normalize_row_for_storage(row=row, game_date=game_date, score_run_id=score_run_id)
            )

    return save_model_scores(rows_to_save)
