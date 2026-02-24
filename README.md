# MLB HR Prop Data Pipeline

Automated data pipeline for home run prop analysis. Pulls player stats, pitcher matchups, park factors, weather, and odds into a local SQLite database for daily model scoring.

## Setup

```bash
pip install pybaseball requests pandas numpy python-dotenv
```

## Configuration

Copy `.env.example` to `.env` and add your API keys:
```
ODDS_API_KEY=your_key_here    # Free tier: https://the-odds-api.com
WEATHER_API_KEY=your_key_here  # Free: https://openweathermap.org
```

## Usage

```bash
# One-time: Initialize database + load historical data (last 2 seasons)
python run_pipeline.py --init

# Daily: Pull today's matchups, stats, weather, and odds
python run_pipeline.py --daily

# Check what's in the database
python run_pipeline.py --status
```

## Architecture

```
run_pipeline.py          → Main entry point
config.py                → Settings, API keys, constants
db/
  schema.sql             → SQLite table definitions
  database.py            → DB connection + helpers
fetchers/
  statcast.py            → Barrel %, exit velo, HR/FB via pybaseball
  pitchers.py            → Pitcher HR/9, FB%, handedness splits
  schedule.py            → Today's games + lineups via MLB Stats API
  weather.py             → Wind speed/dir, temp by stadium
  odds.py                → HR prop odds via The Odds API
  park_factors.py        → Park HR factors (static + wind-adjusted)
  umpires.py             → Home plate ump assignments
utils/
  stadiums.py            → Stadium coords + dimensions
  calculations.py        → Rolling averages, z-scores, etc.
```

## Data Refresh Schedule

| Source | Frequency | Method |
|--------|-----------|--------|
| Statcast (barrel %, EV) | Daily overnight | pybaseball (14-day window) |
| Pitcher splits | Daily overnight | pybaseball |
| Game schedule + lineups | Daily 10AM ET | MLB Stats API |
| Weather | Daily 10AM ET + 2PM update | OpenWeather API |
| Odds | Every 30 min on game days | The Odds API |
| Park factors | Seasonal (static load) | FanGraphs CSV |
| Umpire assignments | Daily 10AM ET | MLB Stats API |
