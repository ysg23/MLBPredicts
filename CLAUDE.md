# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

MLB decision-support system for multi-market betting workflows. Backend pipeline (Python) deployed on Railway + Postgres. Frontend dashboard (static HTML/JS) deployed on Vercel. All Python work lives under `pipeline/`.

## Commands

All commands run from `pipeline/`:

```bash
# One-time setup
python run_pipeline.py --init        # create DB schema + load stadium refs
python db/migrate.py                 # run additive Postgres migrations

# Daily operations
python run_pipeline.py --daily --date 2026-03-27
python refresh_odds.py --date 2026-03-27
python fetch_lineups.py --date 2026-03-27
python build_features.py --date 2026-03-27
python score_markets.py --date 2026-03-27 --all-markets --send-alerts
python rescore_on_lineup.py --date 2026-03-27 --send-alerts
python grade_results.py --date 2026-03-27

# Historical backfill (bulk mode is default — fetches Statcast once per chunk)
python backfill_historical.py --start-date 2023-03-30 --end-date 2025-10-01 --build-features --score --all-markets --grade
# --no-bulk: revert to per-day fetch (slower, lower memory)
# --workers N: thread pool size for Phase 2 features/score/grade (default 4)

# Backtesting
python backtest.py --market HR --start-date 2025-04-01 --end-date 2025-09-30 --signals BET,LEAN

# Status check
python run_pipeline.py --status
```

### Running tests

```bash
cd pipeline
python -m pytest tests/
python -m pytest tests/test_alerts_and_tiering.py   # single file
```

### Dashboard (local)

```bash
cd dashboard
python -m http.server 4173
```

## Architecture

### Data flow (per game date)

```
Schedule/umpires (MLB Stats API)
        ↓
Statcast pitch data (pybaseball → Baseball Savant)
        ↓
batter_stats + pitcher_stats  (rolling 7/14/30-day windows)
        ↓
build_features → batter_daily_features, pitcher_daily_features,
                 team_daily_features, game_context_features
        ↓
score_markets → model_scores  (signal: BET / LEAN / FADE / SKIP)
        ↓
grade_results → market_outcomes  (settlement + CLV capture)
```

### Key architectural decisions

**Database**: Postgres-first (Railway/Supabase), sqlite fallback for local dev. `db/database.py` wraps both with a `DBConnection` that transparently converts `?` placeholders to `%s` for Postgres. Never use raw sqlite-style queries when the code may run against Postgres.

**Statcast fetching — backfill vs daily**: `fetch_daily_batter_stats()` and `fetch_daily_pitcher_stats()` (in `fetchers/`) are for the daily pipeline (one day at a time). For backfill, use `fetch_statcast_bulk()` + `compute_batter_stats_for_date()` + `compute_pitcher_stats_from_df()` — these fetch the full range once and slice in memory, avoiding thousands of redundant API calls.

**Scoring engine**: `score_markets.py` dynamically imports a per-market module from `scoring/` (e.g. `scoring.hr_model`). Each model implements a common interface via `scoring/base_engine.py`. Market metadata (required feature tables, signal thresholds, entity type) lives in `scoring/market_specs.py` as `MarketSpec` dataclasses — this is the authoritative registry.

**Feature tables**: Four tables feed scoring: `batter_daily_features`, `pitcher_daily_features`, `team_daily_features`, `game_context_features`. Each has a builder in `features/`. `build_features.py` orchestrates all four for a given date.

**Signals**: BET / LEAN / FADE / SKIP. Thresholds vary by market — see `DEFAULT_THRESHOLDS`, `CONSERVATIVE_THRESHOLDS`, `AGGRESSIVE_THRESHOLDS` in `scoring/market_specs.py`.

**Railway deployment**: `api.py` is the always-on FastAPI service (`GET /health`, `GET /status`). Start command: `sh -c "uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}"`. All pipeline jobs (fetching, scoring, grading) run as separate one-off or scheduled commands, not inside the API process. Root directory for Railway is `pipeline/`.

**Backfill phases**: `backfill_historical.py` runs in two phases — Phase 1 (sequential) fetches schedule + slices Statcast per day; Phase 2 (parallel, ThreadPoolExecutor) runs features/score/grade. Statcast pre-fetch uses 60-day chunks to balance memory and API calls.

## Environment Variables

```env
# DB — use direct Postgres connection string (postgresql://...), NOT a project API URL
SUPABASE_DB_URL=postgresql://...   # or DATABASE_URL / POSTGRES_URL / PGHOST+PGPORT+...

# Fetchers
ODDS_API_KEY=...
WEATHER_API_KEY=...

# Alerts (optional)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
ALERT_THRESHOLDS_JSON={"*":{"signals":["BET","LEAN"],"min_score":70,"max_rows":5}}
```

## Supported Markets

`HR`, `K`, `HITS_1P`, `HITS_LINE`, `TB_LINE`, `OUTS_RECORDED`, `ML`, `TOTAL`, `F5_ML`, `F5_TOTAL`, `TEAM_TOTAL`

Default `--all-markets` bundle (used in daily scoring): `HR`, `K`, `HITS_1P`, `HITS_LINE`, `TB_LINE`, `OUTS_RECORDED`.

## Known Gaps

- Dashboard is a static prototype — no live Supabase read/write wiring yet.
- `model_scores.visibility_tier` (`FREE`/`PRO`) is schema-only; billing/auth not implemented.
- Historical odds are not guaranteed via `refresh_odds.py`; weather fetcher is real-time only.
- `Log Bet` button in dashboard is a UI placeholder.
