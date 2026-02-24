import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fetchers.statcast import compute_batter_hr_stats


def test_compute_batter_hr_stats_handles_nullable_na_and_zero_values():
    rows = []
    for i in range(10):
        rows.append(
            {
                "launch_speed": 96.0,
                "events": "single" if i < 9 else "home_run",
                "batter": 123,
                "player_name": "Test Batter",
                "stand": "R",
                "launch_speed_angle": 0,
                "launch_angle": 30.0,
                "hc_x": 100.0,
                "p_throws": "R",
                "estimated_woba_using_speedangle": pd.NA,
                "home_team": "NYY",
            }
        )
    df = pd.DataFrame(rows)
    df["estimated_woba_using_speedangle"] = df["estimated_woba_using_speedangle"].astype("Float64")

    stats = compute_batter_hr_stats(df, window_days=7, stat_date="2023-03-31")

    assert len(stats) == 1
    row = stats[0]
    assert row["xwoba"] is None
    assert row["barrel_pct"] == 0.0
    assert row["pull_pct"] == 100.0
