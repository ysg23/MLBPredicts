"""Minimal API service for Railway health checks and lightweight status endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI

app = FastAPI(title="MLBPredicts API", version="0.1.0")


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
