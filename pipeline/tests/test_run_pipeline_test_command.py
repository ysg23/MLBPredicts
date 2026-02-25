import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_pipeline  # noqa: E402


def _install_schedule_stub(fetch_impl):
    """Install a temporary fetchers.schedule stub module for run_test imports."""
    fetchers_pkg = types.ModuleType("fetchers")
    schedule_mod = types.ModuleType("fetchers.schedule")
    schedule_mod.fetch_todays_games = fetch_impl
    fetchers_pkg.schedule = schedule_mod
    sys.modules["fetchers"] = fetchers_pkg
    sys.modules["fetchers.schedule"] = schedule_mod


@pytest.fixture(autouse=True)
def clear_fetcher_stubs():
    for name in ("fetchers.schedule", "fetchers"):
        sys.modules.pop(name, None)
    yield
    for name in ("fetchers.schedule", "fetchers"):
        sys.modules.pop(name, None)


def test_run_test_returns_true_on_success(capsys):
    _install_schedule_stub(
        lambda: [
            {
                "away_team": "NYY",
                "home_team": "BOS",
                "away_pitcher_name": "A",
                "home_pitcher_name": "B",
            }
        ]
    )

    assert run_pipeline.run_test() is True
    assert "API connection works" in capsys.readouterr().out


def test_run_test_returns_false_on_failure(capsys):
    def _raise_error():
        raise RuntimeError("boom")

    _install_schedule_stub(_raise_error)

    assert run_pipeline.run_test() is False
    assert "Test failed" in capsys.readouterr().out
