"""
Feature store orchestrator CLI.

Usage:
    python build_features.py --date 2026-03-27
    python build_features.py --all-dates
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any

from db.database import complete_score_run, create_score_run, fail_score_run, query
from features.batter_features import build_batter_daily_features
from features.game_context_features import build_game_context_features
from features.pitcher_features import build_pitcher_daily_features
from features.team_features import build_team_daily_features


def _resolve_dates(target_date: str | None, all_dates: bool) -> list[str]:
    if all_dates:
        rows = query(
            """
            SELECT DISTINCT game_date
            FROM games
            WHERE game_date IS NOT NULL
            ORDER BY game_date
            """
        )
        return [str(r["game_date"]) for r in rows]

    if target_date:
        return [target_date]
    return [datetime.now().strftime("%Y-%m-%d")]


def _run_for_date(game_date: str, run_type: str) -> dict[str, Any]:
    score_run_id = create_score_run(
        run_type=run_type,
        game_date=game_date,
        market=None,
        triggered_by="build_features",
        metadata={"job": "build_features"},
    )

    print("\n" + "=" * 72)
    print(f"🏗️  BUILD FEATURES FOR {game_date} (score_run_id={score_run_id})")
    print("=" * 72)

    step_outputs: dict[str, Any] = {}
    total_upserted = 0
    try:
        step_outputs["batter_daily_features"] = build_batter_daily_features(game_date=game_date)
        total_upserted += int(step_outputs["batter_daily_features"].get("rows_upserted", 0))

        step_outputs["pitcher_daily_features"] = build_pitcher_daily_features(game_date=game_date)
        total_upserted += int(step_outputs["pitcher_daily_features"].get("rows_upserted", 0))

        step_outputs["team_daily_features"] = build_team_daily_features(game_date=game_date)
        total_upserted += int(step_outputs["team_daily_features"].get("rows_upserted", 0))

        step_outputs["game_context_features"] = build_game_context_features(game_date=game_date)
        total_upserted += int(step_outputs["game_context_features"].get("rows_upserted", 0))

        complete_score_run(
            score_run_id=score_run_id,
            status="completed",
            rows_scored=total_upserted,
            metadata={"steps": step_outputs},
        )
    except Exception as exc:
        fail_score_run(
            score_run_id=score_run_id,
            error_message=str(exc),
            metadata={"steps": step_outputs},
        )
        raise

    summary = {
        "game_date": game_date,
        "score_run_id": score_run_id,
        "run_type": run_type,
        "rows_upserted_total": total_upserted,
        "steps": step_outputs,
    }

    print("📊 Feature build summary:")
    print(json.dumps(summary, indent=2, default=str))
    return summary


def run_build_features(date: str | None = None, all_dates: bool = False) -> list[dict[str, Any]]:
    dates = _resolve_dates(target_date=date, all_dates=all_dates)
    if not dates:
        print("⚠️ No dates available to build features.")
        return []

    run_type = "overnight_features" if all_dates else "manual_features"
    results: list[dict[str, Any]] = []
    for game_date in dates:
        results.append(_run_for_date(game_date=game_date, run_type=run_type))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Build daily feature snapshots")
    parser.add_argument("--date", type=str, help="Game date (YYYY-MM-DD)")
    parser.add_argument(
        "--all-dates",
        action="store_true",
        help="Build features for all distinct dates in games table",
    )
    args = parser.parse_args()

    results = run_build_features(date=args.date, all_dates=args.all_dates)
    print(f"\n✅ Completed feature build runs: {len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
