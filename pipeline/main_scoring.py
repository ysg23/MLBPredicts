"""
mlb-scoring-engine — always-on scheduler

Scores all markets for today's games, rescores after lineup confirmation,
grades yesterday's results, and sends Discord alerts.

Railway start command: python main_scoring.py
MODE env var:
  schedule  (default) — long-running scheduler
  score     — score today's markets and exit
  rescore   — rescore on confirmed lineups and exit
  grade     — grade yesterday's results and exit
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
    format="%(asctime)s [scoring] %(levelname)s %(message)s",
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

def job_score(date: str | None = None):
    """Score all markets for today's games (~10:30 AM ET)."""
    date = date or _today_et()
    if not _is_game_day(date):
        log.info("no games on %s — skipping scoring", date)
        return
    log.info("scoring all markets for %s", date)
    from score_markets import score_markets
    results = score_markets(
        game_date=date,
        all_markets=True,
        send_alerts=True,
        triggered_by="scheduler",
    )
    total = sum(int(r.get("rows_written", 0)) for r in results)
    log.info("scoring complete: %d markets, %d rows", len(results), total)


def job_rescore(date: str | None = None):
    """Rescore on confirmed lineups (~2:00 PM ET)."""
    date = date or _today_et()
    if not _is_game_day(date):
        return
    log.info("rescoring on confirmed lineups for %s", date)
    from rescore_on_lineup import rescore_on_lineup
    rows = rescore_on_lineup(game_date=date, send_alerts=True)
    log.info("rescore complete: %d rows updated", rows)


def job_grade(date: str | None = None):
    """Grade results for yesterday's games (~12:30 AM ET)."""
    date = date or _yesterday_et()
    log.info("grading results for %s", date)
    from grade_results import run_grading
    run_grading(date)
    log.info("grading complete for %s", date)


# ── Scheduler ────────────────────────────────────────────────────────────────

def run_scheduler():
    log.info("mlb-scoring-engine scheduler starting (ET timezone)")

    # Morning score — after features are built at 09:30 ET
    schedule.every().day.at("10:30").do(lambda: _safe_run("score", job_score))

    # Rescore on confirmed lineups (usually posted 1-2 hrs before first pitch)
    schedule.every().day.at("14:00").do(lambda: _safe_run("rescore", job_rescore))

    # Post-game grading + alerts for yesterday's results
    schedule.every().day.at("00:30").do(lambda: _safe_run("grade", job_grade))

    log.info("scheduler running — press Ctrl+C to stop")
    while True:
        schedule.run_pending()
        time.sleep(60)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = os.getenv("MODE", "schedule")

    if mode == "score":
        _safe_run("score", job_score)
    elif mode == "rescore":
        _safe_run("rescore", job_rescore)
    elif mode == "grade":
        _safe_run("grade", job_grade)
    else:
        run_scheduler()
