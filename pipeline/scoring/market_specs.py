"""
Market specification registry for the multi-market scoring engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


EntityType = Literal["batter", "pitcher", "team", "game"]
OutputType = Literal["probability", "projection", "hybrid"]
EdgeMethod = Literal["probability_vs_implied", "projection_vs_line", "hybrid"]
LineupRequirement = Literal["required", "recommended", "not_required"]
MissingDataPolicy = Literal["degrade_confidence", "skip_row", "store_with_risk_flags"]


@dataclass(frozen=True)
class MarketSpec:
    market: str
    entity_type: EntityType
    required_feature_tables: tuple[str, ...]
    output_type: OutputType
    edge_method: EdgeMethod
    thresholds: dict[str, dict[str, float]]
    lineup_requirement: LineupRequirement
    missing_data_policy: MissingDataPolicy
    weather_recommended: bool = True


DEFAULT_THRESHOLDS = {
    "BET": {"min_score": 75.0, "min_edge_pct": 5.0},
    "LEAN": {"min_score": 60.0, "min_edge_pct": 2.5},
    "FADE": {"max_score": 35.0, "max_edge_pct": -3.0},
    "SKIP": {},
}

CONSERVATIVE_THRESHOLDS = {
    "BET": {"min_score": 78.0, "min_edge_pct": 6.0},
    "LEAN": {"min_score": 64.0, "min_edge_pct": 3.5},
    "FADE": {"max_score": 32.0, "max_edge_pct": -4.0},
    "SKIP": {},
}

AGGRESSIVE_THRESHOLDS = {
    "BET": {"min_score": 72.0, "min_edge_pct": 4.0},
    "LEAN": {"min_score": 58.0, "min_edge_pct": 2.0},
    "FADE": {"max_score": 38.0, "max_edge_pct": -2.5},
    "SKIP": {},
}


MARKET_SPECS: dict[str, MarketSpec] = {
    "HR": MarketSpec(
        market="HR",
        entity_type="batter",
        required_feature_tables=("mlb_batter_daily_features", "mlb_pitcher_daily_features", "mlb_game_context_features"),
        output_type="probability",
        edge_method="probability_vs_implied",
        thresholds=CONSERVATIVE_THRESHOLDS,
        lineup_requirement="recommended",
        missing_data_policy="store_with_risk_flags",
    ),
    "K": MarketSpec(
        market="K",
        entity_type="pitcher",
        required_feature_tables=("mlb_pitcher_daily_features", "mlb_team_daily_features", "mlb_game_context_features"),
        output_type="hybrid",
        edge_method="hybrid",
        thresholds=DEFAULT_THRESHOLDS,
        lineup_requirement="recommended",
        missing_data_policy="store_with_risk_flags",
    ),
    "HITS_1P": MarketSpec(
        market="HITS_1P",
        entity_type="batter",
        required_feature_tables=("mlb_batter_daily_features", "mlb_pitcher_daily_features", "mlb_game_context_features"),
        output_type="probability",
        edge_method="probability_vs_implied",
        thresholds=AGGRESSIVE_THRESHOLDS,
        lineup_requirement="required",
        missing_data_policy="degrade_confidence",
    ),
    "HITS_LINE": MarketSpec(
        market="HITS_LINE",
        entity_type="batter",
        required_feature_tables=("mlb_batter_daily_features", "mlb_pitcher_daily_features", "mlb_game_context_features"),
        output_type="hybrid",
        edge_method="hybrid",
        thresholds=DEFAULT_THRESHOLDS,
        lineup_requirement="required",
        missing_data_policy="degrade_confidence",
    ),
    "TB_LINE": MarketSpec(
        market="TB_LINE",
        entity_type="batter",
        required_feature_tables=("mlb_batter_daily_features", "mlb_pitcher_daily_features", "mlb_game_context_features"),
        output_type="hybrid",
        edge_method="hybrid",
        thresholds=DEFAULT_THRESHOLDS,
        lineup_requirement="required",
        missing_data_policy="degrade_confidence",
    ),
    "OUTS_RECORDED": MarketSpec(
        market="OUTS_RECORDED",
        entity_type="pitcher",
        required_feature_tables=("mlb_pitcher_daily_features", "mlb_team_daily_features", "mlb_game_context_features"),
        output_type="projection",
        edge_method="projection_vs_line",
        thresholds=DEFAULT_THRESHOLDS,
        lineup_requirement="recommended",
        missing_data_policy="store_with_risk_flags",
    ),
    "ML": MarketSpec(
        market="ML",
        entity_type="game",
        required_feature_tables=("mlb_pitcher_daily_features", "mlb_team_daily_features", "mlb_game_context_features"),
        output_type="probability",
        edge_method="probability_vs_implied",
        thresholds=DEFAULT_THRESHOLDS,
        lineup_requirement="recommended",
        missing_data_policy="store_with_risk_flags",
    ),
    "TOTAL": MarketSpec(
        market="TOTAL",
        entity_type="game",
        required_feature_tables=("mlb_pitcher_daily_features", "mlb_team_daily_features", "mlb_game_context_features"),
        output_type="projection",
        edge_method="projection_vs_line",
        thresholds=DEFAULT_THRESHOLDS,
        lineup_requirement="recommended",
        missing_data_policy="store_with_risk_flags",
    ),
    "F5_ML": MarketSpec(
        market="F5_ML",
        entity_type="game",
        required_feature_tables=("mlb_pitcher_daily_features", "mlb_team_daily_features", "mlb_game_context_features"),
        output_type="probability",
        edge_method="probability_vs_implied",
        thresholds=DEFAULT_THRESHOLDS,
        lineup_requirement="recommended",
        missing_data_policy="store_with_risk_flags",
    ),
    "F5_TOTAL": MarketSpec(
        market="F5_TOTAL",
        entity_type="game",
        required_feature_tables=("mlb_pitcher_daily_features", "mlb_team_daily_features", "mlb_game_context_features"),
        output_type="projection",
        edge_method="projection_vs_line",
        thresholds=DEFAULT_THRESHOLDS,
        lineup_requirement="recommended",
        missing_data_policy="store_with_risk_flags",
    ),
    "TEAM_TOTAL": MarketSpec(
        market="TEAM_TOTAL",
        entity_type="team",
        required_feature_tables=("mlb_team_daily_features", "mlb_pitcher_daily_features", "mlb_game_context_features"),
        output_type="projection",
        edge_method="projection_vs_line",
        thresholds=DEFAULT_THRESHOLDS,
        lineup_requirement="recommended",
        missing_data_policy="store_with_risk_flags",
    ),
}


def get_market_spec(market: str) -> MarketSpec:
    normalized = (market or "").upper()
    if normalized not in MARKET_SPECS:
        raise KeyError(f"Unknown market spec: {market}")
    return MARKET_SPECS[normalized]


def list_supported_markets() -> list[str]:
    return sorted(MARKET_SPECS.keys())
