"""
Grade outcomes and settle bets for a date.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any

from clv import capture_closing_lines_for_date, update_bet_clv_for_date
from db.database import get_connection, query, upsert_many
from grading.base_grader import (
    SUPPORTED_MARKETS,
    payout_for_settlement,
    settle_selection,
)
from grading.game_markets import grade_game_market_outcomes
from grading.player_props import grade_player_prop_outcomes


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _selection_candidates(game_date: str) -> list[dict[str, Any]]:
    model_rows = query(
        """
        SELECT
            game_date, market, game_id, event_id, entity_type, player_id, team_id,
            opponent_team_id, team_abbr, opponent_team_abbr, selection_key, side, bet_type, line
        FROM mlb_model_scores
        WHERE game_date = ? AND COALESCE(is_active, 1) = 1
        """,
        (game_date,),
    )
    bet_rows = query(
        """
        SELECT
            game_date, market, game_id, NULL AS event_id, NULL AS entity_type, player_id, team_id,
            opponent_team_id, team_id AS team_abbr, opponent_team_id AS opponent_team_abbr,
            selection_key, side, bet_type, line
        FROM mlb_bets
        WHERE game_date = ?
          AND (result IS NULL OR result = 'pending')
        """,
        (game_date,),
    )

    merged = model_rows + bet_rows
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in merged:
        market = str(row.get("market") or "").upper()
        if market not in SUPPORTED_MARKETS:
            continue
        key = (
            market,
            row.get("game_id"),
            row.get("player_id"),
            row.get("team_id"),
            row.get("selection_key"),
            row.get("side"),
            row.get("bet_type"),
            row.get("line"),
        )
        if key in seen:
            continue
        seen.add(key)
        row["market"] = market
        deduped.append(row)
    return deduped


def _upsert_outcomes(outcomes: list[dict[str, Any]]) -> int:
    if not outcomes:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    normalized: list[dict[str, Any]] = []
    for row in outcomes:
        item = dict(row)
        item["settled_at"] = now
        normalized.append(item)
    return int(
        upsert_many(
            "mlb_market_outcomes",
            normalized,
            conflict_cols=["market", "game_id", "player_id", "team_abbr", "bet_type", "line", "selection_key"],
        )
    )


def _outcome_index(outcomes: list[dict[str, Any]]) -> tuple[dict[tuple[Any, ...], dict[str, Any]], dict[tuple[Any, ...], dict[str, Any]]]:
    by_selection: dict[tuple[Any, ...], dict[str, Any]] = {}
    by_shape: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in outcomes:
        market = row.get("market")
        game_id = row.get("game_id")
        selection_key = row.get("selection_key")
        if selection_key:
            by_selection[(market, game_id, selection_key)] = row
        by_shape[
            (
                market,
                game_id,
                row.get("player_id"),
                row.get("team_id"),
                row.get("bet_type"),
                row.get("line"),
                row.get("side"),
            )
        ] = row
    return by_selection, by_shape


_SETTLEMENT_TO_RESULT: dict[str, str] = {
    "win": "win",
    "loss": "loss",
    "push": "push",
    "void": "void",
    "no_action": "void",
}


def _normalize_result(settlement: str) -> str:
    """Map a raw settlement string to the canonical result column value."""
    return _SETTLEMENT_TO_RESULT.get(settlement.lower(), "pending")


def _update_model_score_results(
    game_date: str,
    selections: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
) -> int:
    """
    After outcomes are graded, back-fill result / actual_value / graded_at on
    every matching mlb_model_scores row.

    Returns the number of rows updated.
    """
    # Only process rows that originally came from mlb_model_scores (they have
    # game_date set and are fully identified by the WHERE clause below).
    model_selections = [
        s for s in selections
        if str(s.get("market") or "").upper() in SUPPORTED_MARKETS
    ]
    if not model_selections or not outcomes:
        return 0

    by_selection, by_shape = _outcome_index(outcomes)

    conn = get_connection()
    updated = 0
    try:
        for sel in model_selections:
            market = str(sel.get("market") or "").upper()
            game_id = sel.get("game_id")

            # Match outcome using selection_key first, then shape fallback.
            matched: dict[str, Any] | None = None
            selection_key = sel.get("selection_key")
            if selection_key:
                matched = by_selection.get((market, game_id, selection_key))
            if matched is None:
                shape_key = (
                    market,
                    game_id,
                    sel.get("player_id"),
                    sel.get("team_id"),
                    sel.get("bet_type"),
                    sel.get("line"),
                    sel.get("side"),
                )
                matched = by_shape.get(shape_key)

            if matched is None:
                continue

            settlement = settle_selection(
                market=market,
                side=sel.get("side"),
                line=sel.get("line"),
                outcome_value=matched.get("outcome_value"),
                bet_type=sel.get("bet_type"),
            )
            result_val = _normalize_result(settlement)
            actual_value = matched.get("outcome_value")

            conn.execute(
                """
                UPDATE mlb_model_scores
                SET result = ?,
                    actual_value = ?,
                    graded_at = CURRENT_TIMESTAMP
                WHERE game_date = ?
                  AND market = ?
                  AND game_id = ?
                  AND player_id IS NOT DISTINCT FROM ?
                  AND team_abbr IS NOT DISTINCT FROM ?
                  AND bet_type = ?
                  AND line IS NOT DISTINCT FROM ?
                  AND COALESCE(is_active, 1) = 1
                """,
                (
                    result_val,
                    actual_value,
                    game_date,
                    market,
                    game_id,
                    sel.get("player_id"),
                    sel.get("team_abbr"),
                    sel.get("bet_type"),
                    sel.get("line"),
                ),
            )
            if isinstance(conn.raw.rowcount if hasattr(conn.raw, "rowcount") else None, int):
                updated += max(0, conn.raw.rowcount)
            else:
                updated += 1

        conn.commit()
    finally:
        conn.close()

    return updated


def _settle_bets(game_date: str, outcomes: list[dict[str, Any]]) -> dict[str, int]:
    pending_bets = query(
        """
        SELECT *
        FROM mlb_bets
        WHERE game_date = ?
          AND (result IS NULL OR result = 'pending')
        """,
        (game_date,),
    )
    if not pending_bets:
        return {"pending_bets": 0, "settled": 0, "still_pending": 0}

    by_selection, by_shape = _outcome_index(outcomes)
    settled = 0
    still_pending = 0
    conn = get_connection()
    try:
        for bet in pending_bets:
            market = str(bet.get("market") or "").upper()
            game_id = bet.get("game_id")
            matched = None
            selection_key = bet.get("selection_key")
            if selection_key:
                matched = by_selection.get((market, game_id, selection_key))
            if matched is None:
                shape_key = (
                    market,
                    game_id,
                    bet.get("player_id"),
                    bet.get("team_id"),
                    bet.get("bet_type"),
                    bet.get("line"),
                    bet.get("side"),
                )
                matched = by_shape.get(shape_key)

            if matched is None:
                still_pending += 1
                continue

            settlement = settle_selection(
                market=market,
                side=bet.get("side"),
                line=bet.get("line"),
                outcome_value=matched.get("outcome_value"),
                bet_type=bet.get("bet_type"),
            )
            if settlement == "pending":
                still_pending += 1
                continue

            payout, profit = payout_for_settlement(
                stake=bet.get("stake"),
                american_odds=bet.get("odds"),
                settlement=settlement,
            )
            conn.execute(
                """
                UPDATE mlb_bets
                SET result = ?,
                    payout = ?,
                    profit = ?,
                    settled_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (settlement, payout, profit, bet["id"]),
            )
            settled += 1
        conn.commit()
    finally:
        conn.close()
    return {"pending_bets": len(pending_bets), "settled": settled, "still_pending": still_pending}


def grade_results_for_date(game_date: str) -> dict[str, Any]:
    selections = _selection_candidates(game_date)
    player_outcomes = grade_player_prop_outcomes(selections)
    game_outcomes = grade_game_market_outcomes(selections)
    all_outcomes = player_outcomes + game_outcomes
    upserted = _upsert_outcomes(all_outcomes)
    model_scores_updated = _update_model_score_results(game_date, selections, all_outcomes)
    closing_capture = capture_closing_lines_for_date(game_date)
    clv_update = update_bet_clv_for_date(game_date)
    settle_summary = _settle_bets(game_date, all_outcomes)
    return {
        "game_date": game_date,
        "selections_considered": len(selections),
        "player_outcomes": len(player_outcomes),
        "game_outcomes": len(game_outcomes),
        "outcomes_upserted": upserted,
        "model_scores_updated": model_scores_updated,
        "closing_groups": closing_capture.get("groups", 0),
        "closing_upserted": closing_capture.get("upserted", 0),
        "bets_clv_updated": clv_update.get("updated", 0),
        **settle_summary,
    }


# Alias used by main_ingester.py and main_scoring.py
run_grading = grade_results_for_date


def main() -> int:
    parser = argparse.ArgumentParser(description="Grade market outcomes and settle bets")
    parser.add_argument("--date", type=str, help="Target date YYYY-MM-DD (defaults to today)")
    args = parser.parse_args()
    game_date = args.date or _today_str()
    summary = grade_results_for_date(game_date)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
