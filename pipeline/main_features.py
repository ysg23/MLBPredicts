"""
mlb-feature-engine — always-on scheduler

Builds daily feature snapshots (batter, pitcher, team, game context)
for today's upcoming games. Runs once per day at 09:30 ET.

Railway start command: python main_features.py
MODE env var:
  schedule  (default) — long-running scheduler
  daily     — build features for today and exit
  date      — build features for DATE env var and exit
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import pytz
import schedule

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [features] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _today_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def _is_game_day(date_str: str | None = None) -> bool:
    from db.database import query
    date_str = date_str or _today_et()
    rows = query(
        "SELECT COUNT(*) AS cnt FROM mlb_games WHERE game_date = ? AND status != 'cancelled'",
        (date_str,),
    )
    return bool(rows and rows[0]["cnt"] > 0)


def _safe_run(name: str, fn, *args, **kwargs):
    log.info("starting job: %s", name)
    try:
        fn(*args, **kwargs)
        log.info("finished job: %s", name)
    except Exception as exc:
        log.exception("job failed: %s — %s", name, exc)


# ── Jobs ─────────────────────────────────────────────────────────────────────

def job_build_features(date: str | None = None):
    """Build all four feature tables for the given date."""
    date = date or _today_et()
    if not _is_game_day(date):
        log.info("no games on %s — skipping feature build", date)
        return
    log.info("building features for %s", date)
    from build_features import run_build_features
    results = run_build_features(date=date)
    total = sum(int(r.get("rows_upserted_total", 0)) for r in results)
    log.info("feature build complete: %d rows upserted across %d tables", total, len(results))


# ── Scheduler ────────────────────────────────────────────────────────────────

def run_scheduler():
    log.info("mlb-feature-engine scheduler starting (ET timezone)")

    # Build features at 09:30 ET — after Statcast data lands (~5:30 AM ET)
    # and before scoring runs at 10:30 ET
    schedule.every().day.at("09:30").do(lambda: _safe_run("build_features", job_build_features))

    # Run once on startup so a fresh deploy catches up immediately
    _safe_run("build_features_startup", job_build_features)

    log.info("scheduler running — press Ctrl+C to stop")
    while True:
        schedule.run_pending()
        time.sleep(60)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = os.getenv("MODE", "schedule")

    if mode == "daily":
        job_build_features(_today_et())
    elif mode == "date":
        target = os.getenv("DATE") or _today_et()
        job_build_features(target)
    else:
        run_scheduler()
