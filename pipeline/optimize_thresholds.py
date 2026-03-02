"""
Backtest-driven threshold optimizer.

Wraps backtest.py infrastructure to find optimal (min_score, min_edge) thresholds
per market by grid search.  Does NOT auto-update market_specs.py — prints
recommended changes for manual review.

Usage:
    python optimize_thresholds.py --market HR --start 2025-04-01 --end 2025-09-30
    python optimize_thresholds.py --all --start 2025-04-01 --end 2025-09-30

Objective: Sharpe-style = ROI / std(profit_per_bet), with minimum 30 bets required.

Grid:
    min_score : [55, 60, 65, 70, 75, 78, 80]
    min_edge  : [2, 3, 4, 5, 6, 7]  (percent)
"""
from __future__ import annotations

import argparse
import json
import math
from statistics import mean, stdev
from typing import Any

from backtest import (
    _load_scores,
    _match_open_odds,
    _match_outcome,
    _implied_prob_from_row,
)
from grading.base_grader import payout_for_settlement, settle_selection
from scoring.market_specs import list_supported_markets


MIN_SCORE_GRID = [55, 60, 65, 70, 75, 78, 80]
MIN_EDGE_GRID = [2, 3, 4, 5, 6, 7]
MIN_BETS_REQUIRED = 30


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_simulated_rows(
    market: str,
    start_date: str | None,
    end_date: str | None,
) -> list[dict[str, Any]]:
    """Load all scores for a market/period and resolve outcomes + open odds.

    Returns a flat list of dicts with: model_score, edge, profit_units.
    No signal filter is applied so the full score range is available for grid search.
    """
    scores = _load_scores(market, start_date, end_date, signals=set())
    rows: list[dict[str, Any]] = []
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
        _, profit = payout_for_settlement(
            stake=1.0,
            american_odds=open_odds.get("price_american"),
            settlement=settlement,
        )
        if profit is None:
            continue

        rows.append({
            "model_score": _to_float(score.get("model_score")),
            "edge": _to_float(score.get("edge")),
            "profit_units": float(profit),
        })
    return rows


def _evaluate_threshold(
    rows: list[dict[str, Any]],
    min_score: float,
    min_edge: float,
) -> dict[str, Any] | None:
    """Apply (min_score, min_edge) filter and compute Sharpe-style metric.

    Returns None if fewer than MIN_BETS_REQUIRED bets qualify.
    """
    filtered = [
        r for r in rows
        if (r["model_score"] is not None and r["model_score"] >= min_score)
        and (r["edge"] is not None and r["edge"] >= min_edge)
    ]
    n = len(filtered)
    if n < MIN_BETS_REQUIRED:
        return None

    profits = [r["profit_units"] for r in filtered]
    total = sum(profits)
    roi = total / n

    if n < 2:
        return None
    std = stdev(profits)
    sharpe = (roi / std) if std > 0 else 0.0

    wins = sum(1 for p in profits if p > 0)
    losses = sum(1 for p in profits if p < 0)
    win_rate = wins / (wins + losses) if (wins + losses) > 0 else None

    return {
        "min_score": min_score,
        "min_edge": min_edge,
        "n_bets": n,
        "roi": round(roi, 4),
        "sharpe": round(sharpe, 4),
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "total_profit": round(total, 3),
    }


def optimize_market(
    market: str,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any]:
    """Run grid search for a single market and return results table + recommended thresholds."""
    print(f"\n--- Optimizing {market} ---")
    rows = _build_simulated_rows(market, start_date, end_date)
    if not rows:
        print(f"  No graded rows found for {market}.")
        return {"market": market, "rows_graded": 0, "results": [], "recommended": None}

    print(f"  Total graded rows: {len(rows)}")

    results: list[dict[str, Any]] = []
    for min_score in MIN_SCORE_GRID:
        for min_edge in MIN_EDGE_GRID:
            result = _evaluate_threshold(rows, float(min_score), float(min_edge))
            if result:
                results.append(result)

    if not results:
        print(f"  No threshold combo reached {MIN_BETS_REQUIRED} bets.")
        return {"market": market, "rows_graded": len(rows), "results": [], "recommended": None}

    # Rank by Sharpe descending
    results.sort(key=lambda r: r["sharpe"], reverse=True)
    best = results[0]

    # Print table
    header = f"{'min_score':>10} {'min_edge':>10} {'n_bets':>8} {'roi':>8} {'sharpe':>8} {'win_rate':>10}"
    print(f"\n  {header}")
    print(f"  {'-' * len(header)}")
    for r in results:
        win_str = f"{r['win_rate']:.4f}" if r["win_rate"] is not None else "   N/A  "
        print(
            f"  {r['min_score']:>10.0f} {r['min_edge']:>10.0f} {r['n_bets']:>8d} "
            f"{r['roi']:>8.4f} {r['sharpe']:>8.4f} {win_str:>10}"
        )

    print(f"\n  Best: min_score={best['min_score']}, min_edge={best['min_edge']}, "
          f"sharpe={best['sharpe']}, roi={best['roi']}, n={best['n_bets']}")
    print(
        f"\n  Recommended market_specs.py update for {market}:\n"
        f"    BET:  min_score={best['min_score']}, min_edge_pct={best['min_edge']}\n"
        f"    LEAN: min_score={max(55, best['min_score'] - 15)}, "
        f"min_edge_pct={max(2, best['min_edge'] - 2)}"
    )

    return {
        "market": market,
        "rows_graded": len(rows),
        "results": results,
        "recommended": {
            "BET": {"min_score": best["min_score"], "min_edge_pct": best["min_edge"]},
            "LEAN": {
                "min_score": max(55, best["min_score"] - 15),
                "min_edge_pct": max(2, best["min_edge"] - 2),
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Grid-search optimal thresholds per market using historical backtest data"
    )
    parser.add_argument("--market", type=str, help="Market code, e.g. HR, HITS_1P")
    parser.add_argument("--all", action="store_true", help="Run for all registered markets")
    parser.add_argument("--start", type=str, dest="start_date", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, dest="end_date", help="End date YYYY-MM-DD")
    args = parser.parse_args()

    if not args.market and not args.all:
        parser.error("Specify --market MARKET or --all")

    markets = list_supported_markets() if args.all else [args.market.upper()]

    all_results: list[dict[str, Any]] = []
    for market in markets:
        result = optimize_market(market, args.start_date, args.end_date)
        all_results.append(result)

    print("\n\n=== SUMMARY OF RECOMMENDED THRESHOLD UPDATES ===")
    print("(Review before applying to scoring/market_specs.py)\n")
    any_rec = False
    for res in all_results:
        rec = res.get("recommended")
        if rec:
            any_rec = True
            print(f"  {res['market']}:")
            print(f"    BET : min_score={rec['BET']['min_score']}, min_edge_pct={rec['BET']['min_edge_pct']}")
            print(f"    LEAN: min_score={rec['LEAN']['min_score']}, min_edge_pct={rec['LEAN']['min_edge_pct']}")
    if not any_rec:
        print("  No recommendations generated (insufficient graded data).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
