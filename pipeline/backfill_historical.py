"""
Historical backfill runner for multi-year MLB data seeding.

Example:
  python backfill_historical.py --start-date 2023-03-30 --end-date 2025-10-01 --build-features --score --grade
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from typing import Any

from build_features import run_build_features
from fetchers.lineups import fetch_lineups_for_date
from fetchers.pitchers import fetch_daily_pitcher_stats
from fetchers.schedule import fetch_todays_games, fetch_umpire_assignments
from fetchers.statcast import fetch_daily_batter_stats
from grade_results import grade_results_for_date
from score_markets import score_markets


DATE_FMT = "%Y-%m-%d"


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, DATE_FMT)


def _iter_dates(start_date: str, end_date: str):
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    current = start
    while current <= end:
        yield current.strftime(DATE_FMT)
        current += timedelta(days=1)


def run_backfill(
    start_date: str,
    end_date: str,
    include_lineups: bool = False,
    build_features: bool = False,
    score: bool = False,
    grade: bool = False,
    all_markets: bool = True,
    market: str = "HR",
) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for game_date in _iter_dates(start_date, end_date):
        print("\n" + "=" * 70)
        print(f"📚 BACKFILL {game_date}")
        print("=" * 70)

        day_summary: dict[str, Any] = {
            "game_date": game_date,
            "games": 0,
            "umpires": 0,
            "lineups": 0,
            "batter_rows": 0,
            "pitcher_rows": 0,
            "feature_runs": 0,
            "score_rows": 0,
            "grade_outcomes": 0,
        }

        try:
            games = fetch_todays_games(game_date)
            day_summary["games"] = len(games)

            umpire_map = fetch_umpire_assignments(game_date)
            day_summary["umpires"] = len(umpire_map)

            if include_lineups:
                lineup_result = fetch_lineups_for_date(game_date)
                day_summary["lineups"] = int(lineup_result.get("inserted", 0))

            batter_rows = fetch_daily_batter_stats(as_of_date=game_date) or []
            day_summary["batter_rows"] = len(batter_rows)

            pitcher_ids: list[int] = []
            for game in games:
                if game.get("home_pitcher_id"):
                    pitcher_ids.append(int(game["home_pitcher_id"]))
                if game.get("away_pitcher_id"):
                    pitcher_ids.append(int(game["away_pitcher_id"]))
            pitcher_ids = sorted(set(pitcher_ids))
            day_summary["pitcher_rows"] = int(fetch_daily_pitcher_stats(pitcher_ids, as_of_date=game_date))

            if build_features:
                feature_summary = run_build_features(date=game_date, all_dates=False)
                day_summary["feature_runs"] = len(feature_summary)

            if score:
                score_summary = score_markets(
                    game_date=game_date,
                    market=market,
                    all_markets=all_markets,
                    triggered_by="backfill_historical",
                )
                day_summary["score_rows"] = sum(int(row.get("rows_written", 0)) for row in score_summary)

            if grade:
                grade_summary = grade_results_for_date(game_date)
                day_summary["grade_outcomes"] = int(grade_summary.get("outcomes_upserted", 0))

            summaries.append(day_summary)
            print(f"✅ Completed {game_date}: {day_summary}")
        except Exception as exc:  # noqa: BLE001
            failure = {"game_date": game_date, "error": str(exc)}
            failures.append(failure)
            print(f"❌ Failed {game_date}: {exc}")

    return {
        "start_date": start_date,
        "end_date": end_date,
        "days": len(summaries) + len(failures),
        "success_days": len(summaries),
        "failed_days": len(failures),
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill historical MLB data across a date range")
    parser.add_argument("--start-date", required=True, help="Inclusive start date YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="Inclusive end date YYYY-MM-DD")
    parser.add_argument("--lineups", action="store_true", help="Attempt lineup snapshots (optional)")
    parser.add_argument("--build-features", action="store_true", help="Build feature snapshots per date")
    parser.add_argument("--score", action="store_true", help="Run scoring per date")
    parser.add_argument("--grade", action="store_true", help="Run result grading per date")
    parser.add_argument("--market", default="HR", help="Single market for --score when not using --all-markets")
    parser.add_argument("--all-markets", action="store_true", help="Use default market bundle for --score")

    args = parser.parse_args()
    summary = run_backfill(
        start_date=args.start_date,
        end_date=args.end_date,
        include_lineups=args.lineups,
        build_features=args.build_features,
        score=args.score,
        grade=args.grade,
        all_markets=args.all_markets,
        market=args.market,
    )
    print("\n📦 Backfill summary:")
    print(summary)
    return 0 if summary["failed_days"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
