"""
Statcast batter stats fetcher.

Pulls rolling window stats for HR-relevant metrics using pybaseball.
Only fetches recent windows (7/14/30 days) for daily runs — fast and lightweight.
"""
import pandas as pd
from datetime import datetime, timedelta
from pybaseball import statcast, playerid_lookup, statcast_batter
from pybaseball import cache as pb_cache

from config import BATTER_WINDOWS
from db.database import upsert_many

# Enable pybaseball caching to avoid re-fetching
pb_cache.enable()


def fetch_statcast_window(start_date: str, end_date: str) -> pd.DataFrame:
    """
    Pull Statcast data for a date range.
    For daily pipeline: pulls last 30 days max.
    Returns DataFrame with pitch-level data.
    """
    print(f"  📊 Fetching Statcast: {start_date} → {end_date}")
    df = statcast(start_dt=start_date, end_dt=end_date)
    if df is None or df.empty:
        print("  ⚠️  No Statcast data returned")
        return pd.DataFrame()
    print(f"  ✅ Got {len(df):,} pitches")
    return df


def compute_batter_hr_stats(df: pd.DataFrame, window_days: int) -> list[dict]:
    """
    From raw Statcast pitch data, compute per-batter HR-relevant aggregates.
    
    Key metrics:
    - barrel_pct: % of batted balls that are "barrels" (optimal EV + LA)
    - hard_hit_pct: % of batted balls 95+ mph
    - avg_exit_velo: mean exit velocity on batted balls
    - fly_ball_pct: % of batted balls that are fly balls
    - hr_per_fb: HR rate on fly balls
    - pull_pct: pull rate on fly balls (HRs go to pull side)
    - iso_power: SLG - AVG
    """
    if df.empty:
        return []

    # Filter to batted ball events only for Statcast metrics
    batted = df[df["launch_speed"].notna()].copy()
    
    # All plate appearances for counting stats
    pa_events = df[df["events"].notna()].copy()

    stat_date = datetime.now().strftime("%Y-%m-%d")
    rows = []

    for batter_id in batted["batter"].unique():
        batter_batted = batted[batted["batter"] == batter_id]
        batter_pa = pa_events[pa_events["batter"] == batter_id]
        
        if len(batter_batted) < 10:  # need minimum sample
            continue

        # Get player info from first row
        first_row = batter_batted.iloc[0]
        player_name = f"{first_row.get('player_name', 'Unknown')}"
        
        # Determine bat hand from stand column
        bat_hand = batter_batted["stand"].mode()
        bat_hand = bat_hand.iloc[0] if not bat_hand.empty else "R"

        # Core Statcast metrics
        n_batted = len(batter_batted)
        barrel_pct = (batter_batted["launch_speed_angle"].eq(6).sum() / n_batted * 100
                      if "launch_speed_angle" in batter_batted.columns else None)
        
        hard_hit = (batter_batted["launch_speed"] >= 95).sum() / n_batted * 100
        avg_ev = batter_batted["launch_speed"].mean()
        max_ev = batter_batted["launch_speed"].max()
        avg_la = batter_batted["launch_angle"].mean()
        
        # Sweet spot: launch angle 8-32 degrees (optimal HR range)
        sweet_spot = ((batter_batted["launch_angle"] >= 8) & 
                      (batter_batted["launch_angle"] <= 32)).sum() / n_batted * 100

        # Fly ball rate (LA > 25 degrees)
        fly_balls = batter_batted[batter_batted["launch_angle"] > 25]
        fb_pct = len(fly_balls) / n_batted * 100 if n_batted > 0 else 0

        # HR counting
        hr_events = batter_pa[batter_pa["events"] == "home_run"]
        hrs = len(hr_events)
        hr_per_fb = hrs / len(fly_balls) * 100 if len(fly_balls) > 0 else 0

        # Pull rate on fly balls
        if len(fly_balls) > 0 and "hc_x" in fly_balls.columns:
            # hc_x < 126 = pull side for RHB, > 126 = pull side for LHB
            if bat_hand == "R":
                pull_fbs = (fly_balls["hc_x"] < 126).sum()
            else:
                pull_fbs = (fly_balls["hc_x"] > 126).sum()
            pull_pct = pull_fbs / len(fly_balls) * 100
        else:
            pull_pct = None

        # Counting stats
        n_pa = len(batter_pa)
        n_ab = len(batter_pa[~batter_pa["events"].isin(
            ["walk", "hit_by_pitch", "sac_fly", "sac_bunt", "catcher_interf"]
        )])
        
        # Hits for AVG/SLG/ISO
        hits = batter_pa[batter_pa["events"].isin(
            ["single", "double", "triple", "home_run"]
        )]
        singles = (hits["events"] == "single").sum()
        doubles = (hits["events"] == "double").sum()
        triples = (hits["events"] == "triple").sum()
        
        avg = len(hits) / n_ab if n_ab > 0 else 0
        slg = (singles + 2*doubles + 3*triples + 4*hrs) / n_ab if n_ab > 0 else 0
        iso = slg - avg

        # K% and BB%
        ks = (batter_pa["events"] == "strikeout").sum()
        bbs = (batter_pa["events"].isin(["walk", "hit_by_pitch"])).sum()
        k_pct = ks / n_pa * 100 if n_pa > 0 else 0
        bb_pct = bbs / n_pa * 100 if n_pa > 0 else 0

        # xwOBA and xSLG from Statcast (if available)
        xwoba = batter_batted["estimated_woba_using_speedangle"].mean() if \
            "estimated_woba_using_speedangle" in batter_batted.columns else None
        
        # Handedness splits
        vs_lhp = batter_batted[batter_batted["p_throws"] == "L"]
        vs_rhp = batter_batted[batter_batted["p_throws"] == "R"]
        
        def calc_iso_split(split_batted, split_pa):
            if len(split_pa) < 5:
                return None
            s_ab = len(split_pa[~split_pa["events"].isin(
                ["walk", "hit_by_pitch", "sac_fly", "sac_bunt"])])
            if s_ab == 0:
                return None
            s_hits = split_pa[split_pa["events"].isin(
                ["single", "double", "triple", "home_run"])]
            s_1b = (s_hits["events"] == "single").sum()
            s_2b = (s_hits["events"] == "double").sum()
            s_3b = (s_hits["events"] == "triple").sum()
            s_hr = (s_hits["events"] == "home_run").sum()
            s_avg = len(s_hits) / s_ab
            s_slg = (s_1b + 2*s_2b + 3*s_3b + 4*s_hr) / s_ab
            return round(s_slg - s_avg, 3)

        vs_lhp_pa = pa_events[(pa_events["batter"] == batter_id) & (pa_events["p_throws"] == "L")]
        vs_rhp_pa = pa_events[(pa_events["batter"] == batter_id) & (pa_events["p_throws"] == "R")]

        rows.append({
            "player_id": int(batter_id),
            "player_name": player_name,
            "team": first_row.get("home_team", ""),
            "bat_hand": bat_hand,
            "stat_date": stat_date,
            "window_days": window_days,
            "barrel_pct": round(barrel_pct, 1) if barrel_pct else None,
            "hard_hit_pct": round(hard_hit, 1),
            "avg_exit_velo": round(avg_ev, 1),
            "max_exit_velo": round(max_ev, 1),
            "fly_ball_pct": round(fb_pct, 1),
            "hr_per_fb": round(hr_per_fb, 1),
            "pull_pct": round(pull_pct, 1) if pull_pct else None,
            "avg_launch_angle": round(avg_la, 1),
            "sweet_spot_pct": round(sweet_spot, 1),
            "iso_power": round(iso, 3),
            "slg": round(slg, 3),
            "woba": None,  # needs linear weights calc
            "xwoba": round(xwoba, 3) if xwoba else None,
            "xslg": None,  # from Statcast leaderboard
            "pa": n_pa,
            "ab": n_ab,
            "hrs": hrs,
            "k_pct": round(k_pct, 1),
            "bb_pct": round(bb_pct, 1),
            "iso_vs_lhp": calc_iso_split(vs_lhp, vs_lhp_pa),
            "iso_vs_rhp": calc_iso_split(vs_rhp, vs_rhp_pa),
            "barrel_pct_vs_lhp": None,  # TODO: split barrel calc
            "barrel_pct_vs_rhp": None,
            "hr_count_vs_lhp": len(vs_lhp_pa[vs_lhp_pa["events"] == "home_run"]) if len(vs_lhp_pa) > 0 else 0,
            "hr_count_vs_rhp": len(vs_rhp_pa[vs_rhp_pa["events"] == "home_run"]) if len(vs_rhp_pa) > 0 else 0,
        })

    return rows


def fetch_daily_batter_stats():
    """
    Main entry: fetch rolling window stats for all batters.
    Pulls data for the longest window (30 days) once, then slices for 7 and 14.
    """
    print("\n🏏 Fetching daily batter stats...")
    today = datetime.now()
    max_window = max(BATTER_WINDOWS)
    
    start = (today - timedelta(days=max_window)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    
    # One pull for the full 30-day window
    full_df = fetch_statcast_window(start, end)
    if full_df.empty:
        print("  ❌ No data — skipping batter stats")
        return

    all_rows = []
    for window in BATTER_WINDOWS:
        window_start = (today - timedelta(days=window)).strftime("%Y-%m-%d")
        window_df = full_df[full_df["game_date"] >= window_start]
        
        print(f"  📐 Computing {window}-day stats ({len(window_df):,} pitches)...")
        rows = compute_batter_hr_stats(window_df, window)
        all_rows.extend(rows)
        print(f"  ✅ {len(rows)} batters computed for {window}-day window")

    # Save to database (Supabase upserts on unique constraint automatically)
    count = upsert_many("batter_stats", all_rows)
    print(f"  💾 Saved {count} batter stat rows to database")
    return all_rows
