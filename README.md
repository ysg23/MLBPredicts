# MLBPredicts Monorepo (Phase 10B baseline)

Backend-first MLB decision-support system for multi-market betting workflows with a data-dense dashboard prototype.

## Current Scope

This repository currently contains:

- `pipeline/` (active backend)
  - data fetchers (schedule, Statcast, weather, odds, lineups, umpires)
  - feature store builders (`batter_daily_features`, `pitcher_daily_features`, `team_daily_features`, `game_context_features`)
  - market scoring engine + market models
  - grading, settlement, CLV capture
  - no-lookahead backtesting
  - alerts/tiering/deployment scaffolding
- `dashboard/` (Phase 8A/8B/8C static prototype)
  - market explorer + filters + details
  - performance / model health / CLV / bankroll views
  - UX additions (exposure/correlation flags, saved filters, alerts panel, freshness indicators)

---

## Monorepo Layout

```text
.
├── pipeline/
│   ├── api.py                         # FastAPI health/status service for Railway
│   ├── alerts.py                      # Discord-first market alerts
│   ├── db/
│   │   ├── schema.sql                 # canonical PostgreSQL/Supabase schema
│   │   ├── schema_sqlite.sql          # sqlite fallback schema (local-only)
│   │   ├── migrations/
│   │   │   ├── 001_phase_1a_foundation.sql
│   │   │   └── 002_phase_9b_visibility_tier.sql
│   │   ├── database.py                # Postgres-first DB access layer
│   │   └── migrate.py                 # SQL migration runner
│   ├── refresh_odds.py
│   ├── fetch_lineups.py
│   ├── build_features.py
│   ├── score_markets.py
│   ├── rescore_on_lineup.py
│   ├── grade_results.py
│   ├── run_pipeline.py
│   ├── backfill_historical.py         # date-range historical ingest/backfill
│   ├── Dockerfile
│   ├── railway.toml
│   └── requirements.txt
├── dashboard/
│   ├── index.html                     # static dashboard prototype
│   ├── vercel.json                    # SPA rewrite/static config
│   └── .env.example
└── README.md
```

---

## Local Setup

### Backend

```bash
cd pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env` in `pipeline/` (or project root):

```env
# Database (prefer Postgres/Supabase)
SUPABASE_DB_URL=postgresql://postgres:<url-encoded-password>@db.<project-ref>.supabase.co:5432/postgres
# or DATABASE_URL / SUPABASE_DATABASE_URL
# NOTE: This is NOT the Supabase Project URL and NOT the publishable key.

# Data fetchers
ODDS_API_KEY=...
WEATHER_API_KEY=...

# Alerts (optional)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
ALERT_THRESHOLDS_JSON={"*":{"signals":["BET","LEAN"],"min_score":70,"max_rows":5}}
```

### Supabase env sanity check

- `SUPABASE_DB_URL` / `DATABASE_URL` = **Direct connection string** from Supabase Database settings.
- Do **not** set `SUPABASE_DB_URL` to `https://<project-ref>.supabase.co` (that is API URL for frontend).
- Do **not** set it to publishable/anon key.
- If your DB password has special chars (e.g. `@`, `:`, `/`, `#`), URL-encode them in the connection string.

### Railway-hosted Postgres/Supabase stack

If you deployed Supabase/Postgres inside Railway (IPv4-friendly), set one of these in MLBPredicts service:

- Preferred: `DATABASE_URL` or `POSTGRES_URL` provided by Railway Postgres service.
- Also supported: `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` (the app can assemble a DSN from these).

Tip: remove old external `SUPABASE_DB_URL` values if they still point to `db.<project-ref>.supabase.co` and are unreachable from Railway.

### Dashboard

```bash
cd dashboard
python3 -m http.server 4173
# open http://localhost:4173
```

---

## Schema / Migration Steps

From `pipeline/`:

```bash
# initialize schema and static refs
python3 run_pipeline.py --init

# run additive Postgres migrations
python3 db/migrate.py
```

Tiering extension point introduced:
- `model_scores.visibility_tier` (`FREE` / `PRO`) via schema + migration.

---

## Common CLI Commands

From `pipeline/`:

```bash
# health/status
python3 run_pipeline.py --status
python3 api.py  # (for local import check)

# data jobs
python3 run_pipeline.py --daily --date 2026-03-27
python3 refresh_odds.py --date 2026-03-27
python3 fetch_lineups.py --date 2026-03-27
python3 build_features.py --date 2026-03-27

# scoring/rescoring
python3 score_markets.py --date 2026-03-27 --all-markets --send-alerts
python3 rescore_on_lineup.py --date 2026-03-27 --send-alerts

# grading / clv
python3 grade_results.py --date 2026-03-27

# backtesting
python3 backtest.py --market HR --start-date 2025-04-01 --end-date 2025-09-30 --signals BET,LEAN

# historical (multi-year) backfill
python3 backfill_historical.py --start-date 2023-03-30 --end-date 2025-10-01 --build-features --score --all-markets --grade
```

---


## 3-Year Historical Backfill Plan

If you want this system to become useful quickly, seed three seasons first (schedule + Statcast + scoring + outcomes).

Recommended run order from `pipeline/`:

```bash
python3 run_pipeline.py --init
python3 db/migrate.py
python3 backfill_historical.py --start-date 2023-03-30 --end-date 2025-10-01 --build-features --score --all-markets --grade
```

Notes:
- This command loops each date and runs schedule, umpires, batter stats, pitcher stats, then optional features/scoring/grading.
- `--lineups` is optional for historical seeds (older games may not have lineup payloads).
- Historical bookmaker odds are provider-dependent and are **not** guaranteed by `refresh_odds.py`; backfill first, then layer archival odds separately if needed for full CLV history.
- Weather fetcher is real-time oriented; historical weather should be treated as missing unless you add a historical-weather provider.

---

## Railway API Deploy

For Railway API deployment:

- Set service **Root Directory** to `pipeline/`.
- API service starts with uvicorn bound to Railway dynamic port (`$PORT`, fallback `8000`) via shell expansion.
- Railway healthchecks should target `GET /health` (lightweight endpoint, no DB/API dependency).
- Keep CLI workflows (`refresh_odds.py`, `build_features.py`, `score_markets.py`, `grade_results.py`, `backfill_historical.py`) as one-off or scheduled job commands, separate from the always-on API process.

### Railway Healthcheck Troubleshooting

If deploy is unhealthy, check:

- `api.py` exists in `pipeline/` and exports `app` for `uvicorn api:app`.
- `GET /health` exists and returns HTTP 200 JSON.
- API process binds to Railway `$PORT` via shell expansion (literal `${PORT}` passed to uvicorn will fail).

---

## Job Scheduling Overview (Railway)

Use separate jobs/services instead of one monolith run.

- API service:
  - `sh -c "uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}"`
- Odds refresh:
  - `python refresh_odds.py --date <today>`
- Features:
  - `python build_features.py --date <today>`
- Scoring:
  - `python score_markets.py --all-markets --date <today> --send-alerts`
- Grading:
  - `python grade_results.py --date <today>`
- Lineup polling/rescore:
  - `python fetch_lineups.py --date <today> && python rescore_on_lineup.py --date <today> --send-alerts`

---

## Supported Markets

- `HR`
- `K`
- `HITS_1P`
- `HITS_LINE`
- `TB_LINE`
- `OUTS_RECORDED`
- `ML`
- `TOTAL`
- `F5_ML`
- `F5_TOTAL`
- `TEAM_TOTAL`

---

## Known Limitations / TODOs

- Dashboard is static prototype (no authenticated Supabase read/write wiring yet).
- `Log Bet` button is currently a UI placeholder hook.
- Alerts are Discord-first and depend on `DISCORD_WEBHOOK_URL`.
- Proxy-restricted environments may block external APIs (MLB/odds/weather).
- Billing is **not** implemented yet; tiering is schema/config extension-point only.
- Future integration points:
  - Supabase Auth for user/session-level visibility checks
  - Stripe for paid tiers / entitlements
  - API endpoints for dashboard live data and mutations
