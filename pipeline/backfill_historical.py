"""
Historical backfill runner for multi-year MLB data seeding.

Smart by default: checks what already exists in the DB per date and skips
stages that are already populated. Use --force to override and redo everything.

Performance modes
-----------------
Default (--bulk, enabled automatically):
  Fetches the entire Statcast range ONCE in monthly chunks, then slices per
  date in memory. Eliminates thousands of repeated API calls — typical 6-month
  backfill drops from 6+ hours to ~15–20 minutes.

Legacy (--no-bulk):
  Original per-day Statcast pulls; kept for compatibility or when memory is
  constrained.

Concurrency:
  Features, scoring, and grading run in a thread pool (--workers, default 4).
  Statcast fetch is always single-threaded to avoid rate-limiting.

Examples:
  # Fast bulk backfill (recommended):
  python backfill_historical.py --start-date 2023-03-30 --end-date 2025-10-01 --build-features --score --all-markets --grade

  # Force re-score even if scores exist:
  python backfill_historical.py --start-date 2023-06-01 --end-date 2023-06-30 --score --all-markets --force

  # Legacy per-day mode:
  python backfill_historical.py --start-date 2024-04-01 --end-date 2024-04-30 --no-bulk
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from build_features import run_build_features
from db.database import query
from fetchers.lineups import fetch_lineups_for_date
from fetchers.pitchers import compute_pitcher_stats_from_df, fetch_daily_pitcher_stats
from fetchers.schedule import fetch_todays_games, fetch_umpire_assignments
from fetchers.statcast import (
    compute_batter_stats_for_date,
    fetch_daily_batter_stats,
    fetch_statcast_bulk,
)
from grade_results import grade_results_for_date
from score_markets import score_markets
from db.database import upsert_many


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
    rows = query("SELECT COUNT(*) AS cnt FROM mlb_games WHERE game_date = ?", (game_date,))
    return bool(rows and int(rows[0].get("cnt", 0)) > 0)


def _has_batter_stats(game_date: str) -> bool:
    rows = query("SELECT COUNT(*) AS cnt FROM mlb_batter_stats WHERE stat_date = ?", (game_date,))
    return bool(rows and int(rows[0].get("cnt", 0)) > 0)


def _has_pitcher_stats(game_date: str) -> bool:
    rows = query("SELECT COUNT(*) AS cnt FROM mlb_pitcher_stats WHERE stat_date = ?", (game_date,))
    return bool(rows and int(rows[0].get("cnt", 0)) > 0)


def _has_features(game_date: str) -> bool:
    rows = query("SELECT COUNT(*) AS cnt FROM mlb_batter_daily_features WHERE game_date = ?", (game_date,))
    return bool(rows and int(rows[0].get("cnt", 0)) > 0)


def _has_scores(game_date: str) -> bool:
    rows = query(
        "SELECT COUNT(*) AS cnt FROM mlb_model_scores WHERE game_date = ? AND COALESCE(is_active, 1) = 1",
        (game_date,),
    )
    return bool(rows and int(rows[0].get("cnt", 0)) > 0)


def _has_grades(game_date: str) -> bool:
    rows = query("SELECT COUNT(*) AS cnt FROM mlb_market_outcomes WHERE game_date = ?", (game_date,))
    return bool(rows and int(rows[0].get("cnt", 0)) > 0)


def _get_pitcher_ids(game_date: str) -> list[int]:
    games = query(
        "SELECT home_pitcher_id, away_pitcher_id FROM mlb_games WHERE game_date = ?",
        (game_date,),
    )
    ids: list[int] = []
    for g in games:
        if g.get("home_pitcher_id"):
            ids.append(int(g["home_pitcher_id"]))
        if g.get("away_pitcher_id"):
            ids.append(int(g["away_pitcher_id"]))
    return sorted(set(ids))


# ---------------------------------------------------------------------------
# Per-day processor (used both by bulk and legacy paths)
# ---------------------------------------------------------------------------

def _process_day(
    game_date: str,
    *,
    bulk_df: pd.DataFrame | None,
    include_lineups: bool,
    build_features: bool,
    score: bool,
    grade: bool,
    all_markets: bool,
    market: str,
    skip_fetch: bool,
    force: bool,
) -> dict[str, Any]:
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
            if bulk_df is not None:
                # Fast path: slice in-memory bulk DataFrame
                batter_rows = compute_batter_stats_for_date(bulk_df, game_date)
                count = upsert_many("mlb_batter_stats", batter_rows, ["player_id", "stat_date", "window_days"])
                day_summary["batter_rows"] = count
            else:
                batter_rows = fetch_daily_batter_stats(as_of_date=game_date) or []
                day_summary["batter_rows"] = len(batter_rows)
        else:
            day_summary["skipped_stages"].append("batter_stats")

        if force or not _has_pitcher_stats(game_date):
            pitcher_ids = _get_pitcher_ids(game_date)
            if pitcher_ids:
                if bulk_df is not None:
                    # Fast path: filter in-memory bulk DataFrame
                    count = compute_pitcher_stats_from_df(bulk_df, pitcher_ids, game_date)
                    day_summary["pitcher_rows"] = count
                else:
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

    return day_summary


# ---------------------------------------------------------------------------
# Main backfill runner
# ---------------------------------------------------------------------------

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
    bulk: bool = True,
    workers: int = 4,
) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    skipped_dates = 0

    if force:
        print("🔄 --force: will redo all requested stages even if data exists")
    if skip_fetch:
        print("⏩ --skip-fetch: skipping raw data fetching (games/umpires/batters/pitchers)")

    # ------------------------------------------------------------------
    # Bulk Statcast pre-fetch
    # ------------------------------------------------------------------
    bulk_df: pd.DataFrame | None = None
    if bulk and not skip_fetch:
        # Pull 30 extra days before start so rolling windows are correct on day 1
        padded_start = (_parse_date(start_date) - timedelta(days=30)).strftime(DATE_FMT)
        print(f"\n🚀 Bulk Statcast pre-fetch: {padded_start} → {end_date}")
        bulk_df = fetch_statcast_bulk(padded_start, end_date)
        if bulk_df.empty:
            print("  ⚠️  Bulk fetch returned no data — falling back to per-day fetch")
            bulk_df = None
        else:
            print(f"  ✅ In-memory cache ready: {len(bulk_df):,} pitches\n")

    # ------------------------------------------------------------------
    # Quick skip-check and date list
    # ------------------------------------------------------------------
    dates_to_process: list[str] = []
    for game_date in _iter_dates(start_date, end_date):
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
        dates_to_process.append(game_date)

    print(f"\n📋 {len(dates_to_process)} dates to process, {skipped_dates} already complete")
    if not dates_to_process:
        return {
            "start_date": start_date,
            "end_date": end_date,
            "days": skipped_dates,
            "success_days": 0,
            "skipped_days": skipped_dates,
            "failed_days": 0,
            "failures": [],
        }

    # ------------------------------------------------------------------
    # Statcast stages (batter + pitcher) are computed from the bulk df
    # and are fast enough to run sequentially. Features/score/grade are
    # parallelised since they hit the DB and CPU rather than external APIs.
    # ------------------------------------------------------------------

    # Phase 1 — fetch stages (sequential, bulk df slice per day)
    fetch_results: dict[str, dict] = {}
    for i, game_date in enumerate(dates_to_process, 1):
        print(f"\n{'=' * 70}")
        print(f"📚 BACKFILL {game_date}  [{i}/{len(dates_to_process)}]")
        print("=" * 70)
        try:
            result = _process_day(
                game_date,
                bulk_df=bulk_df,
                include_lineups=include_lineups,
                build_features=False,   # deferred to Phase 2
                score=False,
                grade=False,
                all_markets=all_markets,
                market=market,
                skip_fetch=skip_fetch,
                force=force,
            )
            fetch_results[game_date] = result
            skipped_str = (
                f" (skipped: {', '.join(result['skipped_stages'])})"
                if result["skipped_stages"] else ""
            )
            print(
                f"✅ Fetched {game_date}{skipped_str}: "
                f"batters={result['batter_rows']}, pitchers={result['pitcher_rows']}"
            )
        except Exception as exc:
            failures.append({"game_date": game_date, "error": str(exc)})
            fetch_results[game_date] = {}
            print(f"❌ Fetch failed {game_date}: {exc}")

    # Phase 2 — features / score / grade (parallelised)
    if build_features or score or grade:
        phase2_dates = [d for d in dates_to_process if d in fetch_results and fetch_results[d]]
        print(f"\n⚡ Phase 2 — features/score/grade for {len(phase2_dates)} dates ({workers} workers)")

        def _phase2(game_date: str) -> dict[str, Any]:
            return _process_day(
                game_date,
                bulk_df=None,           # no Statcast needed in Phase 2
                include_lineups=False,
                build_features=build_features,
                score=score,
                grade=grade,
                all_markets=all_markets,
                market=market,
                skip_fetch=True,        # raw data already done in Phase 1
                force=force,
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_date = {pool.submit(_phase2, d): d for d in phase2_dates}
            for future in as_completed(future_to_date):
                game_date = future_to_date[future]
                try:
                    p2 = future.result()
                    # Merge Phase 2 results into the Phase 1 summary
                    base = fetch_results.get(game_date, {})
                    base["feature_runs"] = p2.get("feature_runs", 0)
                    base["score_rows"] = p2.get("score_rows", 0)
                    base["grade_outcomes"] = p2.get("grade_outcomes", 0)
                    base["skipped_stages"].extend(p2.get("skipped_stages", []))
                    summaries.append(base)
                    print(
                        f"  ✅ {game_date}: features={p2['feature_runs']}, "
                        f"scores={p2['score_rows']}, grades={p2['grade_outcomes']}"
                    )
                except Exception as exc:
                    failures.append({"game_date": game_date, "error": str(exc)})
                    print(f"  ❌ Phase 2 failed {game_date}: {exc}")
    else:
        # No Phase 2 — just collect fetch results
        for game_date in dates_to_process:
            if fetch_results.get(game_date):
                summaries.append(fetch_results[game_date])

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
    parser.add_argument(
        "--no-bulk",
        action="store_true",
        help="Disable bulk Statcast pre-fetch (falls back to per-day pulls; slower but lower memory)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Thread pool size for Phase 2 (features/score/grade). Default: 4",
    )

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
        bulk=not args.no_bulk,
        workers=args.workers,
    )
    print("\n📦 Backfill summary:")
    print(summary)
    return 0 if summary["failed_days"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
