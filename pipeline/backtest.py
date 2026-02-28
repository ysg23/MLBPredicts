"""
Historical backtesting using feature-store driven scores with no lookahead on odds.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from config import DATA_DIR
from db.database import query
from grading.base_grader import payout_for_settlement, settle_selection


FIELDNAMES = [
    "game_date",
    "market",
    "game_id",
    "selection_key",
    "signal",
    "model_score",
    "model_prob",
    "edge",
    "side",
    "line",
    "open_odds",
    "open_implied_prob",
    "close_implied_prob",
    "clv",
    "outcome_value",
    "settlement",
    "profit_units",
    "score_bucket",
    "prob_bucket",
]


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _score_bucket(model_score: float | None) -> str:
    if model_score is None:
        return "unknown"
    score = float(model_score)
    if score < 50:
        return "<50"
    if score < 60:
        return "50-59"
    if score < 70:
        return "60-69"
    if score < 80:
        return "70-79"
    return "80+"


def _prob_bucket(model_prob: float | None) -> str:
    if model_prob is None:
        return "unknown"
    p = max(0.0, min(1.0, float(model_prob)))
    lo = int(math.floor(p * 10) * 10)
    hi = min(99, lo + 9)
    return f"{lo}-{hi}%"


def _load_scores(market: str, start_date: str | None, end_date: str | None, signals: set[str]) -> list[dict[str, Any]]:
    filters = ["market = ?"]
    params: list[Any] = [market]
    if start_date:
        filters.append("game_date >= ?")
        params.append(start_date)
    if end_date:
        filters.append("game_date <= ?")
        params.append(end_date)
    if signals:
        signal_list = sorted(signals)
        filters.append(f"signal IN ({','.join(['?'] * len(signal_list))})")
        params.extend(signal_list)

    where_sql = " AND ".join(filters)
    return query(
        f"""
        SELECT *
        FROM mlb_model_scores
        WHERE {where_sql}
        ORDER BY game_date, game_id, created_at, id
        """,
        tuple(params),
    )


def _match_outcome(score: dict[str, Any]) -> dict[str, Any] | None:
    selection_key = score.get("selection_key")
    if selection_key:
        rows = query(
            """
            SELECT *
            FROM mlb_market_outcomes
            WHERE market = ?
              AND game_id = ?
              AND selection_key = ?
            LIMIT 1
            """,
            (score.get("market"), score.get("game_id"), selection_key),
        )
        if rows:
            return rows[0]

    rows = query(
        """
        SELECT *
        FROM mlb_market_outcomes
        WHERE market = ?
          AND game_id = ?
          AND (player_id = ? OR (player_id IS NULL AND ? IS NULL))
          AND (team_id = ? OR (team_id IS NULL AND ? IS NULL))
          AND (side = ? OR (side IS NULL AND ? IS NULL))
          AND (bet_type = ? OR (bet_type IS NULL AND ? IS NULL))
          AND (line = ? OR (line IS NULL AND ? IS NULL))
        LIMIT 1
        """,
        (
            score.get("market"),
            score.get("game_id"),
            score.get("player_id"),
            score.get("player_id"),
            score.get("team_id"),
            score.get("team_id"),
            score.get("side"),
            score.get("side"),
            score.get("bet_type"),
            score.get("bet_type"),
            score.get("line"),
            score.get("line"),
        ),
    )
    return rows[0] if rows else None


def _match_open_odds(score: dict[str, Any]) -> dict[str, Any] | None:
    """
    No-lookahead: only allow odds rows fetched at or before model_score created_at.
    """
    created_at = score.get("created_at")
    selection_key = score.get("selection_key")
    if selection_key:
        rows = query(
            """
            SELECT *
            FROM mlb_market_odds
            WHERE market = ?
              AND game_id = ?
              AND selection_key = ?
              AND fetched_at <= ?
            ORDER BY fetched_at DESC, price_decimal DESC
            LIMIT 1
            """,
            (score.get("market"), score.get("game_id"), selection_key, created_at),
        )
        if rows:
            return rows[0]

    rows = query(
        """
        SELECT *
        FROM mlb_market_odds
        WHERE market = ?
          AND game_id = ?
          AND (player_id = ? OR (player_id IS NULL AND ? IS NULL))
          AND (team_id = ? OR (team_id IS NULL AND ? IS NULL))
          AND (side = ? OR (side IS NULL AND ? IS NULL))
          AND (bet_type = ? OR (bet_type IS NULL AND ? IS NULL))
          AND (line = ? OR (line IS NULL AND ? IS NULL))
          AND fetched_at <= ?
        ORDER BY fetched_at DESC, price_decimal DESC
        LIMIT 1
        """,
        (
            score.get("market"),
            score.get("game_id"),
            score.get("player_id"),
            score.get("player_id"),
            score.get("team_id"),
            score.get("team_id"),
            score.get("side"),
            score.get("side"),
            score.get("bet_type"),
            score.get("bet_type"),
            score.get("line"),
            score.get("line"),
            created_at,
        ),
    )
    return rows[0] if rows else None


def _match_closing_odds(score: dict[str, Any]) -> dict[str, Any] | None:
    selection_key = score.get("selection_key")
    if selection_key:
        rows = query(
            """
            SELECT *
            FROM mlb_closing_lines
            WHERE market = ?
              AND game_id = ?
              AND selection_key = ?
            LIMIT 1
            """,
            (score.get("market"), score.get("game_id"), selection_key),
        )
        if rows:
            return rows[0]

    rows = query(
        """
        SELECT *
        FROM mlb_closing_lines
        WHERE market = ?
          AND game_id = ?
          AND (player_id = ? OR (player_id IS NULL AND ? IS NULL))
          AND (team_id = ? OR (team_id IS NULL AND ? IS NULL))
          AND (side = ? OR (side IS NULL AND ? IS NULL))
          AND (bet_type = ? OR (bet_type IS NULL AND ? IS NULL))
          AND (line = ? OR (line IS NULL AND ? IS NULL))
        LIMIT 1
        """,
        (
            score.get("market"),
            score.get("game_id"),
            score.get("player_id"),
            score.get("player_id"),
            score.get("team_id"),
            score.get("team_id"),
            score.get("side"),
            score.get("side"),
            score.get("bet_type"),
            score.get("bet_type"),
            score.get("line"),
            score.get("line"),
        ),
    )
    return rows[0] if rows else None


def _implied_prob_from_row(row: dict[str, Any] | None) -> float | None:
    if not row:
        return None
    implied = _to_float(row.get("implied_probability"))
    if implied is not None:
        return implied
    american = _to_float(row.get("price_american"))
    if american is None or american == 0:
        return None
    if american > 0:
        return 100.0 / (american + 100.0)
    return abs(american) / (abs(american) + 100.0)


def _factor_dict(score: dict[str, Any]) -> dict[str, float]:
    raw = score.get("factors_json")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {k: _to_float(v) for k, v in raw.items() if _to_float(v) is not None}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in parsed.items():
        converted = _to_float(value)
        if converted is not None:
            out[key] = converted
    return out


def _corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = mean(xs)
    my = mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def _write_results_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            out = {k: row.get(k) for k in FIELDNAMES}
            writer.writerow(out)


def run_backtest(
    *,
    market: str,
    start_date: str | None,
    end_date: str | None,
    signals: set[str],
) -> dict[str, Any]:
    output_path = Path(DATA_DIR) / f"backtest_results_{market}.csv"
    scores = _load_scores(market, start_date, end_date, signals)
    if not scores:
        _write_results_csv(output_path, [])
        return {
            "market": market,
            "rows_scored": 0,
            "rows_with_open_odds": 0,
            "rows_graded": 0,
            "win_rate": None,
            "roi_units": None,
            "csv_path": str(output_path),
        }

    simulated_rows: list[dict[str, Any]] = []
    for score in scores:
        open_odds = _match_open_odds(score)
        if not open_odds:
            continue
        outcome = _match_outcome(score)
        if not outcome:
            continue
        settlement = settle_selection(
            market=score.get("market"),
            side=score.get("side"),
            line=score.get("line"),
            outcome_value=outcome.get("outcome_value"),
            bet_type=score.get("bet_type"),
        )
        payout, profit = payout_for_settlement(
            stake=1.0,
            american_odds=open_odds.get("price_american"),
            settlement=settlement,
        )
        if profit is None:
            continue

        open_implied = _implied_prob_from_row(open_odds)
        close_odds = _match_closing_odds(score)
        close_implied = _implied_prob_from_row(close_odds)
        clv = None
        if open_implied is not None and close_implied is not None:
            clv = open_implied - close_implied

        simulated_rows.append(
            {
                "game_date": score.get("game_date"),
                "market": score.get("market"),
                "game_id": score.get("game_id"),
                "selection_key": score.get("selection_key"),
                "signal": score.get("signal"),
                "model_score": _to_float(score.get("model_score")),
                "model_prob": _to_float(score.get("model_prob")),
                "edge": _to_float(score.get("edge")),
                "side": score.get("side"),
                "line": _to_float(score.get("line")),
                "open_odds": _to_float(open_odds.get("price_american")),
                "open_implied_prob": open_implied,
                "close_implied_prob": close_implied,
                "clv": clv,
                "outcome_value": _to_float(outcome.get("outcome_value")),
                "settlement": settlement,
                "profit_units": profit,
                "score_bucket": _score_bucket(_to_float(score.get("model_score"))),
                "prob_bucket": _prob_bucket(_to_float(score.get("model_prob"))),
                "factors_json": score.get("factors_json"),
            }
        )

    rows_graded = len(simulated_rows)
    if rows_graded == 0:
        _write_results_csv(output_path, [])
        return {
            "market": market,
            "rows_scored": len(scores),
            "rows_with_open_odds": 0,
            "rows_graded": 0,
            "win_rate": None,
            "roi_units": None,
            "csv_path": str(output_path),
        }

    wins = sum(1 for row in simulated_rows if row["settlement"] == "win")
    losses = sum(1 for row in simulated_rows if row["settlement"] == "loss")
    decisions = wins + losses
    total_profit = sum(float(row["profit_units"]) for row in simulated_rows)
    roi_units = total_profit / rows_graded
    win_rate = (wins / decisions) if decisions > 0 else None

    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_prob_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in simulated_rows:
        by_bucket[row["score_bucket"]].append(row)
        by_prob_bucket[row["prob_bucket"]].append(row)

    bucket_summary: dict[str, dict[str, Any]] = {}
    for bucket, rows in by_bucket.items():
        b_wins = sum(1 for r in rows if r["settlement"] == "win")
        b_losses = sum(1 for r in rows if r["settlement"] == "loss")
        b_decisions = b_wins + b_losses
        bucket_summary[bucket] = {
            "count": len(rows),
            "win_rate": (b_wins / b_decisions) if b_decisions else None,
            "roi": sum(float(r["profit_units"]) for r in rows) / len(rows),
            "avg_edge": mean([r["edge"] for r in rows if r["edge"] is not None]) if any(r["edge"] is not None for r in rows) else None,
            "avg_clv": mean([r["clv"] for r in rows if r["clv"] is not None]) if any(r["clv"] is not None for r in rows) else None,
        }

    calibration_summary: dict[str, dict[str, Any]] = {}
    for bucket, rows in by_prob_bucket.items():
        if bucket == "unknown":
            continue
        realized = [1.0 if r["settlement"] == "win" else 0.0 for r in rows if r["settlement"] in {"win", "loss"}]
        model_probs = [r["model_prob"] for r in rows if r["model_prob"] is not None and r["settlement"] in {"win", "loss"}]
        if not realized or not model_probs:
            continue
        calibration_summary[bucket] = {
            "count": len(realized),
            "avg_model_prob": mean(model_probs),
            "realized_win_rate": mean(realized),
            "calibration_error": mean(model_probs) - mean(realized),
        }

    factor_values: dict[str, list[float]] = defaultdict(list)
    factor_profits: dict[str, list[float]] = defaultdict(list)
    for row in simulated_rows:
        factors = _factor_dict({"factors_json": row.get("factors_json")})
        profit = float(row["profit_units"])
        for key, value in factors.items():
            factor_values[key].append(value)
            factor_profits[key].append(profit)

    factor_diagnostics: dict[str, Any] = {}
    for key in sorted(factor_values):
        corr = _corr(factor_values[key], factor_profits[key])
        if corr is not None:
            factor_diagnostics[key] = {
                "n": len(factor_values[key]),
                "corr_with_profit": corr,
            }

    _write_results_csv(output_path, simulated_rows)

    return {
        "market": market,
        "rows_scored": len(scores),
        "rows_with_open_odds": len(simulated_rows),
        "rows_graded": rows_graded,
        "win_rate": win_rate,
        "roi_units": roi_units,
        "total_profit_units": total_profit,
        "score_bucket_summary": bucket_summary,
        "calibration_summary": calibration_summary,
        "factor_diagnostics": factor_diagnostics,
        "csv_path": str(output_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run market backtest with no-lookahead odds matching")
    parser.add_argument("--market", type=str, required=True, help="Market code, e.g. HR, K, ML, TOTAL")
    parser.add_argument("--start-date", type=str, help="Start date YYYY-MM-DD")
    parser.add_argument("--end-date", type=str, help="End date YYYY-MM-DD")
    parser.add_argument(
        "--signals",
        type=str,
        default="BET",
        help="Comma-separated signals to include (default: BET). Example: BET,LEAN",
    )
    args = parser.parse_args()

    signals = {s.strip().upper() for s in args.signals.split(",") if s.strip()}
    summary = run_backtest(
        market=args.market.upper(),
        start_date=args.start_date,
        end_date=args.end_date,
        signals=signals,
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
