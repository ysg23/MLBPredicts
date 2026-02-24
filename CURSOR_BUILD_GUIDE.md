# MLB HR Prop Model — Cursor Build Guide

Complete step-by-step prompts for Cursor to build, wire, and deploy the full project.
Run these in order. Wait for each to complete before moving to the next.

---

## PHASE 0: PRE-FLIGHT (do this manually first)

### 0A. Repo Structure
Make sure your GitHub repo looks like this after uploading the pipeline files:

```
mlb-hr-model/
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
├── dashboard/           ← (empty for now, Cursor will scaffold)
├── .gitignore
└── README.md
```

### 0B. Local Test
Open terminal in Cursor, run:
```bash
cd pipeline
pip install pybaseball requests pandas numpy python-dotenv
cp .env.example .env
python run_pipeline.py --init
python run_pipeline.py --test
```

✅ If --test shows games or says "no games" (offseason), your pipeline works.
❌ If it errors, fix before continuing.

---

## PHASE 1: PIPELINE HARDENING

### Prompt 1A — Add Pitcher Stats Fetcher

```
I have an MLB HR prop data pipeline in /pipeline. Look at the existing code structure:
- config.py for settings
- db/schema.sql for the database schema (see pitcher_stats table)
- db/database.py for DB helpers
- fetchers/statcast.py for batter stats (use as reference pattern)

Create /pipeline/fetchers/pitchers.py that:

1. Uses pybaseball to fetch pitcher stats for the same rolling windows defined in config.py PITCHER_WINDOWS (14, 30 days)
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

### Prompt 1B — Wire Pitcher Fetcher Into Pipeline

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

### Prompt 1C — Add Model Scoring Engine

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
   - hot_cold_score: compare batter's 7-day wRC+ or ISO to their 30-day baseline. Hot streak = boost, cold streak = penalty

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

---

## PHASE 2: DASHBOARD

### Prompt 2A — Scaffold React Dashboard

```
Create a React dashboard in /dashboard using Vite + React + Tailwind CSS.

Initialize with:
- Vite + React + TypeScript
- Tailwind CSS 3
- React Router for navigation

The dashboard has 4 main views:
1. Today's Picks — the main view showing scored HR props
2. Model Performance — historical accuracy tracking
3. Tool Comparison — my model vs PropFinder vs BallparkPal vs HomeRunPredict
4. Bankroll — bet tracking and P&L

Use a dark theme (gray-950 background, gray-800 cards, cyan-400 accents).
Use the font "JetBrains Mono" for numbers/data and "Plus Jakarta Sans" for text.

Scaffold the project structure and routing, create placeholder pages.
We'll fill in the actual components next.
```

### Prompt 2B — Build Today's Picks Page

```
I have a React dashboard in /dashboard. Build the main "Today's Picks" page.

Reference this mock data structure for what the API will return:
{
  player_name: "Aaron Judge",
  team: "NYY",
  opponent: "BOS",
  opposing_pitcher: "Brayan Bello",
  model_score: 87,
  signal: "BET",        // BET | LEAN | SKIP | FADE
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
  wind_hr_impact: 1.15
}

The page should have:
1. Summary bar at top: total props scanned, BET count, LEAN count, avg edge on BETs
2. Filter bar: filter by signal (ALL, BET, LEAN, SKIP, FADE), sort by score or edge
3. Pick cards that show:
   - Player name, team vs opponent, opposing pitcher
   - Model score as a circular progress indicator (0-100)
   - Signal badge (color coded: green=BET, yellow=LEAN, gray=SKIP, red=FADE)
   - Edge percentage prominently displayed
   - Book implied vs model probability comparison
4. Expandable section on each card showing:
   - All 5 factor scores as horizontal bars (barrel, matchup, park/weather, pitcher vuln, hot/cold)
   - Weather info (temp, wind description, HR impact multiplier)
   - Tool consensus: icons for PropFinder, BallparkPal, HomeRunPredict showing their signal
   - Consensus count (e.g., "3/3 agree" in green, "1/3" in yellow)

Dark theme: bg-gray-950, cards bg-gray-800/border-gray-700, cyan-400 accents.
Use JetBrains Mono for all numbers. Signal colors: BET=#22c55e, LEAN=#eab308, SKIP=#6b7280, FADE=#ef4444.

Start with hardcoded mock data (array of 6-8 picks across all signal types). We'll connect the API later.
```

### Prompt 2C — Build Performance & Bankroll Pages

```
Build two more pages for the /dashboard:

PAGE 1: Model Performance (/performance)
- Win rate over time (line chart, rolling 7-day window)
- Total record: W-L with win %
- ROI % (cumulative)
- Breakdown by signal type: win rate for BET picks vs LEAN picks
- Breakdown by factor: which factor score correlates most with wins
- Calendar heatmap showing daily P&L (green = profit day, red = loss day)
- Use Recharts for all charts

PAGE 2: Bankroll Tracker (/bankroll)  
- Starting bankroll input
- Running balance line chart
- Daily P&L bar chart
- Bet log table: date, player, odds, stake, result, profit
- Summary stats: total wagered, total profit, ROI, longest win streak, longest losing streak, max drawdown
- Unit size calculator: shows recommended bet size based on Kelly criterion using model edge and odds

Both pages should use mock data for now. Same dark theme.
Use Recharts for charts. Keep the design consistent with the picks page.
```

### Prompt 2D — Build API Layer

```
The dashboard needs to read data from the Python pipeline's SQLite database.

Create a lightweight API layer. Two options — pick the simpler one:

Option A: Python FastAPI backend (preferred since our pipeline is already Python)
- Create /pipeline/api.py using FastAPI
- Endpoints:
  GET /api/picks?date=YYYY-MM-DD — returns today's scored picks from hr_model_scores joined with weather
  GET /api/performance — returns betting performance stats from bets table
  GET /api/bankroll — returns running bankroll from bets table
  GET /api/tools — returns tool accuracy comparison
  GET /api/status — returns pipeline health (last run time, DB row counts)
- Reads from the same SQLite DB the pipeline writes to
- Add CORS middleware for the Vercel frontend to call it
- Add a Procfile or start command for Railway: `uvicorn pipeline.api:app --host 0.0.0.0 --port $PORT`

Then update the React dashboard to:
- Create a /dashboard/src/api.ts module with fetch functions for each endpoint
- Use the API_URL from an environment variable (VITE_API_URL)
- Add loading states and error handling to all pages
- Replace mock data with real API calls using useEffect + useState

For local dev, the API runs on localhost:8000 and the dashboard on localhost:5173.
```

---

## PHASE 3: DEPLOYMENT

### Prompt 3A — Railway Config (Backend)

```
Create deployment configs for Railway in the /pipeline directory:

1. Create /pipeline/requirements.txt with all Python dependencies:
   - pybaseball
   - requests  
   - pandas
   - numpy
   - python-dotenv
   - fastapi
   - uvicorn[standard]

2. Create /pipeline/Dockerfile:
   - Python 3.11 slim base
   - Install requirements
   - Copy pipeline code
   - Expose port from $PORT env var
   - CMD runs uvicorn for the API server

3. Create /pipeline/railway.toml or document the Railway cron setup:
   - The API server runs continuously (uvicorn)
   - The daily pipeline runs as a cron job at 10:00 AM ET (15:00 UTC): python run_pipeline.py --daily
   - Document how to set this up in Railway (either as a separate service or using Railway's cron feature)

4. Create /pipeline/.env.example listing all required env vars:
   - ODDS_API_KEY
   - WEATHER_API_KEY
   - PORT (Railway sets this automatically)

Make sure the Dockerfile works for both the API server and the cron job.
```

### Prompt 3B — Vercel Config (Frontend)

```
Create deployment configs for Vercel in the /dashboard directory:

1. Create /dashboard/vercel.json:
   - Build command: npm run build
   - Output directory: dist
   - Framework: vite
   - Environment variable: VITE_API_URL (points to Railway backend URL)

2. Update /dashboard/.env.example:
   - VITE_API_URL=http://localhost:8000 (for local dev)

3. Update /dashboard/src/api.ts to use VITE_API_URL:
   - const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

4. Add a /dashboard/.gitignore if not present (node_modules, dist, .env)

5. Update the root README.md with:
   - Project overview
   - Local dev setup instructions (run pipeline + dashboard)
   - Deployment instructions for Railway and Vercel
   - Environment variables needed for each platform
```

---

## PHASE 4: POLISH & EXTRAS

### Prompt 4A — Add Manual Tool Input

```
Add a feature to the dashboard's Today's Picks page where I can manually log what PropFinder, BallparkPal, and HomeRunPredict say about each pick.

For each pick card, add an "External Tools" section with:
- Three dropdown selects (one per tool): BET / LEAN / SKIP / FADE / (not listed)
- A save button that POST's to a new API endpoint: POST /api/picks/:id/tools
- The endpoint updates propfinder_signal, ballparkpal_signal, hrpredict_signal in hr_model_scores
- After saving, the consensus_agreement count recalculates and the card updates

This is how I'll benchmark my model against the paid tools over time.
Keep the UX minimal — small dropdowns inline on the expanded card view.
```

### Prompt 4B — Add Discord Alerts

```
Add a Discord webhook integration to the pipeline.

Create /pipeline/alerts.py:
1. After scoring completes (at the end of run_pipeline.py --daily), send a Discord message with:
   - How many BET signals today
   - Top 3 picks by model score with: player name, opponent, pitcher, score, edge%, signal
   - Weather flags (any games with wind_hr_impact > 1.10 or < 0.90)
   - Link to the dashboard

2. Use a Discord webhook URL from env var DISCORD_WEBHOOK_URL
3. Format as a Discord embed with color coding (green border for BET-heavy days)
4. Keep it concise — this is a morning alert I check on my phone

Add DISCORD_WEBHOOK_URL to .env.example.
```

### Prompt 4C — Add Bet Logger

```
Add a quick bet logging feature to the dashboard.

On each pick card (BET and LEAN signals only), add a "Log Bet" button that opens a small modal:
- Sportsbook dropdown (DraftKings, FanDuel, BetMGM, bet365, Other)
- Odds (pre-filled from the best available odds in the data)
- Stake in dollars
- Units (auto-calculated from stake / unit_size in config)
- Submit button

POST /api/bets — inserts into the bets table with status='pending'

Also add a way to settle bets:
- On the Bankroll page, show pending bets
- Each pending bet has Win / Loss / Push buttons
- Clicking settles the bet and updates profit calculation

This is how we track real performance over time.
```

---

## DEPLOYMENT CHECKLIST

After all phases complete:

### Railway (Backend)
1. Create new project in Railway
2. Connect to your GitHub repo
3. Set root directory to `/pipeline`
4. Add environment variables: ODDS_API_KEY, WEATHER_API_KEY, DISCORD_WEBHOOK_URL
5. Deploy — should auto-detect Dockerfile
6. Set up cron job for daily pipeline run (10 AM ET)
7. Note the public URL (you'll need it for Vercel)

### Vercel (Frontend)
1. Import GitHub repo in Vercel
2. Set root directory to `/dashboard`
3. Set framework to Vite
4. Add environment variable: VITE_API_URL = https://your-railway-url.up.railway.app
5. Deploy

### GitHub
1. Make sure .env files are in .gitignore
2. Set up branch protection on main if you want
3. Both Railway and Vercel auto-deploy on push to main

---

## TESTING CHECKLIST

Before going live for Opening Day:

- [ ] `python run_pipeline.py --init` creates DB with all tables
- [ ] `python run_pipeline.py --test` pulls MLB schedule
- [ ] `python run_pipeline.py --daily` fetches all data sources
- [ ] `python run_pipeline.py --score` generates model scores
- [ ] API starts and /api/picks returns data
- [ ] Dashboard loads and displays picks
- [ ] Manual tool input saves correctly
- [ ] Bet logger creates and settles bets
- [ ] Discord alert fires after daily pipeline
- [ ] Railway cron runs on schedule
- [ ] Vercel dashboard is accessible

---

## NOTES FOR CURSOR

- All Python code uses the existing patterns in /pipeline (config.py for settings, db/database.py for DB helpers)
- The SQLite DB schema is already defined in db/schema.sql — don't create new tables, use what's there
- Factor weights in config.py are starting points — we'll tune them after collecting 2-3 weeks of data
- pybaseball caching is enabled — repeated calls for the same data won't re-fetch
- Keep pybaseball calls to minimum: one pull per window, slice locally
- The dashboard should work with mock data first, then swap to real API calls
- Dark theme everywhere: bg-gray-950, cards bg-gray-800, cyan-400 accents, JetBrains Mono for numbers
