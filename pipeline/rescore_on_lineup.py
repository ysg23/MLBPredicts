"""
Lineup-triggered rescore job.

Goal:
- Detect changed/confirmed lineups
- Re-score affected markets for impacted games
- Track each market pass in score_runs (run_type='lineup_rescore')
"""
from __future__ import annotations

import argparse
import importlib
from collections import defaultdict
from datetime import datetime
from typing import Any

from db.database import (
    complete_score_run,
    create_score_run,
    fail_score_run,
    get_connection,
    insert_many,
    query,
)
from scoring.base_engine import get_park_factor, get_weather, load_today_games


BATTER_MARKETS = ["HR", "HITS_1P", "HITS_LINE", "TB_LINE", "RBI", "RUNS"]
GAME_MARKETS_OPTIONAL = ["TOTAL", "TEAM_TOTAL", "F5_TOTAL", "ML", "F5_ML"]

MARKET_MODULES = {
    "HR": "scoring.hr_model",
    "K": "scoring.k_model",
    "ML": "scoring.ml_model",
    "TOTAL": "scoring.totals_model",
    "F5_ML": "scoring.f5_ml_model",
    "F5_TOTAL": "scoring.f5_total_model",
}


def _today_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _lineup_signature(rows: list[dict[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    sig = [
        (
            int(r["player_id"]),
            int(r["batting_order"]) if r.get("batting_order") is not None else None,
            r.get("position"),
            int(r.get("is_starter", 0)),
            int(r.get("confirmed", 0)),
        )
        for r in rows
    ]
    sig.sort()
    return tuple(sig)


def _detect_changed_lineup_pairs(
    game_date: str,
    game_id: int | None = None,
    team_id: str | None = None,
) -> list[dict[str, Any]]:
    filters = ["game_date = ?"]
    params: list[Any] = [game_date]
    if game_id is not None:
        filters.append("game_id = ?")
        params.append(game_id)
    if team_id is not None:
        filters.append("team_id = ?")
        params.append(team_id)

    where_sql = " AND ".join(filters)
    rows = query(
        f"""
        SELECT game_id, team_id, fetched_at, confirmed, player_id, batting_order, position, is_starter
        FROM lineups
        WHERE {where_sql}
        ORDER BY game_id, team_id, fetched_at DESC, batting_order, player_id
        """,
        tuple(params),
    )
    if not rows:
        return []

    grouped: dict[tuple[int, str], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        key = (int(row["game_id"]), str(row["team_id"]))
        grouped[key][str(row["fetched_at"])].append(row)

    changed_pairs: list[dict[str, Any]] = []
    for (gid, tid), snapshots in grouped.items():
        snapshot_times = sorted(snapshots.keys(), reverse=True)
        latest_rows = snapshots[snapshot_times[0]]
        prev_rows = snapshots[snapshot_times[1]] if len(snapshot_times) > 1 else []

        latest_sig = _lineup_signature(latest_rows)
        prev_sig = _lineup_signature(prev_rows) if prev_rows else tuple()
        latest_confirmed = any(int(r.get("confirmed", 0)) == 1 for r in latest_rows)
        prev_confirmed = any(int(r.get("confirmed", 0)) == 1 for r in prev_rows)

        lineup_changed = bool(prev_rows) and latest_sig != prev_sig
        became_confirmed = latest_confirmed and (not prev_confirmed)

        # If caller targets a specific game/team, allow manual rescore with latest snapshot.
        manual_targeted = game_id is not None or team_id is not None
        if lineup_changed or became_confirmed or (manual_targeted and latest_rows):
            changed_pairs.append(
                {
                    "game_id": gid,
                    "team_id": tid,
                    "lineup_changed": lineup_changed,
                    "became_confirmed": became_confirmed,
                    "latest_fetched_at": snapshot_times[0],
                }
            )

    return changed_pairs


def _load_market_module(market: str):
    module_path = MARKET_MODULES.get(market)
    if not module_path:
        return None
    try:
        return importlib.import_module(module_path)
    except Exception:
        return None


def _deactivate_market_rows(game_date: str, game_id: int, market: str) -> int:
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE model_scores
            SET is_active = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE game_date = ?
              AND game_id = ?
              AND market = ?
              AND COALESCE(is_active, 1) = 1
            """,
            (game_date, game_id, market),
        )
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


def _score_one_market_for_games(
    game_date: str,
    market: str,
    game_ids: list[int],
    score_run_id: int,
) -> dict[str, Any]:
    mod = _load_market_module(market)
    if mod is None:
        return {"market": market, "supported": False, "rows_written": 0, "games_scored": 0}

    season = int(game_date[:4])
    game_ctx = {g.game_id: g for g in load_today_games(game_date)}

    rows_written = 0
    games_scored = 0
    superseded_rows = 0
    for gid in sorted(set(game_ids)):
        ctx = game_ctx.get(gid)
        if ctx is None:
            continue

        weather = get_weather(gid)
        park_factor = get_park_factor(ctx.stadium_id, season)
        rows = mod.score_game(ctx, weather=weather, park_factor=park_factor, season=season) or []
        if not rows:
            continue

        superseded_rows += _deactivate_market_rows(game_date, gid, market)
        for row in rows:
            row["score_run_id"] = score_run_id
            row["is_active"] = 1
        rows_written += int(insert_many("model_scores", rows))
        games_scored += 1

    return {
        "market": market,
        "supported": True,
        "rows_written": rows_written,
        "games_scored": games_scored,
        "superseded_rows": superseded_rows,
    }


def rescore_on_lineup(
    game_date: str | None = None,
    game_id: int | None = None,
    team_id: str | None = None,
) -> dict[str, Any]:
    game_date = game_date or _today_str()
    changed_pairs = _detect_changed_lineup_pairs(game_date=game_date, game_id=game_id, team_id=team_id)
    if not changed_pairs:
        print("ℹ️ No lineup changes/confirmations detected. Nothing to rescore.")
        return {
            "game_date": game_date,
            "changed_pairs": 0,
            "rows_written_total": 0,
            "market_results": [],
        }

    affected_game_ids = sorted({int(item["game_id"]) for item in changed_pairs})
    markets = BATTER_MARKETS + GAME_MARKETS_OPTIONAL

    print(
        f"\n🔁 Lineup rescore start: date={game_date} "
        f"changed_pairs={len(changed_pairs)} affected_games={len(affected_game_ids)}"
    )

    market_results: list[dict[str, Any]] = []
    rows_written_total = 0
    for market in markets:
        score_run_id = create_score_run(
            run_type="lineup_rescore",
            game_date=game_date,
            market=market,
            triggered_by="lineup_rescore",
            metadata={"affected_games": affected_game_ids, "team_id": team_id},
        )
        try:
            result = _score_one_market_for_games(
                game_date=game_date,
                market=market,
                game_ids=affected_game_ids,
                score_run_id=score_run_id,
            )
            rows_written = int(result.get("rows_written", 0))
            rows_written_total += rows_written
            complete_score_run(
                score_run_id=score_run_id,
                status="completed",
                rows_scored=rows_written,
                metadata=result,
            )
            market_results.append(result)
        except Exception as exc:
            fail_score_run(
                score_run_id=score_run_id,
                error_message=str(exc),
                metadata={"market": market, "affected_games": affected_game_ids},
            )
            market_results.append(
                {
                    "market": market,
                    "supported": False,
                    "rows_written": 0,
                    "error": str(exc),
                }
            )

    print(f"✅ Lineup rescore complete: changed_rows={rows_written_total}")
    return {
        "game_date": game_date,
        "changed_pairs": len(changed_pairs),
        "affected_games": affected_game_ids,
        "rows_written_total": rows_written_total,
        "market_results": market_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rescore impacted markets on lineup changes")
    parser.add_argument("--date", type=str, help="Target game date (YYYY-MM-DD), defaults to today")
    parser.add_argument("--game-id", type=int, help="Optional game_id scope")
    parser.add_argument("--team-id", type=str, help="Optional team_id scope")
    args = parser.parse_args()

    result = rescore_on_lineup(game_date=args.date, game_id=args.game_id, team_id=args.team_id)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
