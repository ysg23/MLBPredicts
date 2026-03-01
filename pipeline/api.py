"""FastAPI service — health checks, status, and MLB data endpoints."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


# ── Rate limiting ──────────────────────────────────────────────────────────────

def _rate_key(request: Request) -> str:
    """Authenticated requests are keyed by token prefix; others by IP."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:50]  # first 43 chars avoids unbounded key length
    return get_remote_address(request)


limiter = Limiter(key_func=_rate_key)

app = FastAPI(title="MLBPredicts API", version="0.3.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


# ── Auth ───────────────────────────────────────────────────────────────────────

_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")
_JWT_ALGO = "HS256"


def _get_bearer(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authorization header missing or malformed. Expected: Bearer <token>",
        )
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Bearer token is empty.")
    return token


def _require_auth(token: str = Depends(_get_bearer)) -> str:
    """Verify Supabase JWT and return user_id (UUID string)."""
    from jose import JWTError, jwt

    if not _JWT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Auth not configured on this service (SUPABASE_JWT_SECRET missing).",
        )
    try:
        payload = jwt.decode(
            token, _JWT_SECRET, algorithms=[_JWT_ALGO], audience="authenticated"
        )
        return str(payload["sub"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")


# ── Helpers ────────────────────────────────────────────────────────────────────

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
    mapping = {
        "last7": "game_date >= CURRENT_DATE - INTERVAL '7 days'",
        "last30": "game_date >= CURRENT_DATE - INTERVAL '30 days'",
        "last90": "game_date >= CURRENT_DATE - INTERVAL '90 days'",
        "alltime": "",
    }
    return mapping.get(period, mapping["last30"])


# ── Health / Status ────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"ok": True, "status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/status")
def status() -> dict:
    try:
        from db.database import get_status
        return {"status": "ok", "tables": get_status()}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "tables": {}, "error": str(exc)}


# ── GET /api/mlb/games ─────────────────────────────────────────────────────────

@app.get("/api/mlb/games")
@limiter.limit("100/minute")
def get_games(
    request: Request,
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
    return _envelope([dict(r) for r in rows], game_date)


# ── GET /api/mlb/scores ────────────────────────────────────────────────────────

@app.get("/api/mlb/scores")
@limiter.limit("100/minute")
def get_scores(
    request: Request,
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
    return _envelope([dict(r) for r in rows], game_date)


# ── GET /api/mlb/daily-card ────────────────────────────────────────────────────

@app.get("/api/mlb/daily-card")
@limiter.limit("100/minute")
def get_daily_card(
    request: Request,
    date: str = Query(default=None, description="Card date YYYY-MM-DD (defaults to today)"),
) -> dict[str, Any]:
    from db.database import query as db_query

    game_date = date or _today()

    rows = db_query(
        "SELECT * FROM mlb_daily_cards WHERE card_date = ? LIMIT 1",
        (game_date,),
    )
    if rows:
        return _envelope([dict(rows[0])], game_date)

    # Fallback: top 10 BET/LEAN scores for the date
    fallback = db_query(
        """
        SELECT
            player_name, team_abbr, opponent_team_abbr,
            market, bet_type, line, side,
            ROUND(model_score::numeric, 2) AS model_score,
            edge, signal, confidence_band, visibility_tier, result
        FROM mlb_model_scores
        WHERE game_date = ?
          AND signal IN ('BET', 'LEAN')
          AND COALESCE(is_active, 1) = 1
        ORDER BY model_score DESC
        LIMIT 10
        """,
        (game_date,),
    )
    return _envelope([dict(r) for r in fallback], game_date)


# ── GET /api/mlb/performance/summary ──────────────────────────────────────────

@app.get("/api/mlb/performance/summary")
@limiter.limit("100/minute")
def get_performance_summary(
    request: Request,
    period: str = Query(default="last30", description="last7 | last30 | last90 | alltime"),
    market: str = Query(default=None, description="Optional market filter e.g. HR"),
) -> dict[str, Any]:
    from db.database import query as db_query

    valid_periods = {"last7", "last30", "last90", "alltime"}
    if period not in valid_periods:
        raise HTTPException(
            status_code=400,
            detail=f"period must be one of: {', '.join(sorted(valid_periods))}",
        )

    where_parts = ["result IS NOT NULL", "result != 'pending'", "result != 'void'"]
    params: list[Any] = []

    if market:
        where_parts.append("market = ?")
        params.append(market.upper())

    period_clause = _period_clause(period)
    if period_clause:
        where_parts.append(period_clause)

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
        WHERE {' AND '.join(where_parts)}
        GROUP BY band
        ORDER BY band DESC
        """,
        tuple(params),
    )

    data = [dict(r) for r in rows]
    for row in data:
        total = row.get("total") or 0
        wins = row.get("wins") or 0
        row["win_rate"] = round(wins / total, 4) if total > 0 else None

    return _envelope(data, _today())


# ── GET /api/mlb/players/{player_id} ──────────────────────────────────────────

@app.get("/api/mlb/players/{player_id}")
@limiter.limit("100/minute")
def get_player(request: Request, player_id: int) -> dict[str, Any]:
    from db.database import query as db_query

    rows = db_query(
        "SELECT * FROM mlb_players WHERE player_id = ? LIMIT 1",
        (player_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Player {player_id} not found")
    return _envelope([dict(rows[0])], _today())


# ── Picks ──────────────────────────────────────────────────────────────────────

class SavePickBody(BaseModel):
    model_score_id: int


@app.post("/api/mlb/picks/save", status_code=201)
@limiter.limit("500/minute")
def save_pick(
    request: Request,
    body: SavePickBody,
    user_id: str = Depends(_require_auth),
) -> dict[str, Any]:
    from db.database import get_connection, query as db_query

    if body.model_score_id <= 0:
        raise HTTPException(status_code=400, detail="model_score_id must be a positive integer.")

    # Verify the model score exists
    rows = db_query(
        "SELECT id FROM mlb_model_scores WHERE id = ? LIMIT 1",
        (body.model_score_id,),
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"model_score {body.model_score_id} not found.",
        )

    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO user_saved_picks (user_id, sport, prediction_id)
            VALUES (?, 'MLB', ?)
            RETURNING id, user_id, sport, prediction_id, saved_at
            """,
            (user_id, body.model_score_id),
        )
        row = cursor.fetchone()
        conn.commit()
    except Exception as exc:
        conn.rollback()
        if "23505" in str(exc):
            raise HTTPException(
                status_code=409,
                detail=f"model_score {body.model_score_id} is already in your picks.",
            )
        raise HTTPException(status_code=500, detail="Failed to save pick.")
    finally:
        conn.close()

    saved = dict(row) if row else {"model_score_id": body.model_score_id}
    return _envelope([saved], _today())


@app.get("/api/mlb/picks/my")
@limiter.limit("500/minute")
def get_my_picks(
    request: Request,
    user_id: str = Depends(_require_auth),
) -> dict[str, Any]:
    from db.database import query as db_query

    rows = db_query(
        """
        SELECT
            p.id,
            p.saved_at,
            p.prediction_id AS model_score_id,
            ms.game_date,
            ms.market,
            ms.player_name,
            ms.team_abbr,
            ms.opponent_team_abbr,
            ms.bet_type,
            ms.line,
            ms.side,
            ROUND(ms.model_score::numeric, 2) AS model_score,
            ms.edge,
            ms.signal,
            ms.visibility_tier,
            ms.result
        FROM user_saved_picks p
        LEFT JOIN mlb_model_scores ms ON ms.id = p.prediction_id
        WHERE p.user_id = ?
          AND p.sport = 'MLB'
        ORDER BY p.saved_at DESC
        """,
        (user_id,),
    )
    return _envelope([dict(r) for r in rows], _today())
