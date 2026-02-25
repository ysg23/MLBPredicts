import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fetchers import pitchers


def test_fetch_pitcher_window_uses_player_id_when_supported(monkeypatch):
    captured = {}

    def fake_statcast_pitcher(start_dt, end_dt, player_id=None):
        captured["args"] = (start_dt, end_dt, player_id)
        return pd.DataFrame()

    monkeypatch.setattr(pitchers, "statcast_pitcher", fake_statcast_pitcher)

    pitchers._fetch_pitcher_window("2023-03-01", "2023-03-31", 123)

    assert captured["args"] == ("2023-03-01", "2023-03-31", 123)


def test_fetch_daily_pitcher_stats_writes_rows_with_player_id_signature(monkeypatch):
    rows = []

    def fake_statcast_pitcher(start_dt, end_dt, player_id=None):
        return pd.DataFrame(
            [
                {
                    "pitcher": player_id,
                    "player_name": "Pitcher One",
                    "events": "strikeout",
                    "outs_on_play": 1,
                    "launch_speed": 95.0,
                    "launch_angle": 30.0,
                    "description": "swinging_strike",
                    "pitch_type": "FF",
                    "release_speed": 96.0,
                    "zone": 5,
                    "game_pk": 1,
                    "at_bat_number": 1,
                    "p_throws": "R",
                    "game_date": "2023-03-31",
                }
            ]
        )

    def fake_upsert_many(_table, payload, conflict_cols=None):
        rows.extend(payload)
        return len(payload)

    def fake_query(_sql, _params=None):
        return [{"pid": 555, "team": "NYY"}]

    monkeypatch.setattr(pitchers, "statcast_pitcher", fake_statcast_pitcher)
    monkeypatch.setattr(pitchers, "upsert_many", fake_upsert_many)
    monkeypatch.setattr(pitchers, "query", fake_query)

    saved = pitchers.fetch_daily_pitcher_stats([555], as_of_date="2023-03-31")

    assert saved == 2
    assert len(rows) == 2
    assert all(r["player_id"] == 555 for r in rows)
    assert all(r["team"] == "NYY" for r in rows)
    assert sorted(r["window_days"] for r in rows) == [14, 30]
