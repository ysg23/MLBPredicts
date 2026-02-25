"""
Score one or many markets for a date.
"""
from __future__ import annotations

import argparse
import importlib
import json
from datetime import datetime, timezone
from typing import Any

from alerts import send_market_alerts
from db.database import complete_score_run, create_score_run, fail_score_run
from scoring.base_engine import score_market_for_date


MARKET_MODULES = {
    "HR": "scoring.hr_model",
    "K": "scoring.k_model",
    "HITS_1P": "scoring.hits_model",
    "HITS_LINE": "scoring.hits_model",
    "TB_LINE": "scoring.tb_model",
    "OUTS_RECORDED": "scoring.outs_recorded_model",
    "ML": "scoring.ml_model",
    "TOTAL": "scoring.totals_model",
    "F5_ML": "scoring.f5_ml_model",
    "F5_TOTAL": "scoring.f5_total_model",
    "TEAM_TOTAL": "scoring.team_totals_model",
}

DEFAULT_ALL_MARKETS = [
    "HR",
    "K",
    "HITS_1P",
    "HITS_LINE",
    "TB_LINE",
    "OUTS_RECORDED",
]


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_market_module(market: str):
    module_path = MARKET_MODULES.get(market)
    if not module_path:
        raise KeyError(f"No module registered for market={market}")
    return importlib.import_module(module_path)


def score_one_market(
    *,
    market: str,
    game_date: str,
    only_game_id: int | None = None,
    triggered_by: str = "manual_score",
) -> dict[str, Any]:
    market = market.upper()
    market_module = _load_market_module(market)
    if hasattr(market_module, "TARGET_MARKET"):
        setattr(market_module, "TARGET_MARKET", market)
    season = int(game_date[:4])
    score_run_id = create_score_run(
        run_type="manual_score",
        game_date=game_date,
        market=market,
        triggered_by=triggered_by,
        metadata={"only_game_id": only_game_id},
    )
    try:
        saved = score_market_for_date(
            market_module=market_module,
            game_date=game_date,
            season=season,
            only_game_id=only_game_id,
            score_run_id=score_run_id,
            supersede_existing=True,
        )
        result = {
            "market": market,
            "score_run_id": score_run_id,
            "rows_written": int(saved),
            "status": "completed",
        }
        complete_score_run(
            score_run_id=score_run_id,
            status="completed",
            rows_scored=int(saved),
            metadata=result,
        )
        return result
    except Exception as exc:
        fail_score_run(
            score_run_id=score_run_id,
            error_message=str(exc),
            metadata={"market": market, "only_game_id": only_game_id},
        )
        return {
            "market": market,
            "score_run_id": score_run_id,
            "rows_written": 0,
            "status": "failed",
            "error": str(exc),
        }


def score_markets(
    *,
    game_date: str,
    market: str | None = None,
    all_markets: bool = False,
    only_game_id: int | None = None,
    triggered_by: str = "manual_score",
    send_alerts: bool = False,
) -> list[dict[str, Any]]:
    if all_markets:
        markets = DEFAULT_ALL_MARKETS
    elif market:
        markets = [market.upper()]
    else:
        raise ValueError("Either market or all_markets must be provided")

    results: list[dict[str, Any]] = []
    for mkt in markets:
        result = score_one_market(
            market=mkt,
            game_date=game_date,
            only_game_id=only_game_id,
            triggered_by=triggered_by,
        )
        if send_alerts and str(result.get("status", "")).lower() == "completed":
            try:
                result["alert"] = send_market_alerts(game_date=game_date, market=mkt)
            except Exception as exc:
                result["alert"] = {"sent": False, "reason": f"error:{exc}"}
        results.append(result)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Score one or many betting markets")
    parser.add_argument("--date", type=str, help="Target date (YYYY-MM-DD), defaults to today")
    parser.add_argument("--market", type=str, help="Single market code")
    parser.add_argument("--all-markets", action="store_true", help="Score default high-ROI market set")
    parser.add_argument("--game-id", type=int, help="Optional game_id scope")
    parser.add_argument("--send-alerts", action="store_true", help="Send Discord alerts for scored markets")
    args = parser.parse_args()

    date_str = args.date or _today_str()
    results = score_markets(
        game_date=date_str,
        market=args.market,
        all_markets=args.all_markets,
        only_game_id=args.game_id,
        send_alerts=args.send_alerts,
    )
    has_failures = any(str(item.get("status", "")).lower() == "failed" for item in results)
    print(json.dumps({"game_date": date_str, "results": results}, indent=2, default=str))
    return 1 if has_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
