import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rescore_on_lineup  # noqa: E402
import score_markets  # noqa: E402


def test_score_markets_main_returns_non_zero_on_failed_market(monkeypatch):
    monkeypatch.setattr(
        score_markets,
        "score_markets",
        lambda **_: [{"market": "HR", "status": "failed", "error": "boom"}],
    )
    monkeypatch.setattr(sys, "argv", ["score_markets.py", "--date", "2026-03-27", "--market", "HR"])

    assert score_markets.main() == 1


def test_rescore_main_returns_non_zero_when_market_errors(monkeypatch):
    monkeypatch.setattr(
        rescore_on_lineup,
        "rescore_on_lineup",
        lambda **_: {
            "game_date": "2026-03-27",
            "market_results": [{"market": "HR", "error": "boom"}],
        },
    )
    monkeypatch.setattr(sys, "argv", ["rescore_on_lineup.py", "--date", "2026-03-27"])

    assert rescore_on_lineup.main() == 1
