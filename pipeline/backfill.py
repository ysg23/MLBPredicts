"""
Historical Statcast backfill loader.

Phase 1A requirements:
- Load seasonal CSVs (2023-2025) from /pipeline/data
- Process one season at a time with chunked reads
- Compute rolling batter/pitcher stats by game date
- Populate games table from historical game data
- Track batter-level HR outcomes by game for backtesting
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config import DATA_DIR
from db.database import insert_many, query, upsert_many


DEFAULT_SEASONS = [2023, 2024, 2025]
CHUNK_SIZE = 100_000
DB_BATCH_SIZE = 500
FULL_SEASON_WINDOW = 0
BATTER_WINDOWS = [7, 14, 30, FULL_SEASON_WINDOW]
PITCHER_WINDOWS = [14, 30, FULL_SEASON_WINDOW]

NON_AB_EVENTS = {
    "walk",
    "intent_walk",
    "hit_by_pitch",
    "sac_fly",
    "sac_bunt",
    "catcher_interf",
}

SWING_DESCRIPTIONS = {
    "swinging_strike",
    "swinging_strike_blocked",
    "foul",
    "foul_tip",
    "hit_into_play",
    "hit_into_play_score",
    "hit_into_play_no_out",
    "foul_bunt",
    "missed_bunt",
    "foul_pitchout",
}

WHIFF_DESCRIPTIONS = {
    "swinging_strike",
    "swinging_strike_blocked",
    "missed_bunt",
}

FASTBALL_TYPES = {"FF", "FA", "FT", "SI", "FC"}

OUTS_MAP = {
    "field_out": 1,
    "force_out": 1,
    "grounded_into_double_play": 2,
    "double_play": 2,
    "triple_play": 3,
    "fielders_choice_out": 1,
    "strikeout": 1,
    "strikeout_double_play": 2,
    "sac_fly": 1,
    "sac_fly_double_play": 2,
    "sac_bunt": 1,
    "bunt_groundout": 1,
    "bunt_pop_out": 1,
    "bunt_lineout": 1,
    "lineout": 1,
    "flyout": 1,
    "pop_out": 1,
}


def _get_series(df: pd.DataFrame, candidates: list[str], default: Any = np.nan) -> pd.Series:
    """Return first matching column or a default-valued Series."""
    for col in candidates:
        if col in df.columns:
            return df[col]
    return pd.Series([default] * len(df), index=df.index)


def _clean_text(series: pd.Series) -> pd.Series:
    """Normalize free-text columns."""
    cleaned = (
        series.fillna("")
        .astype(str)
        .str.strip()
        .str.replace(r"\s+", " ", regex=True)
    )
    return cleaned.replace("nan", "")


def _fallback_name(name: Any, prefix: str, player_id: int) -> str:
    """Ensure player_name columns are never empty."""
    if isinstance(name, str):
        v = name.strip()
        if v:
            return v
    return f"{prefix} {player_id}"


def _as_sql_value(value: Any) -> Any:
    """Convert pandas/numpy scalars to database-compatible Python types."""
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if np.isnan(value):
            return None
        return float(value)
    if isinstance(value, float) and np.isnan(value):
        return None
    if pd.isna(value):
        return None
    return value


def _batch_upsert(
    table: str,
    rows: list[dict[str, Any]],
    conflict_cols: list[str],
    batch_size: int = DB_BATCH_SIZE,
) -> int:
    """Upsert rows in fixed-size batches to avoid API timeouts."""
    if not rows:
        return 0

    total = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        cleaned = [{k: _as_sql_value(v) for k, v in row.items()} for row in batch]
        try:
            total += upsert_many(table, cleaned, conflict_cols)
        except Exception:
            # Fallback keeps ingestion moving if backend upsert behavior changes.
            total += insert_many(table, cleaned)
    return total


def _ping_database() -> None:
    """
    Best-effort connectivity check via database helper API.
    Backfill can still proceed if this helper isn't implemented as SQL passthrough.
    """
    try:
        query("SELECT 1")
    except Exception:
        pass


def _init_batter_day() -> dict[str, Any]:
    return {
        "player_name": "",
        "team": "",
        "opponent": "",
        "bat_hand": "",
        "pa": 0,
        "ab": 0,
        "singles": 0,
        "doubles": 0,
        "triples": 0,
        "hrs": 0,
        "ks": 0,
        "bbs": 0,
        "batted": 0,
        "barrels": 0,
        "hard_hit": 0,
        "ev_sum": 0.0,
        "ev_max": float("-inf"),
        "la_sum": 0.0,
        "sweet_spot": 0,
        "fly_balls": 0,
        "pull_fly_balls": 0,
        "lhp_pa": 0,
        "lhp_ab": 0,
        "lhp_singles": 0,
        "lhp_doubles": 0,
        "lhp_triples": 0,
        "lhp_hrs": 0,
        "lhp_batted": 0,
        "lhp_barrels": 0,
        "rhp_pa": 0,
        "rhp_ab": 0,
        "rhp_singles": 0,
        "rhp_doubles": 0,
        "rhp_triples": 0,
        "rhp_hrs": 0,
        "rhp_batted": 0,
        "rhp_barrels": 0,
    }


def _init_pitcher_day() -> dict[str, Any]:
    return {
        "player_name": "",
        "team": "",
        "pitch_hand": "",
        "games": 0,
        "pitches": 0,
        "pa": 0,
        "ab": 0,
        "singles": 0,
        "doubles": 0,
        "triples": 0,
        "hrs": 0,
        "outs_recorded": 0,
        "batted": 0,
        "barrels": 0,
        "hard_hit": 0,
        "ev_sum": 0.0,
        "ev_max": float("-inf"),
        "la_sum": 0.0,
        "fly_balls": 0,
        "swings": 0,
        "whiffs": 0,
        "in_zone_pitches": 0,
        "out_zone_pitches": 0,
        "chase_swings": 0,
        "fb_velo_sum": 0.0,
        "fb_velo_count": 0,
        "lhb_pa": 0,
        "lhb_ab": 0,
        "lhb_singles": 0,
        "lhb_doubles": 0,
        "lhb_triples": 0,
        "lhb_hrs": 0,
        "lhb_outs": 0,
        "rhb_pa": 0,
        "rhb_ab": 0,
        "rhb_singles": 0,
        "rhb_doubles": 0,
        "rhb_triples": 0,
        "rhb_hrs": 0,
        "rhb_outs": 0,
    }


BATTER_SUM_FIELDS = [
    "pa",
    "ab",
    "singles",
    "doubles",
    "triples",
    "hrs",
    "ks",
    "bbs",
    "batted",
    "barrels",
    "hard_hit",
    "ev_sum",
    "la_sum",
    "sweet_spot",
    "fly_balls",
    "pull_fly_balls",
]

BATTER_SPLIT_FIELDS = [
    "pa",
    "ab",
    "singles",
    "doubles",
    "triples",
    "hrs",
    "batted",
    "barrels",
]

PITCHER_SUM_FIELDS = [
    "games",
    "pitches",
    "pa",
    "ab",
    "singles",
    "doubles",
    "triples",
    "hrs",
    "outs_recorded",
    "batted",
    "barrels",
    "hard_hit",
    "ev_sum",
    "la_sum",
    "fly_balls",
    "swings",
    "whiffs",
    "in_zone_pitches",
    "out_zone_pitches",
    "chase_swings",
    "fb_velo_sum",
    "fb_velo_count",
]

PITCHER_SPLIT_FIELDS = [
    "pa",
    "ab",
    "singles",
    "doubles",
    "triples",
    "hrs",
    "outs_recorded",
]


def _safe_pct(numer: float, denom: float, digits: int = 1) -> float | None:
    if denom <= 0:
        return None
    return round((numer / denom) * 100, digits)


def _safe_avg(numer: float, denom: float, digits: int = 1) -> float | None:
    if denom <= 0:
        return None
    return round(numer / denom, digits)


def _calc_iso(singles: float, doubles: float, triples: float, hrs: float, ab: float) -> float | None:
    if ab <= 0:
        return None
    hits = singles + doubles + triples + hrs
    avg = hits / ab
    slg = (singles + 2 * doubles + 3 * triples + 4 * hrs) / ab
    return round(slg - avg, 3)


def _process_chunk(
    chunk: pd.DataFrame,
    games_meta: dict[int, dict[str, Any]],
    starter_workloads: dict[tuple[int, str, int], int],
    batter_daily: dict[tuple[int, str], dict[str, Any]],
    pitcher_daily: dict[tuple[int, str], dict[str, Any]],
    outcomes: dict[tuple[int, int], dict[str, Any]],
    batter_name_map: dict[int, str],
    pitcher_name_map: dict[int, str],
    pitcher_hand_map: dict[int, str],
) -> None:
    """Aggregate one CSV chunk into season-level dictionaries."""
    if chunk.empty:
        return

    chunk = chunk.copy()
    chunk.columns = [c.strip() for c in chunk.columns]

    work = pd.DataFrame(index=chunk.index)
    work["game_id"] = pd.to_numeric(_get_series(chunk, ["game_pk", "game_id"]), errors="coerce")
    work["game_date"] = pd.to_datetime(_get_series(chunk, ["game_date"]), errors="coerce").dt.strftime(
        "%Y-%m-%d"
    )
    work["home_team"] = _clean_text(_get_series(chunk, ["home_team"]))
    work["away_team"] = _clean_text(_get_series(chunk, ["away_team"]))
    work["inning_topbot"] = _clean_text(_get_series(chunk, ["inning_topbot"])).str.lower()
    work["post_home_score"] = pd.to_numeric(
        _get_series(chunk, ["post_home_score", "home_score"]), errors="coerce"
    )
    work["post_away_score"] = pd.to_numeric(
        _get_series(chunk, ["post_away_score", "away_score"]), errors="coerce"
    )

    work["batter_id"] = pd.to_numeric(_get_series(chunk, ["batter"]), errors="coerce")
    work["pitcher_id"] = pd.to_numeric(_get_series(chunk, ["pitcher"]), errors="coerce")

    work["batter_name"] = _clean_text(_get_series(chunk, ["batter_name", "batter_player_name"]))
    work["pitcher_name"] = _clean_text(
        _get_series(chunk, ["pitcher_name", "pitcher_player_name", "player_name"])
    )

    work["events"] = _clean_text(_get_series(chunk, ["events"])).str.lower()
    work["description"] = _clean_text(_get_series(chunk, ["description"])).str.lower()
    work["launch_speed"] = pd.to_numeric(_get_series(chunk, ["launch_speed"]), errors="coerce")
    work["launch_angle"] = pd.to_numeric(_get_series(chunk, ["launch_angle"]), errors="coerce")
    work["launch_speed_angle"] = pd.to_numeric(
        _get_series(chunk, ["launch_speed_angle"]), errors="coerce"
    )
    work["hc_x"] = pd.to_numeric(_get_series(chunk, ["hc_x"]), errors="coerce")
    work["p_throws"] = _clean_text(_get_series(chunk, ["p_throws"])).str.upper()
    work["stand"] = _clean_text(_get_series(chunk, ["stand"])).str.upper()
    work["pitch_type"] = _clean_text(_get_series(chunk, ["pitch_type"])).str.upper()
    work["release_speed"] = pd.to_numeric(_get_series(chunk, ["release_speed"]), errors="coerce")
    work["zone"] = pd.to_numeric(_get_series(chunk, ["zone"]), errors="coerce")

    work = work[work["game_id"].notna() & work["game_date"].notna()].copy()
    if work.empty:
        return

    work["game_id"] = work["game_id"].astype(int)
    top_mask = work["inning_topbot"].eq("top")
    work["bat_team"] = np.where(top_mask, work["away_team"], work["home_team"])
    work["opp_team"] = np.where(top_mask, work["home_team"], work["away_team"])
    work["def_team"] = np.where(top_mask, work["home_team"], work["away_team"])

    # Game-level metadata
    game_agg = (
        work.groupby("game_id", sort=False)
        .agg(
            game_date=("game_date", "min"),
            home_team=("home_team", "first"),
            away_team=("away_team", "first"),
            home_score=("post_home_score", "max"),
            away_score=("post_away_score", "max"),
        )
        .reset_index()
    )
    for row in game_agg.itertuples(index=False):
        gid = int(row.game_id)
        entry = games_meta.setdefault(
            gid,
            {
                "game_id": gid,
                "game_date": row.game_date,
                "home_team": row.home_team,
                "away_team": row.away_team,
                "home_score": None,
                "away_score": None,
            },
        )
        if isinstance(row.game_date, str) and row.game_date:
            entry["game_date"] = min(entry["game_date"], row.game_date) if entry["game_date"] else row.game_date
        if isinstance(row.home_team, str) and row.home_team:
            entry["home_team"] = row.home_team
        if isinstance(row.away_team, str) and row.away_team:
            entry["away_team"] = row.away_team
        if not pd.isna(row.home_score):
            score = int(row.home_score)
            entry["home_score"] = max(entry["home_score"], score) if entry["home_score"] is not None else score
        if not pd.isna(row.away_score):
            score = int(row.away_score)
            entry["away_score"] = max(entry["away_score"], score) if entry["away_score"] is not None else score

    # Starter inference: highest workload pitcher for each game/team.
    starter_df = work[work["pitcher_id"].notna() & work["def_team"].ne("")].copy()
    if not starter_df.empty:
        starter_df["pitcher_id"] = starter_df["pitcher_id"].astype(int)
        workload = starter_df.groupby(["game_id", "def_team", "pitcher_id"], sort=False).size().reset_index(name="n")
        for row in workload.itertuples(index=False):
            key = (int(row.game_id), row.def_team, int(row.pitcher_id))
            starter_workloads[key] += int(row.n)

    # Name and handedness maps
    pname = work[work["pitcher_id"].notna() & work["pitcher_name"].ne("")][["pitcher_id", "pitcher_name"]]
    if not pname.empty:
        for row in pname.drop_duplicates(subset=["pitcher_id"]).itertuples(index=False):
            pid = int(row.pitcher_id)
            pitcher_name_map.setdefault(pid, row.pitcher_name)

    bname = work[work["batter_id"].notna() & work["batter_name"].ne("")][["batter_id", "batter_name"]]
    if not bname.empty:
        for row in bname.drop_duplicates(subset=["batter_id"]).itertuples(index=False):
            bid = int(row.batter_id)
            batter_name_map.setdefault(bid, row.batter_name)

    phand = work[work["pitcher_id"].notna() & work["p_throws"].isin(["L", "R"])][["pitcher_id", "p_throws"]]
    if not phand.empty:
        for row in phand.drop_duplicates(subset=["pitcher_id"]).itertuples(index=False):
            pitcher_hand_map.setdefault(int(row.pitcher_id), row.p_throws)

    # ---------------- Batter daily aggregates ----------------
    bdf = work[work["batter_id"].notna()].copy()
    if not bdf.empty:
        bdf["batter_id"] = bdf["batter_id"].astype(int)
        bdf["is_pa"] = bdf["events"].ne("").astype(int)
        bdf["is_ab"] = (bdf["events"].ne("") & ~bdf["events"].isin(NON_AB_EVENTS)).astype(int)
        bdf["is_single"] = bdf["events"].eq("single").astype(int)
        bdf["is_double"] = bdf["events"].eq("double").astype(int)
        bdf["is_triple"] = bdf["events"].eq("triple").astype(int)
        bdf["is_hr"] = bdf["events"].eq("home_run").astype(int)
        bdf["is_k"] = bdf["events"].str.startswith("strikeout").astype(int)
        bdf["is_bb"] = bdf["events"].isin({"walk", "intent_walk", "hit_by_pitch"}).astype(int)
        bdf["is_batted"] = bdf["launch_speed"].notna().astype(int)
        bdf["is_barrel"] = (bdf["launch_speed_angle"] == 6).astype(int)
        bdf["is_hard_hit"] = (bdf["launch_speed"] >= 95).astype(int)
        bdf["ev_sum"] = bdf["launch_speed"].fillna(0.0)
        bdf["ev_max"] = bdf["launch_speed"].fillna(float("-inf"))
        bdf["la_sum"] = bdf["launch_angle"].fillna(0.0)
        bdf["is_sweet_spot"] = (
            bdf["launch_angle"].between(8, 32, inclusive="both") & bdf["launch_speed"].notna()
        ).astype(int)
        bdf["is_fly_ball"] = ((bdf["launch_angle"] > 25) & bdf["launch_speed"].notna()).astype(int)
        bdf["is_pull_fly"] = (
            (
                bdf["stand"].eq("R")
                & (bdf["hc_x"] < 126)
                & (bdf["launch_angle"] > 25)
                & bdf["launch_speed"].notna()
            )
            | (
                bdf["stand"].eq("L")
                & (bdf["hc_x"] > 126)
                & (bdf["launch_angle"] > 25)
                & bdf["launch_speed"].notna()
            )
        ).astype(int)

        overall = (
            bdf.groupby(["batter_id", "game_date"], sort=False)
            .agg(
                player_name=("batter_name", "last"),
                team=("bat_team", "last"),
                opponent=("opp_team", "last"),
                bat_hand=("stand", "last"),
                pa=("is_pa", "sum"),
                ab=("is_ab", "sum"),
                singles=("is_single", "sum"),
                doubles=("is_double", "sum"),
                triples=("is_triple", "sum"),
                hrs=("is_hr", "sum"),
                ks=("is_k", "sum"),
                bbs=("is_bb", "sum"),
                batted=("is_batted", "sum"),
                barrels=("is_barrel", "sum"),
                hard_hit=("is_hard_hit", "sum"),
                ev_sum=("ev_sum", "sum"),
                ev_max=("ev_max", "max"),
                la_sum=("la_sum", "sum"),
                sweet_spot=("is_sweet_spot", "sum"),
                fly_balls=("is_fly_ball", "sum"),
                pull_fly_balls=("is_pull_fly", "sum"),
            )
            .reset_index()
        )

        for row in overall.itertuples(index=False):
            key = (int(row.batter_id), row.game_date)
            entry = batter_daily.setdefault(key, _init_batter_day())
            entry["player_name"] = row.player_name or entry["player_name"]
            entry["team"] = row.team or entry["team"]
            entry["opponent"] = row.opponent or entry["opponent"]
            if row.bat_hand in {"L", "R", "S"}:
                entry["bat_hand"] = row.bat_hand
            for col in BATTER_SUM_FIELDS:
                entry[col] += float(getattr(row, col))
            entry["ev_max"] = max(entry["ev_max"], float(row.ev_max))

        split = (
            bdf[bdf["p_throws"].isin(["L", "R"])]
            .groupby(["batter_id", "game_date", "p_throws"], sort=False)
            .agg(
                pa=("is_pa", "sum"),
                ab=("is_ab", "sum"),
                singles=("is_single", "sum"),
                doubles=("is_double", "sum"),
                triples=("is_triple", "sum"),
                hrs=("is_hr", "sum"),
                batted=("is_batted", "sum"),
                barrels=("is_barrel", "sum"),
            )
            .reset_index()
        )

        for row in split.itertuples(index=False):
            key = (int(row.batter_id), row.game_date)
            entry = batter_daily.setdefault(key, _init_batter_day())
            prefix = "lhp" if row.p_throws == "L" else "rhp"
            for metric in BATTER_SPLIT_FIELDS:
                entry[f"{prefix}_{metric}"] += float(getattr(row, metric))

        outcome_group = (
            bdf.groupby(["game_id", "game_date", "batter_id"], sort=False)
            .agg(
                player_name=("batter_name", "last"),
                team=("bat_team", "last"),
                opponent=("opp_team", "last"),
                pa=("is_pa", "sum"),
                hr_count=("is_hr", "sum"),
            )
            .reset_index()
        )
        for row in outcome_group.itertuples(index=False):
            key = (int(row.game_id), int(row.batter_id))
            entry = outcomes.setdefault(
                key,
                {
                    "game_id": int(row.game_id),
                    "game_date": row.game_date,
                    "player_id": int(row.batter_id),
                    "player_name": row.player_name or "",
                    "team": row.team or "",
                    "opponent": row.opponent or "",
                    "pa": 0,
                    "hr_count": 0,
                },
            )
            entry["pa"] += int(row.pa)
            entry["hr_count"] += int(row.hr_count)
            if row.player_name:
                entry["player_name"] = row.player_name
            if row.team:
                entry["team"] = row.team
            if row.opponent:
                entry["opponent"] = row.opponent

    # ---------------- Pitcher daily aggregates ----------------
    pdf = work[work["pitcher_id"].notna()].copy()
    if not pdf.empty:
        pdf["pitcher_id"] = pdf["pitcher_id"].astype(int)
        pdf["is_pa"] = pdf["events"].ne("").astype(int)
        pdf["is_ab"] = (pdf["events"].ne("") & ~pdf["events"].isin(NON_AB_EVENTS)).astype(int)
        pdf["is_single"] = pdf["events"].eq("single").astype(int)
        pdf["is_double"] = pdf["events"].eq("double").astype(int)
        pdf["is_triple"] = pdf["events"].eq("triple").astype(int)
        pdf["is_hr"] = pdf["events"].eq("home_run").astype(int)
        pdf["outs_recorded"] = pdf["events"].map(OUTS_MAP).fillna(0).astype(int)
        pdf["is_batted"] = pdf["launch_speed"].notna().astype(int)
        pdf["is_barrel"] = (pdf["launch_speed_angle"] == 6).astype(int)
        pdf["is_hard_hit"] = (pdf["launch_speed"] >= 95).astype(int)
        pdf["ev_sum"] = pdf["launch_speed"].fillna(0.0)
        pdf["ev_max"] = pdf["launch_speed"].fillna(float("-inf"))
        pdf["la_sum"] = pdf["launch_angle"].fillna(0.0)
        pdf["is_fly_ball"] = ((pdf["launch_angle"] > 25) & pdf["launch_speed"].notna()).astype(int)
        pdf["is_swing"] = pdf["description"].isin(SWING_DESCRIPTIONS).astype(int)
        pdf["is_whiff"] = pdf["description"].isin(WHIFF_DESCRIPTIONS).astype(int)
        pdf["in_zone"] = pdf["zone"].between(1, 9, inclusive="both").astype(int)
        pdf["out_zone"] = ((pdf["zone"].notna()) & ~pdf["zone"].between(1, 9, inclusive="both")).astype(int)
        pdf["is_chase_swing"] = ((pdf["is_swing"] == 1) & (pdf["out_zone"] == 1)).astype(int)
        pdf["is_fastball"] = (pdf["pitch_type"].isin(FASTBALL_TYPES) & pdf["release_speed"].notna()).astype(int)
        pdf["fb_velo_sum"] = np.where(pdf["is_fastball"] == 1, pdf["release_speed"], 0.0)
        pdf["fb_velo_count"] = pdf["is_fastball"]

        overall = (
            pdf.groupby(["pitcher_id", "game_date"], sort=False)
            .agg(
                player_name=("pitcher_name", "last"),
                team=("def_team", "last"),
                pitch_hand=("p_throws", "last"),
                games=("game_id", "nunique"),
                pitches=("pitcher_id", "size"),
                pa=("is_pa", "sum"),
                ab=("is_ab", "sum"),
                singles=("is_single", "sum"),
                doubles=("is_double", "sum"),
                triples=("is_triple", "sum"),
                hrs=("is_hr", "sum"),
                outs_recorded=("outs_recorded", "sum"),
                batted=("is_batted", "sum"),
                barrels=("is_barrel", "sum"),
                hard_hit=("is_hard_hit", "sum"),
                ev_sum=("ev_sum", "sum"),
                ev_max=("ev_max", "max"),
                la_sum=("la_sum", "sum"),
                fly_balls=("is_fly_ball", "sum"),
                swings=("is_swing", "sum"),
                whiffs=("is_whiff", "sum"),
                in_zone_pitches=("in_zone", "sum"),
                out_zone_pitches=("out_zone", "sum"),
                chase_swings=("is_chase_swing", "sum"),
                fb_velo_sum=("fb_velo_sum", "sum"),
                fb_velo_count=("fb_velo_count", "sum"),
            )
            .reset_index()
        )

        for row in overall.itertuples(index=False):
            key = (int(row.pitcher_id), row.game_date)
            entry = pitcher_daily.setdefault(key, _init_pitcher_day())
            entry["player_name"] = row.player_name or entry["player_name"]
            entry["team"] = row.team or entry["team"]
            if row.pitch_hand in {"L", "R"}:
                entry["pitch_hand"] = row.pitch_hand
            for col in PITCHER_SUM_FIELDS:
                entry[col] += float(getattr(row, col))
            entry["ev_max"] = max(entry["ev_max"], float(row.ev_max))

        split = (
            pdf[pdf["stand"].isin(["L", "R"])]
            .groupby(["pitcher_id", "game_date", "stand"], sort=False)
            .agg(
                pa=("is_pa", "sum"),
                ab=("is_ab", "sum"),
                singles=("is_single", "sum"),
                doubles=("is_double", "sum"),
                triples=("is_triple", "sum"),
                hrs=("is_hr", "sum"),
                outs_recorded=("outs_recorded", "sum"),
            )
            .reset_index()
        )

        for row in split.itertuples(index=False):
            key = (int(row.pitcher_id), row.game_date)
            entry = pitcher_daily.setdefault(key, _init_pitcher_day())
            prefix = "lhb" if row.stand == "L" else "rhb"
            for metric in PITCHER_SPLIT_FIELDS:
                src_col = "outs_recorded" if metric == "outs_recorded" else metric
                dst_col = "outs" if metric == "outs_recorded" else metric
                entry[f"{prefix}_{dst_col}"] += float(getattr(row, src_col))


def _build_games_rows(
    games_meta: dict[int, dict[str, Any]],
    starter_workloads: dict[tuple[int, str, int], int],
    pitcher_name_map: dict[int, str],
    pitcher_hand_map: dict[int, str],
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    """Resolve starter IDs per game and produce DB-ready game rows."""
    assignments: dict[int, dict[str, Any]] = {}

    for game_id, meta in games_meta.items():
        home_team = meta.get("home_team", "")
        away_team = meta.get("away_team", "")

        home_candidates = [
            (pid, n)
            for (gid, team, pid), n in starter_workloads.items()
            if gid == game_id and team == home_team
        ]
        away_candidates = [
            (pid, n)
            for (gid, team, pid), n in starter_workloads.items()
            if gid == game_id and team == away_team
        ]

        home_pitcher_id = max(home_candidates, key=lambda x: x[1])[0] if home_candidates else None
        away_pitcher_id = max(away_candidates, key=lambda x: x[1])[0] if away_candidates else None

        assignments[game_id] = {
            "home_pitcher_id": home_pitcher_id,
            "away_pitcher_id": away_pitcher_id,
        }

    rows: list[dict[str, Any]] = []
    for game_id, meta in sorted(games_meta.items(), key=lambda x: (x[1].get("game_date", ""), x[0])):
        assign = assignments.get(game_id, {})
        home_pitcher_id = assign.get("home_pitcher_id")
        away_pitcher_id = assign.get("away_pitcher_id")

        home_name = (
            _fallback_name(pitcher_name_map.get(home_pitcher_id, ""), "Pitcher", home_pitcher_id)
            if home_pitcher_id
            else "TBD"
        )
        away_name = (
            _fallback_name(pitcher_name_map.get(away_pitcher_id, ""), "Pitcher", away_pitcher_id)
            if away_pitcher_id
            else "TBD"
        )

        rows.append(
            {
                "game_id": game_id,
                "game_date": meta.get("game_date"),
                "game_time": None,
                "home_team": meta.get("home_team"),
                "away_team": meta.get("away_team"),
                "stadium_id": None,
                "home_pitcher_id": home_pitcher_id,
                "away_pitcher_id": away_pitcher_id,
                "home_pitcher_name": home_name,
                "away_pitcher_name": away_name,
                "home_pitcher_hand": pitcher_hand_map.get(home_pitcher_id),
                "away_pitcher_hand": pitcher_hand_map.get(away_pitcher_id),
                "umpire_name": None,
                "status": "final",
                "home_score": meta.get("home_score"),
                "away_score": meta.get("away_score"),
            }
        )

    return rows, assignments


def _dict_to_df(
    data: dict[tuple[int, str], dict[str, Any]],
    id_col: str,
    date_col: str = "game_date",
) -> pd.DataFrame:
    rows = []
    for (player_id, stat_date), values in data.items():
        row = {id_col: player_id, date_col: stat_date}
        row.update(values)
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _compute_batter_window_row(
    player_id: int,
    player_name: str,
    team: str,
    bat_hand: str,
    stat_date: str,
    window_days: int,
    frame: pd.DataFrame,
) -> dict[str, Any] | None:
    pa = float(frame["pa"].sum())
    if pa <= 0:
        return None

    ab = float(frame["ab"].sum())
    singles = float(frame["singles"].sum())
    doubles = float(frame["doubles"].sum())
    triples = float(frame["triples"].sum())
    hrs = float(frame["hrs"].sum())
    hits = singles + doubles + triples + hrs

    batted = float(frame["batted"].sum())
    barrels = float(frame["barrels"].sum())
    hard_hit = float(frame["hard_hit"].sum())
    ev_sum = float(frame["ev_sum"].sum())
    ev_max = float(frame["ev_max"].max()) if len(frame) else float("-inf")
    la_sum = float(frame["la_sum"].sum())
    sweet_spot = float(frame["sweet_spot"].sum())
    fly_balls = float(frame["fly_balls"].sum())
    pull_fly_balls = float(frame["pull_fly_balls"].sum())
    ks = float(frame["ks"].sum())
    bbs = float(frame["bbs"].sum())

    slg = round((singles + 2 * doubles + 3 * triples + 4 * hrs) / ab, 3) if ab > 0 else None
    iso_power = _calc_iso(singles, doubles, triples, hrs, ab)

    lhp_ab = float(frame["lhp_ab"].sum())
    lhp_iso = _calc_iso(
        float(frame["lhp_singles"].sum()),
        float(frame["lhp_doubles"].sum()),
        float(frame["lhp_triples"].sum()),
        float(frame["lhp_hrs"].sum()),
        lhp_ab,
    )
    rhp_ab = float(frame["rhp_ab"].sum())
    rhp_iso = _calc_iso(
        float(frame["rhp_singles"].sum()),
        float(frame["rhp_doubles"].sum()),
        float(frame["rhp_triples"].sum()),
        float(frame["rhp_hrs"].sum()),
        rhp_ab,
    )

    row = {
        "player_id": player_id,
        "player_name": _fallback_name(player_name, "Player", player_id),
        "team": team,
        "bat_hand": bat_hand if bat_hand in {"L", "R", "S"} else None,
        "stat_date": stat_date,
        "window_days": int(window_days),
        "barrel_pct": _safe_pct(barrels, batted),
        "hard_hit_pct": _safe_pct(hard_hit, batted),
        "avg_exit_velo": _safe_avg(ev_sum, batted),
        "max_exit_velo": round(ev_max, 1) if ev_max != float("-inf") else None,
        "fly_ball_pct": _safe_pct(fly_balls, batted),
        "hr_per_fb": _safe_pct(hrs, fly_balls),
        "pull_pct": _safe_pct(pull_fly_balls, fly_balls),
        "avg_launch_angle": _safe_avg(la_sum, batted),
        "sweet_spot_pct": _safe_pct(sweet_spot, batted),
        "iso_power": iso_power,
        "slg": slg,
        "woba": None,
        "xwoba": None,
        "xslg": None,
        "pa": int(pa),
        "ab": int(ab),
        "hrs": int(hrs),
        "k_pct": _safe_pct(ks, pa),
        "bb_pct": _safe_pct(bbs, pa),
        "iso_vs_lhp": lhp_iso,
        "iso_vs_rhp": rhp_iso,
        "barrel_pct_vs_lhp": _safe_pct(float(frame["lhp_barrels"].sum()), float(frame["lhp_batted"].sum())),
        "barrel_pct_vs_rhp": _safe_pct(float(frame["rhp_barrels"].sum()), float(frame["rhp_batted"].sum())),
        "hr_count_vs_lhp": int(frame["lhp_hrs"].sum()),
        "hr_count_vs_rhp": int(frame["rhp_hrs"].sum()),
    }
    return row


def _build_batter_rows(batter_daily_df: pd.DataFrame) -> list[dict[str, Any]]:
    """Build rolling-window batter rows for all eligible hitters."""
    if batter_daily_df.empty:
        return []

    season_pa = batter_daily_df.groupby("player_id")["pa"].sum()
    eligible_ids = set(season_pa[season_pa >= 100].index.tolist())
    batter_daily_df = batter_daily_df[batter_daily_df["player_id"].isin(eligible_ids)].copy()
    if batter_daily_df.empty:
        return []

    batter_daily_df["game_date_dt"] = pd.to_datetime(batter_daily_df["game_date"], errors="coerce")
    batter_daily_df = batter_daily_df[batter_daily_df["game_date_dt"].notna()].copy()
    batter_daily_df.sort_values(["player_id", "game_date_dt"], inplace=True)

    rows: list[dict[str, Any]] = []
    player_ids = batter_daily_df["player_id"].drop_duplicates().tolist()
    total_players = len(player_ids)
    print(f"  Computing batter rolling windows for {total_players} eligible batters...")

    for idx, player_id in enumerate(player_ids, start=1):
        player_df = batter_daily_df[batter_daily_df["player_id"] == player_id]
        player_df = player_df.sort_values("game_date_dt").reset_index(drop=True)
        dates = player_df["game_date_dt"].tolist()

        for row_idx, current_date in enumerate(dates):
            current = player_df.iloc[row_idx]
            stat_date = current_date.strftime("%Y-%m-%d")
            team = current.get("team", "")
            player_name = current.get("player_name", "")
            bat_hand = current.get("bat_hand", "")

            for window in BATTER_WINDOWS:
                if window == FULL_SEASON_WINDOW:
                    subset = player_df[player_df["game_date_dt"] <= current_date]
                else:
                    start = current_date - timedelta(days=window - 1)
                    subset = player_df[
                        (player_df["game_date_dt"] >= start) & (player_df["game_date_dt"] <= current_date)
                    ]
                computed = _compute_batter_window_row(
                    player_id=player_id,
                    player_name=player_name,
                    team=team,
                    bat_hand=bat_hand,
                    stat_date=stat_date,
                    window_days=window,
                    frame=subset,
                )
                if computed:
                    rows.append(computed)

        if idx % 100 == 0 or idx == total_players:
            pct = (idx / total_players) * 100 if total_players else 100.0
            print(f"  Batter rolling progress: {pct:5.1f}% ({idx}/{total_players})")

    return rows


def _compute_pitcher_window_row(
    player_id: int,
    player_name: str,
    team: str,
    pitch_hand: str,
    stat_date: str,
    window_days: int,
    frame: pd.DataFrame,
    season_fb_velo: float | None,
    days_rest: int | None,
) -> dict[str, Any] | None:
    pitches = float(frame["pitches"].sum())
    if pitches <= 0:
        return None

    # One row per pitcher/day here, so row count is robust across chunk boundaries.
    games = float(len(frame))
    pa = float(frame["pa"].sum())
    ab = float(frame["ab"].sum())
    singles = float(frame["singles"].sum())
    doubles = float(frame["doubles"].sum())
    triples = float(frame["triples"].sum())
    hrs = float(frame["hrs"].sum())
    outs = float(frame["outs_recorded"].sum())
    innings = outs / 3.0 if outs > 0 else 0.0

    batted = float(frame["batted"].sum())
    barrels = float(frame["barrels"].sum())
    hard_hit = float(frame["hard_hit"].sum())
    ev_sum = float(frame["ev_sum"].sum())
    fly_balls = float(frame["fly_balls"].sum())
    swings = float(frame["swings"].sum())
    whiffs = float(frame["whiffs"].sum())
    in_zone = float(frame["in_zone_pitches"].sum())
    out_zone = float(frame["out_zone_pitches"].sum())
    chase_swings = float(frame["chase_swings"].sum())
    fb_velo_sum = float(frame["fb_velo_sum"].sum())
    fb_velo_count = float(frame["fb_velo_count"].sum())

    avg_fb_velo = _safe_avg(fb_velo_sum, fb_velo_count)
    fb_trend = None
    if avg_fb_velo is not None and season_fb_velo is not None:
        fb_trend = round(avg_fb_velo - season_fb_velo, 2)

    lhb_ab = float(frame["lhb_ab"].sum())
    rhb_ab = float(frame["rhb_ab"].sum())
    lhb_iso = _calc_iso(
        float(frame["lhb_singles"].sum()),
        float(frame["lhb_doubles"].sum()),
        float(frame["lhb_triples"].sum()),
        float(frame["lhb_hrs"].sum()),
        lhb_ab,
    )
    rhb_iso = _calc_iso(
        float(frame["rhb_singles"].sum()),
        float(frame["rhb_doubles"].sum()),
        float(frame["rhb_triples"].sum()),
        float(frame["rhb_hrs"].sum()),
        rhb_ab,
    )
    lhb_outs = float(frame["lhb_outs"].sum())
    rhb_outs = float(frame["rhb_outs"].sum())
    lhb_innings = lhb_outs / 3.0 if lhb_outs > 0 else 0.0
    rhb_innings = rhb_outs / 3.0 if rhb_outs > 0 else 0.0

    return {
        "player_id": player_id,
        "player_name": _fallback_name(player_name, "Pitcher", player_id),
        "team": team,
        "pitch_hand": pitch_hand if pitch_hand in {"L", "R"} else None,
        "stat_date": stat_date,
        "window_days": int(window_days),
        "hr_per_9": round((hrs * 9.0) / innings, 3) if innings > 0 else None,
        "hr_per_fb": _safe_pct(hrs, fly_balls),
        "fly_ball_pct": _safe_pct(fly_balls, batted),
        "hard_hit_pct_against": _safe_pct(hard_hit, batted),
        "barrel_pct_against": _safe_pct(barrels, batted),
        "avg_exit_velo_against": _safe_avg(ev_sum, batted),
        "avg_fastball_velo": avg_fb_velo,
        "fastball_velo_trend": fb_trend,
        "whiff_pct": _safe_pct(whiffs, swings),
        "chase_pct": _safe_pct(chase_swings, out_zone),
        "zone_pct": _safe_pct(in_zone, pitches),
        "innings_pitched": round(innings, 2),
        "pitches_per_start": round(pitches / games, 1) if games > 0 else None,
        "days_rest": days_rest,
        "era": None,
        "fip": None,
        "xfip": None,
        "xera": None,
        "hr_per_9_vs_lhb": round((float(frame["lhb_hrs"].sum()) * 9.0) / lhb_innings, 3)
        if lhb_innings > 0
        else None,
        "hr_per_9_vs_rhb": round((float(frame["rhb_hrs"].sum()) * 9.0) / rhb_innings, 3)
        if rhb_innings > 0
        else None,
        "iso_allowed_vs_lhb": lhb_iso,
        "iso_allowed_vs_rhb": rhb_iso,
    }


def _build_pitcher_rows(
    pitcher_daily_df: pd.DataFrame,
    starter_ids: set[int],
) -> list[dict[str, Any]]:
    """Build rolling-window pitcher rows for inferred starters only."""
    if pitcher_daily_df.empty or not starter_ids:
        return []

    pitcher_daily_df = pitcher_daily_df[pitcher_daily_df["player_id"].isin(starter_ids)].copy()
    if pitcher_daily_df.empty:
        return []

    pitcher_daily_df["game_date_dt"] = pd.to_datetime(pitcher_daily_df["game_date"], errors="coerce")
    pitcher_daily_df = pitcher_daily_df[pitcher_daily_df["game_date_dt"].notna()].copy()
    pitcher_daily_df.sort_values(["player_id", "game_date_dt"], inplace=True)

    rows: list[dict[str, Any]] = []
    player_ids = pitcher_daily_df["player_id"].drop_duplicates().tolist()
    total_players = len(player_ids)
    print(f"  Computing pitcher rolling windows for {total_players} starters...")

    for idx, player_id in enumerate(player_ids, start=1):
        player_df = pitcher_daily_df[pitcher_daily_df["player_id"] == player_id].copy()
        player_df.sort_values("game_date_dt", inplace=True)
        player_df.reset_index(drop=True, inplace=True)
        dates = player_df["game_date_dt"].tolist()

        total_fb_count = float(player_df["fb_velo_count"].sum())
        season_fb = (
            round(float(player_df["fb_velo_sum"].sum()) / total_fb_count, 2)
            if total_fb_count > 0
            else None
        )

        prev_start_date: pd.Timestamp | None = None
        for row_idx, current_date in enumerate(dates):
            current = player_df.iloc[row_idx]
            stat_date = current_date.strftime("%Y-%m-%d")
            team = current.get("team", "")
            player_name = current.get("player_name", "")
            pitch_hand = current.get("pitch_hand", "")
            days_rest = (
                int((current_date - prev_start_date).days)
                if prev_start_date is not None
                else None
            )

            for window in PITCHER_WINDOWS:
                if window == FULL_SEASON_WINDOW:
                    subset = player_df[player_df["game_date_dt"] <= current_date]
                else:
                    start = current_date - timedelta(days=window - 1)
                    subset = player_df[
                        (player_df["game_date_dt"] >= start) & (player_df["game_date_dt"] <= current_date)
                    ]
                computed = _compute_pitcher_window_row(
                    player_id=player_id,
                    player_name=player_name,
                    team=team,
                    pitch_hand=pitch_hand,
                    stat_date=stat_date,
                    window_days=window,
                    frame=subset,
                    season_fb_velo=season_fb,
                    days_rest=days_rest,
                )
                if computed:
                    rows.append(computed)

            prev_start_date = current_date

        if idx % 50 == 0 or idx == total_players:
            pct = (idx / total_players) * 100 if total_players else 100.0
            print(f"  Pitcher rolling progress: {pct:5.1f}% ({idx}/{total_players})")

    return rows


def _build_outcome_rows(
    outcomes: dict[tuple[int, int], dict[str, Any]],
    batter_name_map: dict[int, str],
) -> list[dict[str, Any]]:
    rows = []
    for (_, player_id), outcome in outcomes.items():
        player_name = outcome.get("player_name") or batter_name_map.get(player_id) or ""
        row = {
            "game_id": outcome["game_id"],
            "game_date": outcome["game_date"],
            "player_id": outcome["player_id"],
            "player_name": _fallback_name(player_name, "Player", player_id),
            "team": outcome.get("team"),
            "opponent": outcome.get("opponent"),
            "pa": int(outcome.get("pa", 0)),
            "hr_count": int(outcome.get("hr_count", 0)),
            "did_hit_hr": 1 if int(outcome.get("hr_count", 0)) > 0 else 0,
        }
        rows.append(row)
    return rows


def _load_season(season: int) -> dict[str, int]:
    """Load and persist one season from CSV."""
    csv_path = Path(DATA_DIR) / f"statcast_{season}.csv"
    if not csv_path.exists():
        print(f"\n⚠️  Season {season} skipped: missing file {csv_path}")
        return {"games": 0, "batter_stats": 0, "pitcher_stats": 0, "outcomes": 0}

    print("\n" + "=" * 72)
    print(f"📚 BACKFILL SEASON {season}")
    print("=" * 72)

    games_meta: dict[int, dict[str, Any]] = {}
    starter_workloads: dict[tuple[int, str, int], int] = defaultdict(int)
    batter_daily: dict[tuple[int, str], dict[str, Any]] = {}
    pitcher_daily: dict[tuple[int, str], dict[str, Any]] = {}
    outcomes: dict[tuple[int, int], dict[str, Any]] = {}
    batter_name_map: dict[int, str] = {}
    pitcher_name_map: dict[int, str] = {}
    pitcher_hand_map: dict[int, str] = {}

    file_size = csv_path.stat().st_size
    with open(csv_path, "r", encoding="utf-8", errors="ignore") as handle:
        reader = pd.read_csv(handle, chunksize=CHUNK_SIZE, low_memory=False)
        for chunk_idx, chunk in enumerate(reader, start=1):
            _process_chunk(
                chunk=chunk,
                games_meta=games_meta,
                starter_workloads=starter_workloads,
                batter_daily=batter_daily,
                pitcher_daily=pitcher_daily,
                outcomes=outcomes,
                batter_name_map=batter_name_map,
                pitcher_name_map=pitcher_name_map,
                pitcher_hand_map=pitcher_hand_map,
            )
            try:
                pct = min(100.0, (handle.tell() / file_size) * 100) if file_size else 100.0
            except (OSError, ValueError):
                pct = 0.0
            print(f"  Processing {season}... {pct:5.1f}% complete (chunk {chunk_idx})")

    print("  Finalizing game/starter extraction...")
    game_rows, assignments = _build_games_rows(
        games_meta=games_meta,
        starter_workloads=starter_workloads,
        pitcher_name_map=pitcher_name_map,
        pitcher_hand_map=pitcher_hand_map,
    )
    starter_ids = {
        pid
        for a in assignments.values()
        for pid in (a.get("home_pitcher_id"), a.get("away_pitcher_id"))
        if pid is not None
    }

    print("  Building rolling batter stats...")
    batter_daily_df = _dict_to_df(batter_daily, id_col="player_id", date_col="game_date")
    batter_rows = _build_batter_rows(batter_daily_df)

    print("  Building rolling pitcher stats...")
    pitcher_daily_df = _dict_to_df(pitcher_daily, id_col="player_id", date_col="game_date")
    pitcher_rows = _build_pitcher_rows(pitcher_daily_df, starter_ids)

    print("  Building batter-game HR outcomes...")
    outcome_rows = _build_outcome_rows(outcomes, batter_name_map)

    print("  Writing season rows to database...")
    games_saved = _batch_upsert("games", game_rows, ["game_id"])
    batter_saved = _batch_upsert("batter_stats", batter_rows, ["player_id", "stat_date", "window_days"])
    pitcher_saved = _batch_upsert("pitcher_stats", pitcher_rows, ["player_id", "stat_date", "window_days"])
    outcomes_saved = _batch_upsert("batter_game_outcomes", outcome_rows, ["game_id", "player_id"])

    print(
        "  ✅ Season summary: "
        f"games={len(game_rows)} ({games_saved} upserted), "
        f"batter_stats={len(batter_rows)} ({batter_saved} upserted), "
        f"pitcher_stats={len(pitcher_rows)} ({pitcher_saved} upserted), "
        f"outcomes={len(outcome_rows)} ({outcomes_saved} upserted)"
    )

    return {
        "games": len(game_rows),
        "batter_stats": len(batter_rows),
        "pitcher_stats": len(pitcher_rows),
        "outcomes": len(outcome_rows),
    }


def run_backfill(seasons: list[int] | None = None) -> dict[str, int]:
    """
    Run historical Statcast CSV backfill one season at a time.

    Expected files:
    - /pipeline/data/statcast_2023.csv
    - /pipeline/data/statcast_2024.csv
    - /pipeline/data/statcast_2025.csv
    """
    seasons = seasons or DEFAULT_SEASONS
    print("\n" + "=" * 72)
    print("🚀 HISTORICAL BACKFILL START")
    print("=" * 72)
    print(f"Seasons requested: {seasons}")
    print(f"Data directory: {DATA_DIR}")

    _ping_database()

    totals = {"games": 0, "batter_stats": 0, "pitcher_stats": 0, "outcomes": 0}
    for season in seasons:
        summary = _load_season(int(season))
        for key in totals:
            totals[key] += int(summary.get(key, 0))

    print("\n" + "=" * 72)
    print("✅ HISTORICAL BACKFILL COMPLETE")
    print("=" * 72)
    print(
        "Total rows prepared: "
        f"games={totals['games']}, "
        f"batter_stats={totals['batter_stats']}, "
        f"pitcher_stats={totals['pitcher_stats']}, "
        f"outcomes={totals['outcomes']}"
    )
    return totals

