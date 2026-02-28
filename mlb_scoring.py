"""Root-level entry point for mlb-scoring-engine Railway service."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pipeline"))
from main_scoring import run_scheduler, job_score, job_rescore, job_grade, _safe_run

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
