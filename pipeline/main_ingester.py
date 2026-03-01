"""
mlb-data-ingester — always-on scheduler

Fetches all raw data for today's games: schedule, Statcast, pitchers,
umpires, lineups, weather, odds, and post-game outcomes.

Railway start command: python main_ingester.py
MODE env var:
  schedule  (default) — long-running scheduler
  daily     — run full ingest for today and exit
  outcomes  — run only post-game outcomes fetch and exit
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import pytz
import schedule

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ingester] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _now_et() -> datetime:
    return datetime.now(ET)


def _today_et() -> str:
    return _now_et().strftime("%Y-%m-%d")


def _yesterday_et() -> str:
    return (_now_et() - timedelta(days=1)).strftime("%Y-%m-%d")


def _is_game_day(date_str: str | None = None) -> bool:
    """Return True if there are games in mlb_games for the given date."""
    from db.database import query
    date_str = date_str or _today_et()
    rows = query(
        "SELECT COUNT(*) AS cnt FROM mlb_games WHERE game_date = ? AND status != 'cancelled'",
        (date_str,),
    )
    return bool(rows and rows[0]["cnt"] > 0)


def _safe_run(name: str, fn, *args, **kwargs):
    """Run fn, log success/failure, never crash the scheduler."""
    log.info("starting job: %s", name)
    try:
        fn(*args, **kwargs)
        log.info("finished job: %s", name)
    except Exception as exc:
        log.exception("job failed: %s — %s", name, exc)


# ── Jobs ─────────────────────────────────────────────────────────────────────

def job_statcast():
    """Fetch yesterday's Statcast batter/pitcher stats (~5:30 AM ET daily)."""
    from db.pipeline_monitor import pipeline_run, update_source_health
    date = _yesterday_et()
    log.info("fetching statcast for %s", date)
    with pipeline_run("statcast_fetch", service_name="mlb-data-ingester", source="statcast"):
        from fetchers.statcast import fetch_daily_batter_stats
        from fetchers.pitchers import fetch_daily_pitcher_stats
        from fetchers.schedule import fetch_todays_games
        fetch_daily_batter_stats()
        games = fetch_todays_games(date)
        pitcher_ids = [
            pid for g in games
            for pid in [g.get("home_pitcher_id"), g.get("away_pitcher_id")]
            if pid
        ]
        if pitcher_ids:
            fetch_daily_pitcher_stats(pitcher_ids, as_of_date=date)
            log.info("fetched pitcher stats for %d pitchers", len(pitcher_ids))
        update_source_health("statcast", success=True)


def job_schedule():
    """Fetch today's schedule + umpire assignments (~7:00 AM ET)."""
    from db.pipeline_monitor import pipeline_run, update_source_health
    date = _today_et()
    log.info("fetching schedule + umpires for %s", date)
    with pipeline_run("schedule_fetch", service_name="mlb-data-ingester", source="mlb_stats_api") as run:
        from fetchers.schedule import fetch_todays_games, fetch_umpire_assignments
        games = fetch_todays_games(date)
        if games:
            umpires = fetch_umpire_assignments(date)
            log.info("schedule: %d games, %d umpire assignments", len(games), len(umpires))
            run.records_processed = len(games)
        else:
            log.info("no games today (%s)", date)
        update_source_health("mlb_stats_api", success=True)


def job_lineups():
    """Fetch lineup snapshots (~8:30 AM, 12:00 PM, 4:00 PM ET on game days)."""
    if not _is_game_day():
        return
    from db.pipeline_monitor import pipeline_run, update_source_health
    date = _today_et()
    log.info("fetching lineups for %s", date)
    with pipeline_run("lineup_fetch", service_name="mlb-data-ingester", source="mlb_stats_api") as run:
        from fetchers.lineups import fetch_lineups_for_date
        result = fetch_lineups_for_date(date)
        log.info("lineups: %s", result)
        if isinstance(result, dict):
            run.records_processed = result.get("rows_upserted", 0)
        update_source_health("mlb_stats_api", success=True)


def job_weather():
    """Fetch weather for today's games (~8:00 AM, 2:00 PM, 5:00 PM ET on game days)."""
    if not _is_game_day():
        return
    from db.pipeline_monitor import pipeline_run, update_source_health
    date = _today_et()
    log.info("fetching weather for %s", date)
    with pipeline_run("weather_fetch", service_name="mlb-data-ingester", source="weather_api") as run:
        from fetchers.schedule import fetch_todays_games
        from fetchers.weather import fetch_game_weather
        from utils.stadiums import get_stadium_coords
        games = fetch_todays_games(date)
        if games:
            coords = get_stadium_coords()
            fetch_game_weather(games, coords)
            run.records_processed = len(games)
        update_source_health("weather_api", success=True)


def job_odds():
    """Fetch odds for today's markets (~9:00 AM, 12:00 PM, 3:00 PM ET on game days)."""
    if not _is_game_day():
        return
    from db.pipeline_monitor import pipeline_run, update_source_health
    log.info("fetching odds")
    with pipeline_run("odds_fetch", service_name="mlb-data-ingester", source="odds_api"):
        from fetchers.odds import fetch_hr_props
        fetch_hr_props()
        update_source_health("odds_api", success=True)


def job_outcomes():
    """Fetch post-game outcomes for yesterday's games (~12:00 AM ET)."""
    from db.pipeline_monitor import pipeline_run, update_source_health
    date = _yesterday_et()
    log.info("fetching post-game outcomes for %s", date)
    with pipeline_run("outcomes_fetch", service_name="mlb-data-ingester", source="mlb_stats_api"):
        from grade_results import run_grading
        run_grading(date)
        update_source_health("mlb_stats_api", success=True)


def run_daily_ingest(date: str | None = None):
    """Run full ingest sequence for one date (used in 'daily' mode)."""
    date = date or _today_et()
    _safe_run("statcast", job_statcast)
    _safe_run("schedule", job_schedule)
    _safe_run("lineups", job_lineups)
    _safe_run("weather", job_weather)
    _safe_run("odds", job_odds)


# ── Scheduler ────────────────────────────────────────────────────────────────

def run_scheduler():
    log.info("mlb-data-ingester scheduler starting (ET timezone)")

    # Statcast — previous day's data available ~5 AM ET
    schedule.every().day.at("05:30").do(lambda: _safe_run("statcast", job_statcast))

    # Schedule + umpires
    schedule.every().day.at("07:00").do(lambda: _safe_run("schedule", job_schedule))

    # Lineups (3 snapshots: morning, noon, pre-game)
    schedule.every().day.at("08:30").do(lambda: _safe_run("lineups", job_lineups))
    schedule.every().day.at("12:00").do(lambda: _safe_run("lineups", job_lineups))
    schedule.every().day.at("16:00").do(lambda: _safe_run("lineups", job_lineups))

    # Weather (morning + pre-game refresh)
    schedule.every().day.at("08:00").do(lambda: _safe_run("weather", job_weather))
    schedule.every().day.at("14:00").do(lambda: _safe_run("weather", job_weather))
    schedule.every().day.at("17:00").do(lambda: _safe_run("weather", job_weather))

    # Odds (morning + two refreshes)
    schedule.every().day.at("09:00").do(lambda: _safe_run("odds", job_odds))
    schedule.every().day.at("12:30").do(lambda: _safe_run("odds", job_odds))
    schedule.every().day.at("15:30").do(lambda: _safe_run("odds", job_odds))

    # Post-game outcomes (midnight ET — grades yesterday's games)
    schedule.every().day.at("00:00").do(lambda: _safe_run("outcomes", job_outcomes))

    log.info("scheduler running — press Ctrl+C to stop")
    while True:
        schedule.run_pending()
        time.sleep(60)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = os.getenv("MODE", "schedule")

    if mode == "daily":
        run_daily_ingest()
    elif mode == "outcomes":
        _safe_run("outcomes", job_outcomes)
    else:
        run_scheduler()
