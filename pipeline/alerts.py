"""Market-aware alerting (Discord-first, extensible later)."""
from __future__ import annotations

import json
import os
from typing import Any

import requests

from db.database import query

DEFAULT_THRESHOLDS: dict[str, dict[str, Any]] = {
    "*": {"signals": ["BET", "LEAN"], "min_score": 70, "max_rows": 5},
    "HR": {"signals": ["BET", "LEAN"], "min_score": 72, "max_rows": 5},
    "K": {"signals": ["BET", "LEAN"], "min_score": 70, "max_rows": 5},
}


def _load_thresholds() -> dict[str, dict[str, Any]]:
    raw = os.getenv("ALERT_THRESHOLDS_JSON", "").strip()
    if not raw:
        return DEFAULT_THRESHOLDS
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return DEFAULT_THRESHOLDS


def _threshold_for_market(market: str) -> dict[str, Any]:
    thresholds = _load_thresholds()
    return thresholds.get(market, thresholds.get("*", DEFAULT_THRESHOLDS["*"]))


def _top_rows(game_date: str, market: str) -> list[dict[str, Any]]:
    t = _threshold_for_market(market)
    signals = [str(s).upper() for s in t.get("signals", ["BET"])]
    min_score = float(t.get("min_score", 70))
    max_rows = int(t.get("max_rows", 5))
    placeholders = ",".join(["?"] * len(signals))
    params = (game_date, market, *signals, min_score, max_rows)
    return query(
        f"""
        SELECT game_date, market, player_name, team_abbr, side, line, selection_key,
               model_score, edge, signal, confidence_band, sportsbook,
               lineup_confirmed, reasons_json, risk_flags_json
        FROM model_scores
        WHERE game_date = ?
          AND market = ?
          AND COALESCE(is_active, 1) = 1
          AND signal IN ({placeholders})
          AND model_score >= ?
        ORDER BY model_score DESC, edge DESC
        LIMIT ?
        """,
        params,
    )


def _build_payload(game_date: str, market: str, rows: list[dict[str, Any]], dashboard_url: str | None = None) -> dict[str, Any]:
    title = f"MLBPredicts Alerts — {game_date} {market}"
    lines = []
    for row in rows:
        reasons = row.get("reasons_json") or "[]"
        risk = row.get("risk_flags_json") or "[]"
        try:
            reasons_list = json.loads(reasons)[:2]
        except Exception:
            reasons_list = []
        try:
            risk_list = json.loads(risk)[:2]
        except Exception:
            risk_list = []
        lines.append(
            " • "
            + f"{row.get('signal')} {row.get('player_name') or row.get('selection_key') or row.get('team_abbr')} "
            + f"{row.get('side') or ''} {row.get('line') or ''} "
            + f"score={round(float(row.get('model_score') or 0),1)} edge={round(float(row.get('edge') or 0),2)}% "
            + f"lineup={'Y' if row.get('lineup_confirmed') else 'N'} "
            + (f"reasons={'; '.join(reasons_list)} " if reasons_list else "")
            + (f"risk={'; '.join(risk_list)}" if risk_list else "")
        )

    content = "\n".join([title, *lines])
    if dashboard_url:
        content += f"\nDashboard: {dashboard_url}"
    return {"content": content[:1900]}


def send_market_alerts(game_date: str, market: str, dashboard_url: str | None = None) -> dict[str, Any]:
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    rows = _top_rows(game_date, market)
    if not rows:
        return {"sent": False, "reason": "no_rows", "count": 0}
    if not webhook:
        return {"sent": False, "reason": "webhook_not_set", "count": len(rows)}

    payload = _build_payload(game_date, market, rows, dashboard_url=dashboard_url)
    resp = requests.post(webhook, json=payload, timeout=15)
    resp.raise_for_status()
    return {"sent": True, "count": len(rows), "status_code": resp.status_code}
