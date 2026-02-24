# MLB HR Prop Model — Cursor Build Guide

Complete step-by-step prompts for Cursor to build, wire, and deploy the full project.

## HOW TO USE THIS GUIDE IN CURSOR

1. Open this file in Cursor so it has full context
2. Feed prompts ONE AT A TIME — copy-paste each prompt into Cursor chat
3. Wait for each to complete and verify before moving to the next
4. Tell Cursor: "I have a build guide in CURSOR_BUILD_GUIDE.md. Start with Phase 0, then I'll feed you prompts one by one."
5. If Cursor asks questions, answer them. If it goes off track, say "Stop. Re-read the prompt in CURSOR_BUILD_GUIDE.md for Phase X, Prompt Y."

---

## PHASE 0: PRE-FLIGHT

### 0A. Repo Structure
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

### 0B. Install Dependencies
Open terminal in Cursor:
```bash
cd pipeline
pip install pybaseball requests pandas numpy python-dotenv
cp .env.example .env
```

### 0C. Initialize & Test
```bash
python run_pipeline.py --init
python run_pipeline.py --test
```

✅ If --test shows games or says "no games" (offseason), pipeline works.
❌ If it errors, fix before continuing.

---

## PHASE 1: HISTORICAL DATA BACKFILL + MODEL

We need 3 seasons (2023-2025) of historical data to validate our model weights.
DO NOT use pybaseball for bulk pulls — it's too slow. Use Baseball Savant CSV downloads instead.

### Prompt 1A — Build Historical Data Loader

```
Create /pipeline/backfill.py — a module to load historical Statcast data from CSV files.

Context: Baseball Savant lets you download full-season Statcast data as CSV files.
The user will manually download these from:
https://baseballsavant.mlb.com/statcast_search
(Search → set date range to full season → download CSV)

We need 3 seasons: 2023, 2024, 2025.

The module should:

1. Look for CSV files in /pipeline/data/ directory named:
   - statcast_2023.csv
   - statcast_2024.csv
   - statcast_2025.csv

2. Load each CSV into pandas (these are large files, 700k-1M rows each)

3. For each season, compute per-batter aggregate stats in rolling windows:
   - For every batter who had at least 100 PA in that season
   - Calculate their stats at multiple window sizes: 7, 14, 30 days and full season
   - Stats to compute (same as fetchers/statcast.py):
     - barrel_pct, hard_hit_pct, avg_exit_velo, max_exit_velo
     - fly_ball_pct, hr_per_fb, pull_pct
     - avg_launch_angle, sweet_spot_pct
     - iso_power, slg, k_pct, bb_pct
     - Handedness splits: iso_vs_lhp, iso_vs_rhp
   - These should be computed as rolling windows for every game date in the season
     (so we can backtest what the model WOULD have said on any given day)

4. Save all computed stats to the batter_stats table in SQLite

5. Also extract game-level data and save to the games table:
   - game_id (game_pk from Statcast), date, home/away teams
   - home/away pitcher IDs and names

6. Also compute per-pitcher aggregate stats for every pitcher who started:
   - HR/9, HR/FB, fly_ball_pct allowed, hard_hit_pct allowed
   - barrel_pct_against, avg_exit_velo_against
   - avg_fastball_velo, whiff_pct, chase_pct
   - Handedness splits
   - Save to pitcher_stats table

7. Track actual HR outcomes per batter per game (did they hit a HR? how many?)
   Store in a new table or add to an existing one so we can measure model accuracy.

Main function: run_backfill(seasons=[2023, 2024, 2025])

This will take a few minutes per season — that's fine, it's a one-time operation.
Print progress as it runs: "Processing 2023... 42% complete" etc.

Add a --backfill flag to run_pipeline.py that calls this.

IMPORTANT: Process each season one at a time to manage memory.
Use chunked reading if CSVs are very large: pd.read_csv(path, chunksize=100000)
```

### Prompt 1B — Add Pitcher Stats Fetcher (for daily use)

```
I have an MLB HR prop data pipeline in /pipeline. Look at the existing code structure:
- config.py for settings
- db/schema.sql for the database schema (see pitcher_stats table)
- db/database.py for DB helpers
- fetchers/statcast.py for batter stats (use as reference pattern)

Create /pipeline/fetchers/pitchers.py that:

1. Uses pybaseball to fetch pitcher stats for rolling windows defined in config.py PITCHER_WINDOWS (14, 30 days)
2. For each pitcher who is a probable starter today, compute:
   - HR/9 and HR/FB ratio (how many HRs they give up)
   - Fly ball % allowed
   - Hard hit % allowed and barrel % allowed
   - Average exit velocity allowed
   - Fastball velocity + velocity trend vs season average
   - Whiff % (swinging strike rate) and chase rate
   - xFIP and xERA if available from Statcast
   - Handedness splits: HR/9 vs LHB and vs RHB, ISO allowed vs each
3. Follows the same pattern as statcast.py: fetch one Statcast pull for the max window, slice locally for smaller windows
4. Saves to the pitcher_stats table using upsert_many with conflict on (player_id, stat_date, window_days)
5. Has a main function fetch_daily_pitcher_stats(pitcher_ids: list[int]) that takes a list of pitcher MLB IDs (from the schedule fetcher)

Use pybaseball.statcast_pitcher() for individual pitcher data. Enable caching.
Keep pybaseball calls minimal — only fetch the 30-day window once per pitcher, then slice.
```

### Prompt 1C — Wire Pitcher Fetcher Into Daily Pipeline

```
In /pipeline/run_pipeline.py, Step 3 currently says "Pitcher module coming soon."

Update it to:
1. Import fetch_daily_pitcher_stats from fetchers.pitchers
2. Extract pitcher IDs from the games list (both home_pitcher_id and away_pitcher_id)
3. Filter out None/TBD pitchers
4. Call fetch_daily_pitcher_stats with the list of pitcher IDs
5. Print the count of pitcher stat rows saved

Follow the same error handling pattern as the other steps (try/except with error message).
```

### Prompt 1D — Build Model Scoring Engine

```
Create /pipeline/scoring.py — the HR model scoring engine.

This module takes all the data we've collected (batter stats, pitcher stats, weather, odds, park factors) and produces a final score + signal for each potential HR prop bet.

How it works:

1. For each game today, get the lineup (or fall back to likely starters based on recent games)
2. For each batter in each game:
   a. Pull their 14-day batter_stats from the DB
   b. Pull the opposing pitcher's 14-day pitcher_stats
   c. Pull the game's weather data
   d. Pull the stadium's park factor
   e. Pull the best available HR prop odds for this batter

3. Score each factor 0-100:
   - barrel_score: percentile rank of batter's 14-day barrel% vs all batters in DB
   - matchup_score: based on batter's ISO vs the pitcher's handedness. High ISO vs that hand = high score. Also factor in pitcher's HR/9 and HR/FB
   - park_weather_score: combine stadium hr_park_factor × weather wind_hr_impact. Coors + wind out = 100, Oracle Park + wind in = 10
   - pitcher_vuln_score: percentile rank of pitcher's HR/9, barrel% allowed, hard hit% allowed (higher = more vulnerable = higher score for the batter)
   - hot_cold_score: compare batter's 7-day ISO to their 30-day baseline. Hot streak = boost, cold streak = penalty

4. Weighted composite using HR_FACTOR_WEIGHTS from config.py

5. Compare model probability to book implied probability to get edge %

6. Assign signal based on SIGNAL_THRESHOLDS from config.py:
   - BET: score >= 75 AND edge >= 5%
   - LEAN: score >= 60 AND edge >= 3%
   - FADE: score <= 35 AND edge <= -3%
   - SKIP: everything else

7. Save all scores to hr_model_scores table

Main function: score_todays_props(game_date: str) -> list[dict]

Also add a --score flag to run_pipeline.py that calls this after --daily.

Use numpy for percentile calculations. Keep it simple — no ML yet, just weighted factors.
```

### Prompt 1E — Build Backtesting Engine

```
Create /pipeline/backtest.py — uses the historical data we loaded to test model accuracy.

This module:

1. Takes a date range (e.g., full 2025 season)
2. For each game date in that range:
   - Runs the scoring engine (scoring.py) using ONLY data available BEFORE that date
     (no lookahead bias — the model can only see rolling stats up to the day before)
   - Generates BET/LEAN/SKIP/FADE signals for every batter in every game
   - Checks against actual outcomes: did the batter actually hit a HR that game?

3. Computes accuracy metrics:
   - Overall hit rate: % of BET signals where the batter actually hit a HR
   - Hit rate by signal: BET vs LEAN vs SKIP vs FADE
   - ROI simulation: if you bet 1 unit on every BET signal at average book odds, what's the P&L?
   - Hit rate by score bucket: 90+ score vs 80-89 vs 70-79 etc.
   - Hit rate by individual factor: which factor is most predictive?
   - Calibration: does a 30% model probability actually hit ~30% of the time?

4. Outputs results as:
   - Print summary to console
   - Save detailed results to a CSV in /pipeline/data/backtest_results.csv
   - Show factor correlation analysis (which weights should be adjusted)

5. Optionally: run a simple grid search over factor weights to find optimal weights
   - Try different weight combinations for the 5 factors
   - Find the combination that maximizes ROI on historical data
   - Print recommended weights vs current weights

Main function: run_backtest(start_date: str, end_date: str)

Add a --backtest flag to run_pipeline.py with --start and --end date args.

IMPORTANT: This must avoid lookahead bias. On any given date, the model can only use
stats computed from PRIOR dates. The backfill module should have stored rolling stats
by date to make this possible.
```

---

## PHASE 2: DASHBOARD (React + Vite → Vercel)

The dashboard will be hosted on Vercel (free tier). It's a React app that talks to a
FastAPI backend running on Railway alongside the pipeline.

### Prompt 2A — Scaffold React Dashboard

```
Create a React dashboard in /dashboard using Vite + React + TypeScript.

Initialize with:
- Vite + React + TypeScript
- Tailwind CSS 3
- React Router for navigation
- Recharts for charts

The dashboard has 4 main views (tabs/routes):
1. /picks — Today's Picks (main view, default)
2. /performance — Model Performance & accuracy tracking
3. /tools — My model vs PropFinder vs BallparkPal vs HomeRunPredict
4. /bankroll — Bet tracking and P&L

Design direction:
- Dark theme: bg-gray-950 background, bg-gray-800/border-gray-700 cards
- Accent color: cyan-400 for highlights, green for BET, yellow for LEAN, red for FADE
- Fonts: "JetBrains Mono" (Google Fonts) for all numbers/data, "Plus Jakarta Sans" for text
- Clean, dense, data-forward — think Bloomberg terminal meets sports app
- Cards should have subtle hover states and transitions
- No generic AI-looking design — this should look like a tool built by a bettor for a bettor

Navigation: horizontal tab bar at the top, subtle active indicator.

Scaffold the project structure, routing, shared layout with nav bar, and placeholder pages.
We'll build each page in the next prompts.
```

### Prompt 2B — Build Today's Picks Page

```
Build the main "Today's Picks" page at /picks in the React dashboard.

Reference this data structure for what the API will return:
{
  player_name: "Aaron Judge",
  team: "NYY",
  opponent: "BOS",
  opposing_pitcher: "Brayan Bello",
  model_score: 87,
  signal: "BET",
  model_prob: 0.312,
  book_implied_prob: 0.238,
  edge: 0.074,
  barrel_score: 92,
  matchup_score: 78,
  park_weather_score: 85,
  pitcher_vuln_score: 71,
  hot_cold_score: 65,
  propfinder_signal: "BET",
  ballparkpal_signal: "LEAN",
  hrpredict_signal: "BET",
  consensus_agreement: 3,
  temperature_f: 78,
  wind_speed_mph: 12,
  wind_description: "out to CF (12mph)",
  wind_hr_impact: 1.15,
  best_odds: "+320",
  best_book: "DraftKings"
}

Page layout:
1. Top summary bar: total props scanned, BET signals count, LEAN count, avg edge on BETs, best pick of the day
2. Filter/sort bar: filter by signal (ALL/BET/LEAN/SKIP/FADE), sort by score or edge
3. Pick cards showing:
   - Player name, team vs opponent, opposing pitcher
   - Model score as circular progress ring (0-100)
   - Signal badge (color coded)
   - Edge % prominently displayed
   - Best available odds and which book
   - Book implied % vs model probability side by side
4. Expandable detail panel on each card:
   - 5 factor scores as horizontal progress bars (barrel, matchup, park/weather, pitcher vuln, hot/cold)
   - Weather info (temp, wind description, HR impact multiplier)
   - External tool consensus: PropFinder/BallparkPal/HomeRunPredict signals with colored badges
   - Consensus agreement indicator (e.g., "3/3 agree" badge)
   - Dropdown selects to manually input external tool signals (BET/LEAN/SKIP/FADE/not listed) with save button
   - "Log Bet" button (for BET and LEAN signals only)

Signal colors: BET=#22c55e, LEAN=#eab308, SKIP=#6b7280, FADE=#ef4444.
Use JetBrains Mono for all numbers.

Start with hardcoded mock data (8 picks across all signal types). We connect the API later.
```

### Prompt 2C — Build Performance, Bankroll & Tool Comparison Pages

```
Build three more pages for the /dashboard:

PAGE 1: Model Performance (/performance)
- Overall record card: W-L-P with win %, ROI %
- Win rate over time — line chart with rolling 7-day win rate (Recharts)
- Signal breakdown table: win rate for BET picks vs LEAN picks vs FADE picks
- Factor analysis: bar chart showing which factor score correlates most with actual HR outcomes
- Calibration chart: model probability buckets (10-20%, 20-30%, etc.) vs actual hit rate
- Calendar heatmap showing daily P&L (green for profit days, red for loss days)
- Date range selector to filter all charts

PAGE 2: Tool Comparison (/tools)
- Side-by-side accuracy table: My Model vs PropFinder vs BallparkPal vs HomeRunPredict
- Columns: total picks tracked, BET signal win %, overall win %, ROI %
- Line chart: cumulative ROI over time for each tool (Recharts)
- Agreement analysis: win rate when 2/3 tools agree, when 3/3 agree, when tools disagree
- "My model is N% better/worse than [tool]" summary callout

PAGE 3: Bankroll Tracker (/bankroll)
- Starting bankroll input at top
- Running balance line chart (Recharts)
- Daily P&L bar chart
- Key stats: total wagered, total profit, ROI %, max drawdown, longest win/loss streak
- Bet log table: date, player, odds, sportsbook, stake, units, result, profit — sortable
- Pending bets section with Win/Loss/Push settle buttons
- Unit size calculator: enter bankroll → recommended unit via Kelly criterion

All pages use mock data for now. Same dark theme, JetBrains Mono for numbers.
Use Recharts for all charts.
```

### Prompt 2D — Build FastAPI Backend + Connect Dashboard

```
Create a FastAPI backend that serves data from the SQLite database to the React dashboard.

Create /pipeline/api.py:

Endpoints:
  GET /api/picks?date=YYYY-MM-DD
    → Returns scored picks from hr_model_scores joined with weather and hr_odds
    → Default: today's date

  GET /api/performance?start=YYYY-MM-DD&end=YYYY-MM-DD
    → Returns win/loss stats, rolling win rate, factor correlations from bets + hr_model_scores

  GET /api/bankroll
    → Returns running bankroll, daily P&L, bet log from bets table

  GET /api/tools
    → Returns tool accuracy comparison from hr_model_scores where external signals are logged

  POST /api/picks/{id}/tools
    → Body: { propfinder: "BET", ballparkpal: "LEAN", hrpredict: "SKIP" }
    → Updates external tool signals on hr_model_scores row
    → Recalculates consensus_agreement

  POST /api/bets
    → Body: { game_id, player_id, player_name, sportsbook, odds, stake, units, model_score, model_edge }
    → Inserts into bets table with result='pending'

  PUT /api/bets/{id}/settle
    → Body: { result: "win" | "loss" | "push" }
    → Updates result, calculates payout and profit

  GET /api/status
    → Returns pipeline health: last run time, DB row counts, last successful fetch per source

Requirements:
- Add CORS middleware allowing the Vercel frontend domain + localhost:5173
- Use the same SQLite DB path from config.py
- Add uvicorn and fastapi to requirements.txt
- API runs with: uvicorn pipeline.api:app --host 0.0.0.0 --port $PORT

Then update the React dashboard:
1. Create /dashboard/src/api/client.ts with typed fetch functions for every endpoint
2. Use VITE_API_URL env var: const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
3. Create /dashboard/.env with VITE_API_URL=http://localhost:8000
4. Replace all mock data with real API calls using useEffect + useState
5. Add loading skeletons and error states to all pages
6. Manual tool signal dropdowns POST to /api/picks/{id}/tools
7. Bet logger POSTs to /api/bets
8. Settle buttons PUT to /api/bets/{id}/settle
```

---

## PHASE 3: DEPLOYMENT

### Prompt 3A — Railway Config (Pipeline + API Backend)

```
Create deployment configs for Railway in the /pipeline directory.

Railway hosts both:
- The FastAPI server (runs continuously, serves the dashboard)
- The daily pipeline cron job (runs at 10 AM ET / 15:00 UTC)

Create these files:

1. /pipeline/requirements.txt:
   pybaseball
   requests
   pandas
   numpy
   python-dotenv
   fastapi
   uvicorn[standard]

2. /pipeline/Dockerfile:
   - Python 3.11 slim base
   - Install requirements
   - Copy all pipeline code
   - Expose PORT env var
   - CMD: uvicorn api:app --host 0.0.0.0 --port $PORT

3. /pipeline/railway.toml:
   [build]
   builder = "DOCKERFILE"
   dockerfilePath = "./Dockerfile"

   [deploy]
   startCommand = "uvicorn api:app --host 0.0.0.0 --port ${PORT}"
   restartPolicyType = "ON_FAILURE"

4. Update /pipeline/.env.example with all env vars:
   ODDS_API_KEY=
   WEATHER_API_KEY=
   DISCORD_WEBHOOK_URL=
   PORT=8000

5. Document in a comment how to set up the cron:
   - In Railway dashboard, create a second service in the same project
   - Start command: python run_pipeline.py --daily --score
   - Schedule: 0 15 * * * (10 AM ET = 15:00 UTC during EDT)
   - This service runs the pipeline + scoring, writes to DB, then exits
   - The API service reads from the same DB continuously
```

### Prompt 3B — Vercel Config (Dashboard Frontend)

```
Create deployment configs for Vercel in the /dashboard directory.

Vercel hosts the React dashboard (free tier — unlimited deploys, fast CDN).

Create:

1. /dashboard/vercel.json:
   {
     "buildCommand": "npm run build",
     "outputDirectory": "dist",
     "framework": "vite",
     "rewrites": [
       { "source": "/(.*)", "destination": "/index.html" }
     ]
   }

   Rewrites needed for React Router client-side routing.

2. /dashboard/.env.example:
   VITE_API_URL=http://localhost:8000

3. Make sure /dashboard/.gitignore includes:
   node_modules/
   dist/
   .vercel/
   .env
   .env.local

4. /dashboard/src/api/client.ts uses:
   const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

5. Update root /README.md with full setup instructions:

   ## Local Development

   ### Backend (Pipeline + API)
   cd pipeline
   pip install -r requirements.txt
   cp .env.example .env
   python run_pipeline.py --init
   python run_pipeline.py --daily
   python run_pipeline.py --score
   uvicorn api:app --reload

   ### Frontend (Dashboard)
   cd dashboard
   npm install
   npm run dev

   ## Deployment

   ### Railway (Backend)
   1. Create project, connect GitHub repo, root dir: /pipeline
   2. Add env vars: ODDS_API_KEY, WEATHER_API_KEY, DISCORD_WEBHOOK_URL
   3. Deploy (auto-detects Dockerfile)
   4. Create cron service: python run_pipeline.py --daily --score @ 0 15 * * *

   ### Vercel (Frontend)
   1. Import repo, root dir: /dashboard, framework: Vite
   2. Add env var: VITE_API_URL = https://your-railway-app.up.railway.app
   3. Deploy
```

---

## PHASE 4: EXTRAS

### Prompt 4A — Discord Morning Alerts

```
Create /pipeline/alerts.py — Discord webhook for morning pick alerts.

After scoring completes, send a Discord embed:
- Title: "⚾ MLB HR Picks — [date]"
- Color: green if 3+ BET signals, yellow if 1-2, gray if 0
- Fields:
  - BET signals count
  - Top 3 picks: "Player (TEAM) vs Pitcher — Score: 87, Edge: +7.4%"
  - Weather flags: games with wind_hr_impact > 1.10 or < 0.90
  - Tool consensus: picks with 3/3 agreement
- Footer: link to dashboard

Uses DISCORD_WEBHOOK_URL from env. Gracefully skips if not configured.
Wire into run_pipeline.py after --score completes.
```

### Prompt 4B — Historical Odds Estimation (for backtesting)

```
For backtesting ROI, we need historical odds. The Odds API free tier doesn't have historical data.

In /pipeline/backfill.py, add estimate_historical_odds():

1. For each batter-game in historical data, estimate what book odds WOULD have been
2. Use a lookup: map season HR rate to typical American odds
   - ~2% HR rate ≈ +500
   - ~3% HR rate ≈ +400
   - ~4% HR rate ≈ +320
   - ~5% HR rate ≈ +250
   - ~6%+ HR rate ≈ +200
3. Adjust by park factor (Coors batters get shorter odds)
4. Save to hr_odds table with sportsbook='estimated'

This is an approximation but good enough for backtesting ROI calculations.
Run automatically during --backfill.
```

---

## HISTORICAL DATA DOWNLOAD INSTRUCTIONS

Before running Phase 1 backfill, download these CSV files:

### Baseball Savant Statcast Data (3 seasons)
1. Go to: https://baseballsavant.mlb.com/statcast_search
2. Set: Season 2023, Player Type: Batter, Min Results: 0
3. Search → Download CSV
4. Repeat for 2024, 2025
5. Save as:
   - /pipeline/data/statcast_2023.csv
   - /pipeline/data/statcast_2024.csv
   - /pipeline/data/statcast_2025.csv

NOTE: Each file is ~100-200MB. Baseball Savant may limit to 40k rows per download.
If so, download in monthly chunks and combine, or use this Python script:

```python
from pybaseball import statcast
for year in [2023, 2024, 2025]:
    print(f"Downloading {year}...")
    df = statcast(start_dt=f"{year}-03-30", end_dt=f"{year}-11-05")
    df.to_csv(f"pipeline/data/statcast_{year}.csv", index=False)
    print(f"  Saved {len(df)} rows")
```
This takes 10-20 min per season. Run overnight if needed.

---

## TESTING CHECKLIST

### Before Opening Day (March 27, 2026):

Pipeline:
- [ ] python run_pipeline.py --init creates DB
- [ ] Historical CSVs downloaded to /pipeline/data/
- [ ] python run_pipeline.py --backfill loads 3 seasons
- [ ] python run_pipeline.py --backtest --start 2025-03-27 --end 2025-09-28
- [ ] Backtest shows BET signal hit rate above book implied probability
- [ ] Factor weights adjusted based on backtest results
- [ ] python run_pipeline.py --daily fetches spring training data
- [ ] python run_pipeline.py --score generates picks
- [ ] API starts, /api/picks returns data

Dashboard:
- [ ] Loads on localhost:5173
- [ ] Picks page shows scored props
- [ ] Performance page shows backtest results
- [ ] Manual tool input saves
- [ ] Bet logger works
- [ ] Bankroll tracker shows P&L

Deployment:
- [ ] Railway deploys API + cron
- [ ] Vercel deploys dashboard
- [ ] Dashboard reaches API across domains
- [ ] Discord alerts fire after cron
- [ ] Cron runs at 10 AM ET daily

### First 2 Weeks (March 27 - April 10):
- [ ] Paper trade only — log picks, don't bet real money
- [ ] Compare model vs PropFinder/BallparkPal/HomeRunPredict
- [ ] Track which factors need weight adjustment
- [ ] Adjust signal thresholds if too many/few BET signals

### Go Live (April 10+):
- [ ] Start small (0.5x normal units)
- [ ] Scale after 2 profitable weeks
- [ ] Review weekly, adjust weights monthly

---

## TECH STACK SUMMARY

| Component | Technology | Host | Cost |
|-----------|-----------|------|------|
| Data Pipeline | Python + pybaseball | Railway (cron) | $0 (within plan) |
| API Server | FastAPI + uvicorn | Railway (service) | $0 (within plan) |
| Database | SQLite | Railway (persistent) | $0 |
| Dashboard | React + Vite + Tailwind | Vercel (free tier) | $0 |
| Statcast Data | pybaseball / CSV | Free | $0 |
| Game Schedule | MLB Stats API | Free, no key | $0 |
| Odds | The Odds API | Free tier (500/mo) | $0 |
| Weather | OpenWeatherMap | Free tier (1000/day) | $0 |
| Alerts | Discord Webhook | Free | $0 |
| Code | GitHub | Free | $0 |
| **Total** | | | **$0/month** |

(Railway $20-25/mo already paid for newsletter — this rides on the same plan)
