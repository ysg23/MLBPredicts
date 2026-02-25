
"""
Daily pitcher stats fetcher.

Uses pybaseball.statcast_pitcher() for each starter for the last 30 days, then slices locally for 14 days.

Stores rows in pitcher_stats with window_days in {14, 30}.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import inspect
from typing import Optional

import pandas as pd
from pybaseball import statcast_pitcher
from pybaseball import cache as pb_cache

from config import PITCHER_WINDOWS
from db.database import query, upsert_many


pb_cache.enable()


def _date_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _safe_pct(num: float, den: float) -> Optional[float]:
    if den is None or den == 0:
        return None
    return float(num) / float(den)


def _compute_pitcher_metrics(df: pd.DataFrame) -> dict:
    """
    Compute pitcher metrics from Statcast pitch-level data.
    """
    if df is None or df.empty:
        return {}

    # Basic identifiers (pybaseball often includes these)
    player_id = int(df["pitcher"].iloc[0]) if "pitcher" in df.columns else None
    player_name = None
    if "player_name" in df.columns:
        player_name = str(df["player_name"].iloc[0])
    elif "pitcher_name" in df.columns:
        player_name = str(df["pitcher_name"].iloc[0])

    # Batters faced: unique plate appearances
    # Prefer "at_bat_number" + "game_pk" combo
    if {"game_pk", "at_bat_number"}.issubset(df.columns):
        batters_faced = int(df[["game_pk", "at_bat_number"]].drop_duplicates().shape[0])
    else:
        batters_faced = None

    # Strikeouts: events == 'strikeout' or 'strikeout_double_play'
    k_events = {"strikeout", "strikeout_double_play", "strikeout_other"}
    strikeouts = int(df["events"].isin(k_events).sum()) if "events" in df.columns else 0

    # Innings pitched approximation: outs_on_play plus strikeout outs, etc.
    outs = None
    if "outs_on_play" in df.columns:
        outs = int(df["outs_on_play"].fillna(0).sum())
    innings = (outs / 3.0) if outs is not None else None

    k_pct = _safe_pct(strikeouts, batters_faced) if batters_faced else None
    so_per_9 = (strikeouts / innings * 9.0) if innings and innings > 0 else None

    # HR allowed
    hr_allowed = int((df["events"] == "home_run").sum()) if "events" in df.columns else 0
    hr_per_9 = (hr_allowed / innings * 9.0) if innings and innings > 0 else None

    # Batted ball metrics
    bbe = df[df.get("launch_speed").notna()] if "launch_speed" in df.columns else pd.DataFrame()
    avg_ev = float(bbe["launch_speed"].mean()) if not bbe.empty else None
    hard_hit_pct = float((bbe["launch_speed"] >= 95).mean()) if not bbe.empty else None

    # Barrel approximation: Statcast barrel flag exists in some pulls as 'barrel'
    if "barrel" in bbe.columns:
        barrel_pct = float(bbe["barrel"].fillna(0).astype(int).mean())
    else:
        barrel_pct = None

    # Fly ball % approximation from launch_angle (>= 25 degrees)
    fly_ball_pct = float((bbe["launch_angle"] >= 25).mean()) if (not bbe.empty and "launch_angle" in bbe.columns) else None

    # HR/FB approximation
    hr_per_fb = (hr_allowed / (fly_ball_pct * len(bbe))) if (fly_ball_pct is not None and not bbe.empty and fly_ball_pct > 0) else None

    # Pitch quality: avg fastball velocity (4-seam 'FF')
    if {"pitch_type", "release_speed"}.issubset(df.columns):
        ff = df[df["pitch_type"] == "FF"]
        avg_fastball_velo = float(ff["release_speed"].mean()) if not ff.empty else None
    else:
        avg_fastball_velo = None

    # Whiff%: swinging strikes / swings
    if {"description"}.issubset(df.columns):
        swinging = df["description"].isin(["swinging_strike", "swinging_strike_blocked"])
        swings = df["description"].isin(
            ["swinging_strike", "swinging_strike_blocked", "foul", "foul_tip", "hit_into_play", "hit_into_play_no_out", "hit_into_play_score"]
        )
        whiff_pct = _safe_pct(swinging.sum(), swings.sum())
    else:
        whiff_pct = None

    # Chase% requires zone data; approximate using 'zone' if present (out of zone > 9)
    chase_pct = None
    if {"zone", "description"}.issubset(df.columns):
        # swings at pitches out of the typical strike zone 1-9
        out_zone = ~df["zone"].isin([1,2,3,4,5,6,7,8,9])
        swings = df["description"].isin(
            ["swinging_strike", "swinging_strike_blocked", "foul", "foul_tip", "hit_into_play", "hit_into_play_no_out", "hit_into_play_score"]
        )
        chase_pct = _safe_pct((out_zone & swings).sum(), out_zone.sum())
    # Trend placeholders (computed later if you store historical velo)
    fastball_velo_trend = None

    return {
        "player_id": player_id,
        "player_name": player_name or f"Pitcher {player_id}",
        "pitch_hand": df["p_throws"].iloc[0] if "p_throws" in df.columns else None,
        "batters_faced": batters_faced,
        "k_pct": k_pct,
        "so_per_9": so_per_9,
        "hr_per_9": hr_per_9,
        "hr_per_fb": hr_per_fb,
        "fly_ball_pct": fly_ball_pct,
        "hard_hit_pct_against": hard_hit_pct,
        "barrel_pct_against": barrel_pct,
        "avg_exit_velo_against": avg_ev,
        "avg_fastball_velo": avg_fastball_velo,
        "fastball_velo_trend": fastball_velo_trend,
        "whiff_pct": whiff_pct,
        "chase_pct": chase_pct,
        # Leave advanced fields (era, fip, xfip, xera, splits) for future enhancements
    }


def _fetch_pitcher_window(start_dt: str, end_dt: str, pitcher_id: int) -> pd.DataFrame:
    """Call pybaseball.statcast_pitcher with the supported pitcher-id argument name."""
    params = inspect.signature(statcast_pitcher).parameters
    base_kwargs = {"start_dt": start_dt, "end_dt": end_dt}

    for key in ("player_id", "pitcher_id", "pitcher"):
        if key in params:
            return statcast_pitcher(**base_kwargs, **{key: pitcher_id})

    # Fallback for unexpected signatures: pass pitcher id as positional third arg.
    return statcast_pitcher(start_dt, end_dt, pitcher_id)


def _build_pitcher_team_map(as_of_date: str) -> dict[int, str]:
    """Map pitcher_id -> team abbreviation from the games table."""
    rows = query(
        """
        SELECT home_pitcher_id AS pid, home_team AS team FROM games WHERE game_date = ?
        UNION ALL
        SELECT away_pitcher_id AS pid, away_team AS team FROM games WHERE game_date = ?
        """,
        (as_of_date, as_of_date),
    )
    return {int(r["pid"]): str(r["team"]) for r in rows if r.get("pid") is not None}


def fetch_daily_pitcher_stats(pitcher_ids: list[int], as_of_date: str | None = None) -> int:
    """
    Fetch pitcher rolling stats for all pitcher_ids and write to pitcher_stats table.
    """
    if as_of_date is None:
        as_of_date = datetime.now().strftime("%Y-%m-%d")
    end_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    start_30 = end_dt - timedelta(days=30)
    start_14 = end_dt - timedelta(days=14)

    pitcher_team_map = _build_pitcher_team_map(as_of_date)

    rows_to_upsert = []
    for pid in sorted(set([int(x) for x in pitcher_ids if x])):
        try:
            df30 = _fetch_pitcher_window(start_dt=_date_str(start_30), end_dt=_date_str(end_dt), pitcher_id=pid)
            if df30 is None or df30.empty:
                continue

            team = pitcher_team_map.get(pid)

            # 30-day metrics
            m30 = _compute_pitcher_metrics(df30)
            m30.update({"stat_date": as_of_date, "window_days": 30, "team": team})
            rows_to_upsert.append(m30)

            # 14-day slice
            if "game_date" in df30.columns:
                df14 = df30[df30["game_date"] >= _date_str(start_14)]
            else:
                df14 = df30  # fallback
            m14 = _compute_pitcher_metrics(df14)
            m14.update({"stat_date": as_of_date, "window_days": 14, "team": team})
            rows_to_upsert.append(m14)

        except Exception as e:
            print(f"  ❌ Pitcher fetch failed for {pid}: {e}")

    # Remove keys not in schema by keeping intersection dynamically from first row and DB? We'll assume schema supports these keys.
    if not rows_to_upsert:
        return 0

    saved = upsert_many("pitcher_stats", rows_to_upsert, conflict_cols=["player_id", "stat_date", "window_days"])
    return saved
