import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import alerts  # noqa: E402
from scoring.base_engine import determine_visibility_tier  # noqa: E402


def test_determine_visibility_tier_default_rules():
    assert determine_visibility_tier("BET", "HIGH") == "FREE"
    assert determine_visibility_tier("LEAN", "MEDIUM") == "PRO"


def test_send_market_alerts_skips_when_no_rows(monkeypatch):
    monkeypatch.setattr(alerts, "query", lambda *_args, **_kwargs: [])
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

    res = alerts.send_market_alerts(game_date="2026-03-27", market="HR")
    assert res["sent"] is False
    assert res["reason"] == "no_rows"


def test_send_market_alerts_skips_when_webhook_missing(monkeypatch):
    rows = [{"signal": "BET", "player_name": "A", "line": 0.5, "model_score": 80, "edge": 6, "lineup_confirmed": 1, "reasons_json": "[]", "risk_flags_json": "[]"}]
    monkeypatch.setattr(alerts, "query", lambda *_args, **_kwargs: rows)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

    res = alerts.send_market_alerts(game_date="2026-03-27", market="HR")
    assert res["sent"] is False
    assert res["reason"] == "webhook_not_set"
    assert res["count"] == 1
