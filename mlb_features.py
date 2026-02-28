"""Root-level entry point for mlb-feature-engine Railway service."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pipeline"))
from main_features import run_scheduler, job_build_features, _today_et

if __name__ == "__main__":
    mode = os.getenv("MODE", "schedule")
    if mode == "daily":
        job_build_features(_today_et())
    elif mode == "date":
        job_build_features(os.getenv("DATE") or _today_et())
    else:
        run_scheduler()
