"""Minimal API service for Railway health checks and lightweight status endpoints."""
from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI

from db.database import get_status

app = FastAPI(title="MLBPredicts API", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/status")
def status() -> dict:
    return {"status": "ok", "tables": get_status()}
