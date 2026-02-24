# MLBPredicts — MLB Multi-Market Bet Assist Engine

A fast, modular MLB betting decision-support platform built for **multi-market analysis** (HR, Ks, Hits, Total Bases, ML, Totals, F5, Team Totals, and more).

This project is being built as a **backend-first system** with a shared feature store, market-agnostic scoring pipeline, and operational refresh logic (lineups, umpires, weather, odds) designed for real-world betting workflows.

---

## Project Goal

Build a **sellable, scalable MLB bet assist app** that helps users answer:

- What are the best betting opportunities today?
- Why does the model like them?
- How strong is the edge vs the market?
- What changed (lineup, umpire, weather, odds move)?
- How has the model performed over time (ROI / CLV / by market)?

This is a **decision-support platform**, not a blind picks feed.

---

## Current Status

### ✅ Implemented (Foundation)
- Multi-market database schema foundation
- Generic market tables for:
  - `market_odds`
  - `market_outcomes`
  - `model_scores`
  - `bets`
- `score_runs` audit trail table
- `lineups` table
- Feature-store tables:
  - `batter_daily_features`
  - `pitcher_daily_features`
  - `team_daily_features`
  - `game_context_features`
- Odds normalization utility (The Odds API → normalized `market_odds`)
- Early pipeline/database scaffolding and migrations

### 🔄 In Progress
- Feature builders / orchestration hardening
- Lineups + umpire fetchers
- Re-scoring triggers on lineup changes
- Market scoring modules
- Grading + CLV workflows

### 🧱 Planned
- Dashboard (`/dashboard`) for Vercel deployment
- Market explorer UI
- CLV/performance analytics views
- Alerts / trigger-based notifications
- Subscription-ready product workflows

---

## Architecture

### Stack
- **Cursor** — implementation workflow / code generation
- **GitHub** — source control
- **Supabase (Postgres)** — primary database
- **Railway** — pipeline jobs / cron / optional API trigger service
- **Vercel** — frontend dashboard hosting (later phase)

### Data Sources
- **Statcast / pybaseball** — batted-ball + player-level event data
- **MLB Stats API** — schedule, probable pitchers, lineups, game state, umpires
- **OpenWeather API** — weather conditions / updates
- **The Odds API** — player props + game markets (coverage dependent)
- **FanGraphs Park Factors** — seasonal park environment context

---

## Repository Structure (Clean Monorepo Layout)

> Backend is standardized under `/pipeline`.  
> Frontend will live in `/dashboard` (created in later phase).

```text
.
├── pipeline/
│   ├── db/
│   │   ├── schema.sql
│   │   ├── database.py
│   │   ├── migrate.py
│   │   └── migrations/
│   ├── fetchers/
│   ├── features/
│   ├── scoring/
│   ├── grading/
│   ├── utils/
│   ├── run_pipeline.py
│   ├── build_features.py
│   ├── score_markets.py
│   ├── grade_results.py
│   ├── refresh_odds.py
│   ├── fetch_lineups.py
│   ├── rescore_on_lineup.py
│   └── .env.example
├── dashboard/                 # planned / added in later phase
├── CURSOR_BUILD_GUIDE.md      # source of truth for build phases
├── README.md
└── .gitignore
