"""
Odds fetcher with backward-compatible HR writes and normalized market writes.

Outputs:
1) Legacy `hr_odds` rows for existing HR scoring flow
2) Normalized `market_odds` rows for the multi-market engine
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import requests

from config import ODDS_API_BASE, ODDS_API_KEY
from clv import capture_closing_lines_for_date
from db.database import get_connection, insert_many, query
from utils.odds_normalizer import (
    SUPPORTED_ODDS_API_MARKETS,
    american_to_implied_prob,
    normalize_event_odds,
)

log = logging.getLogger(__name__)


def _cache_response(source: str, endpoint: str, params_dict: dict, body_dict: dict) -> None:
    """Best-effort INSERT of a raw API response into raw_api_responses. Never raises."""
    try:
        from db.database import get_connection as _get_conn
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO raw_api_responses (source, endpoint, params, response_body, fetched_at, ttl_hours)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                (
                    source,
                    endpoint,
                    json.dumps(params_dict),
                    json.dumps(body_dict),
                    datetime.now(timezone.utc).isoformat(),
                    1,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        log.debug("raw_api_responses cache write failed (non-fatal): %s", exc)


def _event_game_date(event_payload: dict[str, Any]) -> str:
    commence = event_payload.get("commence_time")
    if not commence:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    normalized = commence.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).strftime("%Y-%m-%d")
    except ValueError:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _extract_hr_rows(event_payload: dict[str, Any], fetched_at: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    game_date = _event_game_date(event_payload)
    game_id = None  # retained from legacy flow; game matching happens downstream

    for bookmaker in event_payload.get("bookmakers", []):
        book_name = bookmaker.get("key")
        if not book_name:
            continue

        for market in bookmaker.get("markets", []):
            if market.get("key") != "batter_home_runs":
                continue

            for outcome in market.get("outcomes", []):
                player_name = outcome.get("description", outcome.get("name", ""))
                price = outcome.get("price")
                if price is None:
                    continue

                outcome_name = (outcome.get("name") or "").strip().lower()
                is_over = outcome_name in {"over", "yes"}

                rows.append(
                    {
                        "game_id": game_id,
                        "game_date": game_date,
                        "player_id": 0,  # unresolved player mapping; retained for backward compatibility
                        "player_name": player_name,
                        "sportsbook": book_name,
                        "market": "hr",
                        "over_price": price if is_over else None,
                        "under_price": price if not is_over else None,
                        "implied_prob_over": (
                            round(american_to_implied_prob(price), 4) if is_over else None
                        ),
                        "implied_prob_under": (
                            round(american_to_implied_prob(price), 4) if not is_over else None
                        ),
                        "fetch_time": fetched_at,
                    }
                )
    return rows


def _merge_normalization_summary(total: dict[str, Any], part: dict[str, Any]) -> None:
    total["total_outcomes"] += part.get("total_outcomes", 0)
    total["normalized_rows"] += part.get("normalized_rows", 0)
    total["skipped_unsupported_market"] += part.get("skipped_unsupported_market", 0)
    total["skipped_invalid_price"] += part.get("skipped_invalid_price", 0)
    total["skipped_missing_required"] += part.get("skipped_missing_required", 0)

    dst_counts = total["unsupported_market_counts"]
    src_counts = part.get("unsupported_market_counts", {})
    for key, value in src_counts.items():
        dst_counts[key] = dst_counts.get(key, 0) + value


def _dedupe_market_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        dedupe_key = (
            row.get("game_id"),
            row.get("selection_key"),
            row.get("sportsbook"),
            row.get("source_market_key"),
            row.get("fetched_at"),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(row)
    return deduped


def _mark_best_available_for_fetch(rows: list[dict[str, Any]], fetched_at: str) -> int:
    if not rows:
        return 0

    # Group by normalized selection identity and choose best decimal price.
    grouped_keys: set[tuple[Any, ...]] = set()
    for row in rows:
        grouped_keys.add(
            (
                row.get("game_date"),
                row.get("market"),
                row.get("selection_key"),
                row.get("side"),
                row.get("line"),
            )
        )

    conn = get_connection()
    flagged = 0
    try:
        for game_date, market, selection_key, side, line in grouped_keys:
            candidates = conn.execute(
                """
                SELECT id, price_decimal, odds_decimal
                FROM mlb_market_odds
                WHERE game_date = ?
                  AND market = ?
                  AND (selection_key = ? OR (selection_key IS NULL AND ? IS NULL))
                  AND (side = ? OR (side IS NULL AND ? IS NULL))
                  AND (line = ? OR (line IS NULL AND ? IS NULL))
                  AND fetched_at = ?
                """,
                (
                    game_date,
                    market,
                    selection_key,
                    selection_key,
                    side,
                    side,
                    line,
                    line,
                    fetched_at,
                ),
            ).fetchall()
            if not candidates:
                continue

            candidate_ids = [int(r["id"]) for r in candidates]
            conn.execute(
                f"UPDATE mlb_market_odds SET is_best_available = 0 WHERE id IN ({','.join(['?'] * len(candidate_ids))})",
                tuple(candidate_ids),
            )
            best = max(
                candidates,
                key=lambda r: float(r["price_decimal"] if r["price_decimal"] is not None else (r["odds_decimal"] or 0.0)),
            )
            conn.execute("UPDATE mlb_market_odds SET is_best_available = 1 WHERE id = ?", (int(best["id"]),))
            flagged += 1
        conn.commit()
    finally:
        conn.close()
    return flagged


def fetch_hr_props(sport: str = "baseball_mlb") -> list[dict]:
    """
    Fetch odds and write both:
    - backward-compatible HR rows to `hr_odds`
    - normalized supported-market rows to `market_odds`
    """
    if not ODDS_API_KEY:
        print("  ⚠️  No ODDS_API_KEY set — skipping odds fetch")
        return []

    print("\n💰 Fetching odds (HR + normalized markets)...")

    events_url = f"{ODDS_API_BASE}/sports/{sport}/events"
    events_resp = requests.get(
        events_url,
        params={
            "apiKey": ODDS_API_KEY,
            "dateFormat": "iso",
        },
        timeout=15,
    )
    events_resp.raise_for_status()
    events = events_resp.json()

    print(f"  📋 Found {len(events)} games with odds")

    markets_param = ",".join(SUPPORTED_ODDS_API_MARKETS)
    fetched_at = datetime.now(timezone.utc).isoformat()
    all_hr_rows: list[dict[str, Any]] = []
    all_normalized_rows: list[dict[str, Any]] = []
    normalization_summary: dict[str, Any] = {
        "total_outcomes": 0,
        "normalized_rows": 0,
        "skipped_unsupported_market": 0,
        "skipped_invalid_price": 0,
        "skipped_missing_required": 0,
        "unsupported_market_counts": {},
    }

    for event in events:
        event_id = event.get("id")
        if not event_id:
            continue

        try:
            odds_url = f"{ODDS_API_BASE}/sports/{sport}/events/{event_id}/odds"
            odds_params = {
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": markets_param,
                "dateFormat": "iso",
                "oddsFormat": "american",
            }
            odds_resp = requests.get(
                odds_url,
                params=odds_params,
                timeout=15,
            )
            odds_resp.raise_for_status()
            event_odds = odds_resp.json()
            _cache_response(
                "odds_api",
                f"/sports/{sport}/events/{event_id}/odds",
                {k: v for k, v in odds_params.items() if k != "apiKey"},
                event_odds,
            )
        except Exception as exc:
            print(f"  ⚠️  Could not fetch odds for event {event_id}: {exc}")
            continue

        normalized_rows, summary = normalize_event_odds(event_odds, fetched_at=fetched_at)
        all_normalized_rows.extend(normalized_rows)
        _merge_normalization_summary(normalization_summary, summary)

        all_hr_rows.extend(_extract_hr_rows(event_odds, fetched_at=fetched_at))

    print(f"  ✅ Collected {len(all_hr_rows)} raw HR prop lines")
    consolidated = consolidate_odds(all_hr_rows)

    if consolidated:
        inserted = insert_many("mlb_hr_odds", consolidated)
        print(f"  💾 Saved {inserted} consolidated HR rows to hr_odds")

    deduped_normalized = _dedupe_market_rows(all_normalized_rows)
    touched_dates: set[str] = {str(r.get("game_date")) for r in deduped_normalized if r.get("game_date")}
    if deduped_normalized:
        inserted = insert_many("mlb_market_odds", deduped_normalized)
        best_flagged = _mark_best_available_for_fetch(deduped_normalized, fetched_at=fetched_at)
        print(
            f"  💾 Saved {inserted} normalized rows to market_odds "
            f"({len(deduped_normalized)} attempted; best_available flagged={best_flagged})"
        )
        # Keep closing_lines current during odds refreshes.
        for touched_date in sorted(touched_dates):
            clv_summary = capture_closing_lines_for_date(touched_date)
            print(
                "  📉 Closing snapshot sync: "
                f"date={touched_date} groups={clv_summary.get('groups', 0)} "
                f"upserted={clv_summary.get('upserted', 0)}"
            )

    unsupported_counts = normalization_summary["unsupported_market_counts"]
    if unsupported_counts:
        pairs = sorted(unsupported_counts.items(), key=lambda kv: kv[1], reverse=True)
        top = ", ".join(f"{k}:{v}" for k, v in pairs[:5])
        print(f"  ℹ️  Unsupported market outcomes skipped: {top}")

    print(
        "  📊 Normalization summary: "
        f"outcomes={normalization_summary['total_outcomes']}, "
        f"normalized={normalization_summary['normalized_rows']}, "
        f"unsupported={normalization_summary['skipped_unsupported_market']}, "
        f"bad_price={normalization_summary['skipped_invalid_price']}, "
        f"missing_required={normalization_summary['skipped_missing_required']}"
    )

    return consolidated


def consolidate_odds(raw_odds: list[dict]) -> list[dict]:
    """Merge over/under lines for the same player + book into single rows."""
    merged = {}
    
    for odds in raw_odds:
        key = (odds["player_name"], odds["sportsbook"], odds["game_date"])
        
        if key not in merged:
            merged[key] = odds.copy()
        else:
            # Merge over/under prices
            if odds["over_price"] is not None:
                merged[key]["over_price"] = odds["over_price"]
                merged[key]["implied_prob_over"] = odds["implied_prob_over"]
            if odds["under_price"] is not None:
                merged[key]["under_price"] = odds["under_price"]
                merged[key]["implied_prob_under"] = odds["implied_prob_under"]
    
    return list(merged.values())


def get_best_odds(player_name: str, game_date: str) -> dict:
    """
    Find the best available HR Yes odds across all books for a player.
    Returns the best line and which book has it.
    """
    results = query("""
        SELECT sportsbook, over_price, implied_prob_over
        FROM mlb_hr_odds
        WHERE player_name = ? AND game_date = ? AND over_price IS NOT NULL
        ORDER BY over_price DESC
        LIMIT 5
    """, (player_name, game_date))
    
    if not results:
        return {"best_odds": None, "book": None, "implied_prob": None}
    
    best = results[0]
    return {
        "best_odds": best["over_price"],
        "book": best["sportsbook"],
        "implied_prob": best["implied_prob_over"],
        "all_books": results,
    }
