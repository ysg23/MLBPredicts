# MLB HR Prop Model — Cursor Build Guide

Complete step-by-step prompts for Cursor to build, wire, and deploy the full project.

## STACK
- **Cursor** → builds everything (pipeline code + React dashboard)
- **Supabase** → shared Postgres database (free tier, 500MB)
- **Railway** → hosts Python pipeline cron + FastAPI server ($25 plan, already paid)
- **Vercel** → hosts React dashboard (free tier)
- **GitHub** → all code

## HOW TO USE THIS GUIDE IN CURSOR

1. Open this file in Cursor so it has full context
2. Feed prompts ONE AT A TIME — copy-paste each prompt into Cursor chat
3. Wait for each to complete and verify before moving to the next
4. Tell Cursor: "Read through the full project structure in /pipeline and understand the existing code. Then open CURSOR_BUILD_GUIDE.md — this is our build plan. Start with Phase 0B. Show me the output before moving on. After that, I'll tell you which prompt to run next."
5. If Cursor goes off track, say "Stop. Re-read the prompt in CURSOR_BUILD_GUIDE.md for Phase X, Prompt Y."

---

## PHASE 0: PRE-FLIGHT

### 0A. Supabase Setup (do this manually first)
1. Go to https://supabase.com → New Project
2. Name it "mlb-hr-model", choose a region close to you, set a DB password
3. Once created, go to **Settings → API** and copy:
   - Project URL → `SUPABASE_URL`
   - `anon` public key → `SUPABASE_ANON_KEY`
   - `service_role` secret key → `SUPABASE_SERVICE_KEY`
4. Go to **SQL Editor → New Query**
5. Paste the entire contents of `/pipeline/db/schema.sql` and click **Run**
6. Verify tables were created: go to **Table Editor** — you should see all tables

### 0B. Repo Structure
Your GitHub repo should look like this:

```
MLBPredicts/
├── pipeline/
│   ├── run_pipeline.py
│   ├── config.py
│   ├── .env.example
│   ├── db/
│   │   ├── __init__.py
│   │   ├── schema.sql
│   │   └── database.py
│   ├── fetchers/
│   │   ├── __init__.py
│   │   ├── statcast.py
│   │   ├── schedule.py
│   │   ├── weather.py
│   │   └── odds.py
│   └── utils/
│       ├── __init__.py
│       └── stadiums.py
├── dashboard/              ← empty for now, Cursor scaffolds later
├── .gitignore
├── CURSOR_BUILD_GUIDE.md   ← this file
└── README.md
```

### 0C. Install Dependencies & Configure
Open terminal in Cursor:
```bash
cd pipeline
pip install pybaseball requests pandas numpy python-dotenv supabase fastapi uvicorn[standard]
cp .env.example .env
```
Edit `.env` and paste your Supabase URL + keys from step 0A.

### 0D. Initialize & Test
```bash
python run_pipeline.py --init
python run_pipeline.py --test
```

✅ --init should say "Connected to Supabase" and "All tables exist"
✅ --test should show games or say "no games" (offseason)
❌ If it errors, fix before continuing

---

## PHASE 1: HISTORICAL DATA BACKFILL + MODEL

We need 3 seasons (2023-2025) of historical data to validate our model weights.
DO NOT use pybaseball for bulk pulls — it's too slow. Use Baseball Savant CSV downloads.

### Prompt 1A — Build Historical Data Loader

```
Create /pipeline/backfill.py — a module to load historical Statcast data from CSV files into our Supabase database.

Context: Baseball Savant lets you download full-season Statcast data as CSV files.
The user will download these and place them in /pipeline/data/ as:
- statcast_2023.csv, statcast_2024.csv, statcast_2025.csv

Look at the existing code:
- config.py for settings and HISTORICAL_SEASONS
- db/database.py for Supabase helpers (upsert_many, insert_many)
- fetchers/statcast.py for the stat computation logic (use compute_batter_hr_stats as reference)
- db/schema.sql for all table schemas including hr_outcomes

The module should:

1. Load each season CSV into pandas (700k-1M rows each, process one season at a time)

2. For each season, compute per-batter rolling window stats:
   - For every batter with at least 100 PA that season
   - Windows: 7, 14, 30 days (same as config.BATTER_WINDOWS)
   - Stats: barrel_pct, hard_hit_pct, avg_exit_velo, fly_ball_pct, hr_per_fb, pull_pct, avg_launch_angle, sweet_spot_pct, iso_power, slg, k_pct, bb_pct, iso_vs_lhp, iso_vs_rhp
   - Compute these as rolling windows for every game date in the season (so we can backtest without lookahead bias)

3. Save batter stats to batter_stats table via upsert_many (batch in chunks of 500 rows to avoid Supabase timeouts)

4. Extract game-level data → games table

5. Compute per-pitcher rolling stats for starting pitchers → pitcher_stats table

6. Track actual HR outcomes per batter per game → hr_outcomes table (did they hit a HR? how many?)

7. Estimate historical book odds → hr_odds table with sportsbook='estimated':
   - Map batter's season HR rate to typical American odds:
     ~2% ≈ +500, ~3% ≈ +400, ~4% ≈ +320, ~5% ≈ +250, ~6%+ ≈ +200
   - Adjust by park factor

Main function: run_backfill(seasons=[2023, 2024, 2025])
Print progress: "Processing 2023... 42% complete"
Add --backfill flag to run_pipeline.py

IMPORTANT: 
- Process one season at a time to manage memory
- Batch Supabase inserts in chunks of 500 rows
- Use chunked CSV reading if needed: pd.read_csv(path, chunksize=100000)
```

### Prompt 1B — Add Pitcher Stats Fetcher (for daily use)

```
Create /pipeline/fetchers/pitchers.py for daily pitcher stat fetching.

Look at:
- config.py for PITCHER_WINDOWS (14, 30 days)
- db/database.py for Supabase helpers
- fetchers/statcast.py as the reference pattern
- db/schema.sql for pitcher_stats table schema

Create a module that:
1. Uses pybaseball.statcast_pitcher() for each probable starter today
2. Computes: HR/9, HR/FB, fly_ball_pct allowed, hard_hit_pct allowed, barrel_pct_against, avg_exit_velo_against, avg_fastball_velo, fastball_velo_trend, whiff_pct, chase_pct, xFIP, xERA, handedness splits
3. One pybaseball call per pitcher for 30-day window, slice locally for 14-day
4. Saves to pitcher_stats via upsert_many

Main function: fetch_daily_pitcher_stats(pitcher_ids: list[int])
Enable pybaseball caching. Keep calls minimal.
```

### Prompt 1C — Wire Pitcher Fetcher Into Daily Pipeline

```
In /pipeline/run_pipeline.py, Step 3 currently says "Pitcher module coming soon."

Update it to:
1. Import fetch_daily_pitcher_stats from fetchers.pitchers
2. Extract pitcher IDs from games list (home_pitcher_id + away_pitcher_id)
3. Filter out None/TBD
4. Call fetch_daily_pitcher_stats with the pitcher IDs
5. Print count saved. Same error handling pattern as other steps.
```

### Prompt 1D — Build Model Scoring Engine

```
Create /pipeline/scoring.py — the HR model scoring engine.

Look at config.py for HR_FACTOR_WEIGHTS and SIGNAL_THRESHOLDS.
Look at db/database.py for how to query Supabase.

For each batter in each game today:
1. Pull their 14-day batter_stats from Supabase
2. Pull opposing pitcher's 14-day pitcher_stats
3. Pull game weather data
4. Pull stadium park factor
5. Pull best available HR prop odds

Score each factor 0-100:
- barrel_score: percentile rank of 14-day barrel% vs all batters
- matchup_score: batter ISO vs pitcher handedness + pitcher HR/9
- park_weather_score: park factor × wind_hr_impact × temp impact
- pitcher_vuln_score: percentile of pitcher HR/9, barrel% allowed
- hot_cold_score: 7-day ISO vs 30-day baseline

Weighted composite via HR_FACTOR_WEIGHTS. Compare model prob to book implied prob for edge.

Signals:
- BET: score >= 75 AND edge >= 5%
- LEAN: score >= 60 AND edge >= 3%
- FADE: score <= 35 AND edge <= -3%
- SKIP: everything else

Save to hr_model_scores table. Add --score flag to run_pipeline.py.
Use numpy for percentile calcs. No ML — just weighted factors.
```

### Prompt 1E — Build Backtesting Engine

```
Create /pipeline/backtest.py — tests model accuracy on historical data.

Uses hr_outcomes table to check if model predictions were correct.

For a date range (e.g., 2025 full season):
1. For each game date, run scoring engine using ONLY data before that date (no lookahead)
2. Generate signals for every batter
3. Check against hr_outcomes: did they actually hit a HR?

Compute:
- Hit rate by signal type (BET vs LEAN vs SKIP vs FADE)
- ROI simulation: 1 unit on every BET signal at estimated book odds
- Hit rate by score bucket (90+, 80-89, 70-79, etc.)
- Factor correlation: which individual factor predicts HRs best
- Calibration: 30% model prob → does it hit ~30%?
- Optional grid search over factor weights to find optimal combo

Output: console summary + CSV to /pipeline/data/backtest_results.csv
Add --backtest flag with --start and --end date args to run_pipeline.py

CRITICAL: No lookahead bias. Only use stats from prior dates.
Query Supabase with date filters to enforce this.
```

---

## PHASE 2: DASHBOARD (React + Vite → Vercel)

### Prompt 2A — Scaffold React Dashboard

```
Create a React dashboard in /dashboard using Vite + React + TypeScript.

Initialize with: Vite, React, TypeScript, Tailwind CSS 3, React Router, Recharts

4 routes:
1. /picks — Today's Picks (default)
2. /performance — Model accuracy tracking
3. /tools — My model vs PropFinder/BallparkPal/HomeRunPredict
4. /bankroll — Bet tracking and P&L

Design:
- Dark theme: bg-gray-950, cards bg-gray-800/border-gray-700
- Accents: cyan-400 highlights, green BET, yellow LEAN, red FADE
- Fonts: "JetBrains Mono" for numbers, "Plus Jakarta Sans" for text (Google Fonts)
- Bloomberg terminal meets sports app aesthetic — dense, data-forward
- No generic AI design. This should look like a tool built by a bettor.

Scaffold project structure, routing, shared layout with tab nav, placeholder pages.

For Supabase integration: install @supabase/supabase-js
Create /dashboard/src/lib/supabase.ts:
  import { createClient } from '@supabase/supabase-js'
  export const supabase = createClient(
    import.meta.env.VITE_SUPABASE_URL,
    import.meta.env.VITE_SUPABASE_ANON_KEY
  )

The dashboard reads directly from Supabase using the anon key (RLS policies allow public reads).
Create /dashboard/.env with VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY.
```

### Prompt 2B — Build Today's Picks Page

```
Build the main "Today's Picks" page at /picks.

Data comes from Supabase: query hr_model_scores joined with weather, filtered by today's date.

Page layout:
1. Summary bar: props scanned, BET count, LEAN count, avg edge on BETs
2. Filter/sort: by signal (ALL/BET/LEAN/SKIP/FADE), sort by score or edge
3. Pick cards:
   - Player, team vs opponent, opposing pitcher
   - Model score as circular progress ring
   - Signal badge (BET=#22c55e, LEAN=#eab308, SKIP=#6b7280, FADE=#ef4444)
   - Edge %, best odds + which book, model prob vs book implied
4. Expandable detail:
   - 5 factor bars (barrel, matchup, park/weather, pitcher vuln, hot/cold)
   - Weather (temp, wind, HR impact multiplier)
   - Tool consensus: PropFinder/BallparkPal/HomeRunPredict signal badges
   - Dropdown selects to manually set tool signals → updates hr_model_scores in Supabase
   - "Log Bet" button (BET/LEAN only) → inserts into bets table

Use Supabase client for all reads and writes.
JetBrains Mono for numbers. Start with mock data, then wire to Supabase.
```

### Prompt 2C — Build Performance, Bankroll & Tool Comparison Pages

```
Build three more pages:

/performance:
- W-L record, win %, ROI %
- Rolling 7-day win rate line chart (Recharts)
- Signal breakdown: win rate per signal type
- Factor analysis: which factor correlates with HR outcomes
- Calibration: model probability buckets vs actual hit rate
- Calendar heatmap: daily P&L
- Data from: hr_model_scores + hr_outcomes + bets tables in Supabase

/tools:
- Accuracy table: My Model vs PropFinder vs BallparkPal vs HomeRunPredict
- Cumulative ROI line chart per tool
- Agreement analysis: win rate when 2/3 agree, 3/3 agree
- Data from: hr_model_scores + hr_outcomes

/bankroll:
- Starting bankroll input
- Running balance + daily P&L charts (Recharts)
- Stats: total wagered, profit, ROI, max drawdown, streaks
- Bet log table (sortable): date, player, odds, book, stake, result, profit
- Pending bets with Win/Loss/Push settle buttons → updates bets table in Supabase
- Kelly criterion unit calculator
- Data from: bets table

All read/write via Supabase client. Same dark theme. Mock data first, then wire.
```

### Prompt 2D — Build FastAPI Backend for Pipeline

```
Create /pipeline/api.py — a lightweight FastAPI server for the pipeline.

This serves two purposes:
1. Health check endpoint for Railway monitoring
2. Pipeline trigger endpoint (optional, for manual runs)

Endpoints:
  GET /api/status → DB row counts, last pipeline run time
  POST /api/trigger → manually trigger a pipeline run (protected with a simple API key)

Note: The dashboard reads/writes directly to Supabase, so we do NOT need API endpoints
for picks, bets, performance etc. The FastAPI server is mainly for Railway to have
a running process + health check.

Add CORS middleware. Run with: uvicorn api:app --host 0.0.0.0 --port $PORT
```

---

## PHASE 3: DEPLOYMENT

### Prompt 3A — Railway Config (Pipeline + API)

```
Create deployment configs for Railway:

1. /pipeline/requirements.txt:
   pybaseball, requests, pandas, numpy, python-dotenv, supabase, fastapi, uvicorn[standard]

2. /pipeline/Dockerfile:
   Python 3.11 slim, install requirements, copy code, CMD uvicorn

3. /pipeline/railway.toml:
   [build]
   builder = "DOCKERFILE"
   [deploy]
   startCommand = "uvicorn api:app --host 0.0.0.0 --port ${PORT}"

4. Document cron setup:
   - Second Railway service, same project
   - Command: python run_pipeline.py --daily --score
   - Cron: 0 15 * * * (10 AM ET)
   - Shares same Supabase connection

Environment vars needed on Railway:
SUPABASE_URL, SUPABASE_SERVICE_KEY, ODDS_API_KEY, WEATHER_API_KEY, DISCORD_WEBHOOK_URL
```

### Prompt 3B — Vercel Config (Dashboard)

```
Create Vercel deployment configs:

1. /dashboard/vercel.json:
   buildCommand: npm run build, outputDirectory: dist, framework: vite
   rewrites: [{ source: "/(.*)", destination: "/index.html" }]

2. /dashboard/.env.example:
   VITE_SUPABASE_URL=https://your-project.supabase.co
   VITE_SUPABASE_ANON_KEY=your-anon-key

3. /dashboard/.gitignore: node_modules/, dist/, .vercel/, .env

4. Update root README.md with full local dev + deployment instructions
```

---

## PHASE 4: EXTRAS

### Prompt 4A — Discord Morning Alerts

```
Create /pipeline/alerts.py — Discord webhook for morning alerts.

After --daily --score completes, send embed:
- Title: "⚾ MLB HR Picks — [date]"
- BET count, top 3 picks with score + edge
- Weather flags (wind_hr_impact > 1.10 or < 0.90)
- Tool consensus count
- Dashboard link

Uses DISCORD_WEBHOOK_URL. Skips gracefully if not set.
Wire into run_pipeline.py after scoring.
```

---

## HISTORICAL DATA DOWNLOAD

Before Phase 1 backfill, download 3 seasons of Statcast CSVs:

### Option A: Baseball Savant Web (may limit to 40k rows per download)
1. https://baseballsavant.mlb.com/statcast_search
2. Season: 2023, Player Type: Batter → Search → Download CSV
3. Repeat for 2024, 2025 (may need monthly chunks)
4. Save to: /pipeline/data/statcast_YYYY.csv

### Option B: pybaseball Script (slower but no row limits)
```python
from pybaseball import statcast
for year in [2023, 2024, 2025]:
    df = statcast(start_dt=f"{year}-03-30", end_dt=f"{year}-11-05")
    df.to_csv(f"pipeline/data/statcast_{year}.csv", index=False)
```
Takes 10-20 min per season. Run overnight.

---

## SUPABASE SETUP CHECKLIST

1. [ ] Create project at supabase.com
2. [ ] Run schema.sql in SQL Editor (creates all tables + views + RLS policies)
3. [ ] Copy URL + anon key + service key
4. [ ] Add to /pipeline/.env (service key for writes)
5. [ ] Add to /dashboard/.env (anon key for reads)
6. [ ] Verify: python run_pipeline.py --init shows "All tables exist"

---

## TESTING CHECKLIST

### Before Opening Day (March 27, 2026):
- [ ] Supabase tables created and connected
- [ ] Historical CSVs downloaded to /pipeline/data/
- [ ] python run_pipeline.py --backfill loads 3 seasons
- [ ] python run_pipeline.py --backtest --start 2025-03-27 --end 2025-09-28
- [ ] Factor weights tuned from backtest
- [ ] python run_pipeline.py --daily + --score works
- [ ] Dashboard loads, reads from Supabase
- [ ] Manual tool input + bet logger works
- [ ] Railway + Vercel deployed
- [ ] Discord alerts fire

### Paper Trade (March 27 - April 10):
- [ ] Log picks daily, don't bet real money
- [ ] Compare model vs paid tools
- [ ] Adjust thresholds and weights

### Go Live (April 10+):
- [ ] Start at 0.5x units, scale after 2 profitable weeks

---

## TECH STACK

| Component | Technology | Host | Cost |
|-----------|-----------|------|------|
| Pipeline | Python + pybaseball | Railway (cron) | $0 (within $25 plan) |
| API Health | FastAPI + uvicorn | Railway (service) | $0 (same plan) |
| Database | Postgres | Supabase (free, 500MB) | $0 |
| Dashboard | React + Vite + Tailwind | Vercel (free tier) | $0 |
| Statcast | pybaseball / CSV | Free | $0 |
| Schedule | MLB Stats API | Free, no key | $0 |
| Odds | The Odds API | Free (500/mo) | $0 |
| Weather | OpenWeatherMap | Free (1000/day) | $0 |
| Alerts | Discord Webhook | Free | $0 |
| **Total** | | | **$0 additional** |
