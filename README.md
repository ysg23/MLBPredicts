# MLBPredicts Monorepo (Phase 6 Backend Complete)

Backend-first MLB decision-support system for multi-market betting workflows (HR, K, Hits, Total Bases, Outs, ML, Totals, F5, Team Totals).

## Current Scope

This repository currently contains:

- `pipeline/` (active backend)
  - data fetchers (schedule, Statcast, weather, odds, lineups, umpires)
  - feature store builders (`batter_daily_features`, `pitcher_daily_features`, `team_daily_features`, `game_context_features`)
  - market scoring engine + market models
  - grading, settlement, CLV capture
  - no-lookahead backtesting
- `dashboard/` (placeholder; frontend work is deferred to a later phase)

No frontend product/UI is implemented yet in this pass.

---

## Monorepo Layout

```text
.
├── pipeline/
│   ├── db/
│   │   ├── schema.sql                  # canonical PostgreSQL/Supabase schema
│   │   ├── schema_sqlite.sql           # sqlite fallback schema (local-only)
│   │   ├── migrations/
│   │   │   └── 001_phase_1a_foundation.sql
│   │   ├── database.py                 # Postgres-first DB access layer
│   │   └── migrate.py                  # SQL migration runner
│   ├── fetchers/
│   ├── features/
│   ├── scoring/
│   ├── grading/
│   ├── utils/
│   ├── run_pipeline.py
│   ├── build_features.py
│   ├── score_markets.py
│   ├── grade_results.py
│   ├── backtest.py
│   ├── clv.py
│   └── requirements.txt
├── dashboard/                          # reserved for future frontend phase
├── CURSOR_BUILD_GUIDE.md
└── README.md
```

---

## Database Strategy (Production vs Local)

### Production (primary): Supabase PostgreSQL

- Canonical schema: `pipeline/db/schema.sql`
- Primary DB URL env vars supported by backend:
  - `SUPABASE_DB_URL` (preferred)
  - `DATABASE_URL`
  - `SUPABASE_DATABASE_URL`

When one of those URLs is present, backend DB code uses Postgres automatically.

### Local fallback: sqlite

- Fallback schema: `pipeline/db/schema_sqlite.sql`
- Local DB file: `pipeline/db/mlb_hr.db`
- Used only when no Postgres URL env var is set.

---

## Backend Setup (Local / Cursor)

```bash
cd pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env` in `pipeline/` (or project root) with at least:

```env
# Preferred for production-like local runs:
SUPABASE_DB_URL=postgresql://...

# Data fetchers
ODDS_API_KEY=...
WEATHER_API_KEY=...
```

---

## Core Runtime Commands

From `pipeline/`:

```bash
# Initialize schema + static stadium data
python3 run_pipeline.py --init

# Pull daily upstream data (schedule/statcast/weather/odds)
python3 run_pipeline.py --daily --date 2026-03-27

# Refresh odds snapshots (scheduling-friendly standalone job)
python3 refresh_odds.py --date 2026-03-27

# Build daily feature snapshots
python3 build_features.py --date 2026-03-27

# Score one market
python3 score_markets.py --date 2026-03-27 --market HR

# Score default market set
python3 score_markets.py --date 2026-03-27 --all-markets

# Grade outcomes + settle bets + update CLV
python3 grade_results.py --date 2026-03-27

# Backtest (no-lookahead odds matching)
python3 backtest.py --market HR --start-date 2025-04-01 --end-date 2025-09-30 --signals BET,LEAN

# Optional: run additive SQL migrations
python3 db/migrate.py
```

---

## Deployment Plan (Current)

- **Supabase**: primary PostgreSQL datastore
- **Railway**: scheduled/triggered backend jobs (`run_pipeline.py`, `build_features.py`, `score_markets.py`, `grade_results.py`)
- **Vercel**: reserved for future `dashboard/` phase

### Suggested Railway job split

- early AM: `run_pipeline.py --daily --date <today>`
- after odds refresh: `build_features.py --date <today>`
- pre-lock windows: `score_markets.py --date <today> --all-markets`
- post-game/final: `grade_results.py --date <today>`

---

## Phase Boundary

This repo is stabilized through backend Phase 6 (scoring, grading, CLV, backtesting).  
Dashboard/UI, alerting, and productized frontend flows are intentionally deferred to later phases.
