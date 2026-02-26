"""
Historical backfill runner for multi-year MLB data seeding.

Smart by default: checks what already exists in the DB per date and skips
stages that are already populated. Use --force to override and redo everything.

Examples:
  # Run everything, auto-skipping what's already done:
  python backfill_historical.py --start-date 2023-03-30 --end-date 2025-10-01 --build-features --score --all-markets --grade

  # Force re-score even if scores exist:
  python backfill_historical.py --start-date 2023-06-01 --end-date 2023-06-30 --score --all-markets --force
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from typing import Any

from build_features import run_build_features
from db.database import query
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


# ---------------------------------------------------------------------------
# Existence checks — one per stage, fast COUNT queries
# ---------------------------------------------------------------------------

def _has_games(game_date: str) -> bool:
    rows = query("SELECT COUNT(*) AS cnt FROM games WHERE game_date = ?", (game_date,))
    return bool(rows and int(rows[0].get("cnt", 0)) > 0)


def _has_batter_stats(game_date: str) -> bool:
    rows = query("SELECT COUNT(*) AS cnt FROM batter_stats WHERE stat_date = ?", (game_date,))
    return bool(rows and int(rows[0].get("cnt", 0)) > 0)


def _has_pitcher_stats(game_date: str) -> bool:
    rows = query("SELECT COUNT(*) AS cnt FROM pitcher_stats WHERE stat_date = ?", (game_date,))
    return bool(rows and int(rows[0].get("cnt", 0)) > 0)


def _has_features(game_date: str) -> bool:
    rows = query("SELECT COUNT(*) AS cnt FROM batter_daily_features WHERE game_date = ?", (game_date,))
    return bool(rows and int(rows[0].get("cnt", 0)) > 0)


def _has_scores(game_date: str) -> bool:
    rows = query(
        "SELECT COUNT(*) AS cnt FROM model_scores WHERE game_date = ? AND COALESCE(is_active, 1) = 1",
        (game_date,),
    )
    return bool(rows and int(rows[0].get("cnt", 0)) > 0)


def _has_grades(game_date: str) -> bool:
    rows = query("SELECT COUNT(*) AS cnt FROM market_outcomes WHERE game_date = ?", (game_date,))
    return bool(rows and int(rows[0].get("cnt", 0)) > 0)


def run_backfill(
    start_date: str,
    end_date: str,
    include_lineups: bool = False,
    build_features: bool = False,
    score: bool = False,
    grade: bool = False,
    all_markets: bool = True,
    market: str = "HR",
    skip_fetch: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    skipped_dates = 0

    if force:
        print("🔄 --force: will redo all requested stages even if data exists")
    elif skip_fetch:
        print("⏩ --skip-fetch: skipping raw data fetching (games/umpires/batters/pitchers)")

    for game_date in _iter_dates(start_date, end_date):
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
            "skipped_stages": [],
        }

        # Quick check: if ALL requested stages already have data, skip entire date
        if not force:
            all_done = True
            if not skip_fetch:
                if not (_has_games(game_date) and _has_batter_stats(game_date) and _has_pitcher_stats(game_date)):
                    all_done = False
            if build_features and not _has_features(game_date):
                all_done = False
            if score and not _has_scores(game_date):
                all_done = False
            if grade and not _has_grades(game_date):
                all_done = False
            if all_done:
                skipped_dates += 1
                if skipped_dates <= 5 or skipped_dates % 50 == 0:
                    print(f"⏭️  {game_date} — all stages complete, skipping ({skipped_dates} skipped so far)")
                continue

        print("\n" + "=" * 70)
        print(f"📚 BACKFILL {game_date}")
        print("=" * 70)

        try:
            # --- Raw data fetching ---
            if not skip_fetch:
                if force or not _has_games(game_date):
                    games = fetch_todays_games(game_date)
                    day_summary["games"] = len(games)

                    umpire_map = fetch_umpire_assignments(game_date)
                    day_summary["umpires"] = len(umpire_map)
                else:
                    day_summary["skipped_stages"].append("games")

                if include_lineups:
                    lineup_result = fetch_lineups_for_date(game_date)
                    day_summary["lineups"] = int(lineup_result.get("inserted", 0))

                if force or not _has_batter_stats(game_date):
                    batter_rows = fetch_daily_batter_stats(as_of_date=game_date) or []
                    day_summary["batter_rows"] = len(batter_rows)
                else:
                    day_summary["skipped_stages"].append("batter_stats")

                if force or not _has_pitcher_stats(game_date):
                    # Need game list for pitcher IDs
                    games_for_pitchers = query(
                        "SELECT home_pitcher_id, away_pitcher_id FROM games WHERE game_date = ?",
                        (game_date,),
                    )
                    pitcher_ids: list[int] = []
                    for g in games_for_pitchers:
                        if g.get("home_pitcher_id"):
                            pitcher_ids.append(int(g["home_pitcher_id"]))
                        if g.get("away_pitcher_id"):
                            pitcher_ids.append(int(g["away_pitcher_id"]))
                    pitcher_ids = sorted(set(pitcher_ids))
                    if pitcher_ids:
                        day_summary["pitcher_rows"] = int(fetch_daily_pitcher_stats(pitcher_ids, as_of_date=game_date))
                else:
                    day_summary["skipped_stages"].append("pitcher_stats")

            # --- Feature building ---
            if build_features:
                if force or not _has_features(game_date):
                    feature_summary = run_build_features(date=game_date, all_dates=False)
                    day_summary["feature_runs"] = len(feature_summary)
                else:
                    day_summary["skipped_stages"].append("features")

            # --- Scoring ---
            if score:
                if force or not _has_scores(game_date):
                    score_summary = score_markets(
                        game_date=game_date,
                        market=market,
                        all_markets=all_markets,
                        triggered_by="backfill_historical",
                    )
                    day_summary["score_rows"] = sum(int(row.get("rows_written", 0)) for row in score_summary)
                else:
                    day_summary["skipped_stages"].append("scores")

            # --- Grading ---
            if grade:
                if force or not _has_grades(game_date):
                    grade_summary = grade_results_for_date(game_date)
                    day_summary["grade_outcomes"] = int(grade_summary.get("outcomes_upserted", 0))
                else:
                    day_summary["skipped_stages"].append("grades")

            summaries.append(day_summary)
            skipped_str = f" (skipped: {', '.join(day_summary['skipped_stages'])})" if day_summary["skipped_stages"] else ""
            print(f"✅ Completed {game_date}{skipped_str}: scores={day_summary['score_rows']}, grades={day_summary['grade_outcomes']}")
        except Exception as exc:  # noqa: BLE001
            failure = {"game_date": game_date, "error": str(exc)}
            failures.append(failure)
            print(f"❌ Failed {game_date}: {exc}")

    return {
        "start_date": start_date,
        "end_date": end_date,
        "days": len(summaries) + len(failures) + skipped_dates,
        "success_days": len(summaries),
        "skipped_days": skipped_dates,
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
    parser.add_argument("--skip-fetch", action="store_true", help="Skip raw data fetching entirely")
    parser.add_argument("--force", action="store_true", help="Force redo all stages even if data exists")

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
        skip_fetch=args.skip_fetch,
        force=args.force,
    )
    print("\n📦 Backfill summary:")
    print(summary)
    return 0 if summary["failed_days"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
