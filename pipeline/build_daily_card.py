"""
Phase 6 — Daily card builder.

Reads BET/LEAN signals from mlb_model_scores for a given date and
materializes a structured daily card into mlb_daily_cards.

Usage:
    python build_daily_card.py --date 2025-04-01
    python build_daily_card.py --all
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [daily_card] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def build_daily_card(game_date: str) -> dict:
    """
    Build and persist a daily card for the given game_date.

    Returns a summary dict: {game_date, total_signals, top_plays}.
    """
    from db.database import query, upsert_many

    rows = query(
        """
        SELECT player_name, team_abbr, opponent_team_abbr, market, bet_type,
               line, side, model_score, edge, signal, confidence_band,
               visibility_tier, result
        FROM mlb_model_scores
        WHERE game_date = ? AND is_active = 1 AND signal IN ('BET', 'LEAN')
        ORDER BY model_score DESC
        """,
        (game_date,),
    )

    if not rows:
        log.info("no BET/LEAN signals found for %s — skipping card build", game_date)
        return {"game_date": game_date, "total_signals": 0, "top_plays": 0}

    # ── card_data: full signal list ───────────────────────────────────────────
    card_data = [
        {
            "player_name": r.get("player_name"),
            "team_abbr": r.get("team_abbr"),
            "opponent_team_abbr": r.get("opponent_team_abbr"),
            "market": r.get("market"),
            "bet_type": r.get("bet_type"),
            "line": r.get("line"),
            "side": r.get("side"),
            "model_score": r.get("model_score"),
            "edge": r.get("edge"),
            "signal": r.get("signal"),
            "confidence_band": r.get("confidence_band"),
            "visibility_tier": r.get("visibility_tier"),
            "result": r.get("result"),
        }
        for r in rows
    ]

    # ── top_plays: top 5 BET signals with FREE visibility ────────────────────
    top_plays = [
        entry for entry in card_data
        if entry["signal"] == "BET" and entry["visibility_tier"] == "FREE"
    ][:5]

    # ── summary string ────────────────────────────────────────────────────────
    bet_count = sum(1 for r in rows if r.get("signal") == "BET")
    lean_count = sum(1 for r in rows if r.get("signal") == "LEAN")
    market_counts = Counter(r.get("market") for r in rows if r.get("market"))
    top_market, top_market_count = market_counts.most_common(1)[0] if market_counts else ("N/A", 0)
    unique_markets = len(market_counts)

    # Format date for human-readable string, e.g. "March 27"
    try:
        dt = datetime.strptime(game_date, "%Y-%m-%d")
        date_label = dt.strftime("%B %-d")  # Linux/Mac; falls back below on Windows
    except ValueError:
        date_label = game_date
    except Exception:
        # Windows strftime does not support %-d — use lstrip instead
        try:
            dt = datetime.strptime(game_date, "%Y-%m-%d")
            date_label = f"{dt.strftime('%B')} {dt.day}"
        except Exception:
            date_label = game_date

    summary = (
        f"{date_label} — {bet_count} BET signal{'s' if bet_count != 1 else ''}, "
        f"{lean_count} LEAN signal{'s' if lean_count != 1 else ''} "
        f"across {unique_markets} market{'s' if unique_markets != 1 else ''}. "
        f"Top market: {top_market} ({top_market_count} play{'s' if top_market_count != 1 else ''})."
    )

    # ── upsert into mlb_daily_cards ───────────────────────────────────────────
    upsert_many(
        "mlb_daily_cards",
        [
            {
                "game_date": game_date,
                "card_data": json.dumps(card_data),
                "top_plays": json.dumps(top_plays),
                "summary": summary,
                "published": True,
                "published_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        conflict_cols=["game_date"],
    )

    log.info(
        "built daily card for %s: %d total signals, %d top plays",
        game_date,
        len(rows),
        len(top_plays),
    )
    return {"game_date": game_date, "total_signals": len(rows), "top_plays": len(top_plays)}


def _all_scored_dates() -> list[str]:
    """Return all distinct game_dates that have BET/LEAN signals in mlb_model_scores."""
    from db.database import query

    rows = query(
        """
        SELECT DISTINCT game_date
        FROM mlb_model_scores
        WHERE is_active = 1 AND signal IN ('BET', 'LEAN')
        ORDER BY game_date ASC
        """
    )
    return [str(r["game_date"]) for r in rows]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build MLB daily cards from model scores.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="Build card for a specific date (YYYY-MM-DD)")
    group.add_argument(
        "--all",
        action="store_true",
        help="Build cards for all dates present in mlb_model_scores",
    )
    args = parser.parse_args()

    if args.all:
        dates = _all_scored_dates()
        log.info("building cards for %d dates", len(dates))
        for d in dates:
            try:
                result = build_daily_card(d)
                log.info("  %s: %d signals, %d top plays", d, result["total_signals"], result["top_plays"])
            except Exception as exc:
                log.exception("failed to build card for %s: %s", d, exc)
        log.info("done — processed %d dates", len(dates))
    else:
        result = build_daily_card(args.date)
        print(
            f"game_date={result['game_date']}  "
            f"total_signals={result['total_signals']}  "
            f"top_plays={result['top_plays']}"
        )
