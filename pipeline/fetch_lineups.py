"""
CLI entrypoint for lineup snapshots.

Usage:
    python fetch_lineups.py --date YYYY-MM-DD
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime

from fetchers.lineups import fetch_lineups_for_date


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch MLB lineup snapshots")
    parser.add_argument("--date", type=str, help="Target date (YYYY-MM-DD), defaults to today")
    args = parser.parse_args()

    date_str = args.date or datetime.utcnow().strftime("%Y-%m-%d")
    result = fetch_lineups_for_date(date_str)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
