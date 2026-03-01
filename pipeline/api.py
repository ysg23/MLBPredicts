"""FastAPI service — health checks, status, and MLB data endpoints."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query

app = FastAPI(title="MLBPredicts API", version="0.2.0")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _meta(date_str: str, count: int) -> dict[str, Any]:
    return {
        "sport": "MLB",
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": count,
    }


def _envelope(data: list[Any], date_str: str) -> dict[str, Any]:
    return {"data": data, "meta": _meta(date_str, len(data))}


def _period_clause(period: str) -> str:
    """Return a SQL fragment for the period filter (no trailing AND)."""
    mapping = {
        "last7": "game_date >= CURRENT_DATE - INTERVAL '7 days'",
        "last30": "game_date >= CURRENT_DATE - INTERVAL '30 days'",
        "last90": "game_date >= CURRENT_DATE - INTERVAL '90 days'",
        "alltime": "",
    }
    return mapping.get(period, mapping["last30"])


# ---------------------------------------------------------------------------
# Existing endpoints — kept intact
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    # Keep this endpoint dependency-free so Railway healthchecks stay green
    # even when optional integrations/config are unavailable.
    return {"ok": True, "status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/status")
def status() -> dict:
    # Lazy import protects API startup from nonessential DB/import issues.
    try:
        from db.database import get_status

        return {"status": "ok", "tables": get_status()}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "tables": {}, "error": str(exc)}


# ---------------------------------------------------------------------------
# GET /api/mlb/games
# ---------------------------------------------------------------------------

@app.get("/api/mlb/games")
def get_games(
    date: str = Query(default=None, description="Game date YYYY-MM-DD (defaults to today)"),
) -> dict[str, Any]:
    from db.database import query as db_query

    game_date = date or _today()
    rows = db_query(
        """
        SELECT
            game_id,
            game_date,
            home_team,
            away_team,
            home_pitcher_name,
            away_pitcher_name,
            status,
            home_score,
            away_score
        FROM mlb_games
        WHERE game_date = ?
        ORDER BY game_id
        """,
        (game_date,),
    )
    data = [dict(r) for r in rows]
    return _envelope(data, game_date)


# ---------------------------------------------------------------------------
# GET /api/mlb/scores
# ---------------------------------------------------------------------------

@app.get("/api/mlb/scores")
def get_scores(
    date: str = Query(default=None, description="Game date YYYY-MM-DD (defaults to today)"),
    market: str = Query(default=None, description="Market filter e.g. HR, K, HITS_LINE"),
    signal: str = Query(default=None, description="Signal filter e.g. BET, LEAN"),
    min_score: float = Query(default=0.0, description="Minimum model_score (0-100)"),
    limit: int = Query(default=100, ge=1, le=1000, description="Max rows to return"),
) -> dict[str, Any]:
    from db.database import query as db_query

    game_date = date or _today()

    where_parts = ["game_date = ?", "COALESCE(is_active, 1) = 1"]
    params: list[Any] = [game_date]

    if market:
        where_parts.append("market = ?")
        params.append(market.upper())
    if signal:
        where_parts.append("signal = ?")
        params.append(signal.upper())
    if min_score > 0:
        where_parts.append("model_score >= ?")
        params.append(min_score)

    where_sql = " AND ".join(where_parts)
    params.append(limit)

    rows = db_query(
        f"""
        SELECT
            player_name,
            team_abbr,
            opponent_team_abbr,
            market,
            bet_type,
            line,
            side,
            ROUND(model_score::numeric, 2) AS model_score,
            edge,
            signal,
            confidence_band,
            visibility_tier,
            result
        FROM mlb_model_scores
        WHERE {where_sql}
        ORDER BY model_score DESC
        LIMIT ?
        """,
        tuple(params),
    )
    data = [dict(r) for r in rows]
    return _envelope(data, game_date)


# ---------------------------------------------------------------------------
# GET /api/mlb/daily-card
# ---------------------------------------------------------------------------

@app.get("/api/mlb/daily-card")
def get_daily_card(
    date: str = Query(default=None, description="Card date YYYY-MM-DD (defaults to today)"),
) -> dict[str, Any]:
    from db.database import query as db_query

    game_date = date or _today()

    rows = db_query(
        """
        SELECT *
        FROM mlb_daily_cards
        WHERE card_date = ?
        LIMIT 1
        """,
        (game_date,),
    )

    if rows:
        data = [dict(rows[0])]
        return _envelope(data, game_date)

    # Fallback: top 10 model scores for the date
    fallback_rows = db_query(
        """
        SELECT
            player_name,
            team_abbr,
            opponent_team_abbr,
            market,
            bet_type,
            line,
            side,
            ROUND(model_score::numeric, 2) AS model_score,
            edge,
            signal,
            confidence_band,
            visibility_tier,
            result
        FROM mlb_model_scores
        WHERE game_date = ?
          AND signal IN ('BET', 'LEAN')
          AND COALESCE(is_active, 1) = 1
        ORDER BY model_score DESC
        LIMIT 10
        """,
        (game_date,),
    )
    data = [dict(r) for r in fallback_rows]
    return _envelope(data, game_date)


# ---------------------------------------------------------------------------
# GET /api/mlb/performance/summary
# ---------------------------------------------------------------------------

@app.get("/api/mlb/performance/summary")
def get_performance_summary(
    period: str = Query(default="last30", description="last7 | last30 | last90 | alltime"),
    market: str = Query(default=None, description="Optional market filter e.g. HR"),
) -> dict[str, Any]:
    from db.database import query as db_query

    valid_periods = {"last7", "last30", "last90", "alltime"}
    if period not in valid_periods:
        raise HTTPException(status_code=400, detail=f"period must be one of: {', '.join(sorted(valid_periods))}")

    where_parts = [
        "result IS NOT NULL",
        "result != 'pending'",
        "result != 'void'",
    ]
    params: list[Any] = []

    if market:
        where_parts.append("market = ?")
        params.append(market.upper())

    period_clause = _period_clause(period)
    if period_clause:
        where_parts.append(period_clause)

    where_sql = " AND ".join(where_parts)

    rows = db_query(
        f"""
        SELECT
            CASE
                WHEN model_score >= 80 THEN '80+'
                WHEN model_score >= 70 THEN '70-79'
                WHEN model_score >= 60 THEN '60-69'
                ELSE '<60'
            END AS band,
            COUNT(*) AS total,
            SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) AS wins,
            ROUND(AVG(model_score)::numeric, 1) AS avg_score,
            ROUND(AVG(edge)::numeric, 3) AS avg_edge
        FROM mlb_model_scores
        WHERE {where_sql}
        GROUP BY band
        ORDER BY band DESC
        """,
        tuple(params),
    )

    data = [dict(r) for r in rows]
    # Annotate win_rate on each band for convenience
    for row in data:
        total = row.get("total") or 0
        wins = row.get("wins") or 0
        row["win_rate"] = round(wins / total, 4) if total > 0 else None

    today = _today()
    return _envelope(data, today)


# ---------------------------------------------------------------------------
# GET /api/mlb/players/{player_id}
# ---------------------------------------------------------------------------

@app.get("/api/mlb/players/{player_id}")
def get_player(player_id: int) -> dict[str, Any]:
    from db.database import query as db_query

    rows = db_query(
        """
        SELECT *
        FROM mlb_players
        WHERE player_id = ?
        LIMIT 1
        """,
        (player_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Player {player_id} not found")

    data = [dict(rows[0])]
    today = _today()
    return _envelope(data, today)
