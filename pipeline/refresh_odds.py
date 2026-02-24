"""
Refresh odds and persist normalized market rows.

Usage:
    python refresh_odds.py --date YYYY-MM-DD
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime

from db.database import complete_score_run, create_score_run, fail_score_run
from fetchers.odds import fetch_hr_props


def _today_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def refresh_odds(game_date: str) -> dict:
    score_run_id = create_score_run(
        run_type="odds_refresh",
        game_date=game_date,
        market=None,
        triggered_by="refresh_odds",
        metadata={"job": "refresh_odds"},
    )

    print("\n" + "=" * 72)
    print(f"💰 REFRESH ODDS FOR {game_date} (score_run_id={score_run_id})")
    print("=" * 72)

    try:
        rows = fetch_hr_props()
        summary = {
            "game_date": game_date,
            "score_run_id": score_run_id,
            "rows_collected": len(rows),
            "status": "completed",
        }
        complete_score_run(
            score_run_id=score_run_id,
            status="completed",
            rows_scored=len(rows),
            metadata=summary,
        )
        return summary
    except Exception as exc:
        fail_score_run(
            score_run_id=score_run_id,
            error_message=str(exc),
            metadata={"job": "refresh_odds", "game_date": game_date},
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh odds snapshots")
    parser.add_argument("--date", type=str, help="Target date YYYY-MM-DD (defaults to today)")
    args = parser.parse_args()

    game_date = args.date or _today_str()
    summary = refresh_odds(game_date)
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
