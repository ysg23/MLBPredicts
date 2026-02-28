"""Root-level entry point for mlb-data-ingester Railway service."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pipeline"))
from main_ingester import run_scheduler, run_daily_ingest, job_outcomes, _safe_run

if __name__ == "__main__":
    mode = os.getenv("MODE", "schedule")
    if mode == "daily":
        run_daily_ingest()
    elif mode == "outcomes":
        _safe_run("outcomes", job_outcomes)
    else:
        run_scheduler()
