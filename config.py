"""
MLB HR Prop Pipeline - Configuration
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "db" / "mlb_hr.db"
DATA_DIR = BASE_DIR / "data"          # for CSV caches
DATA_DIR.mkdir(exist_ok=True)

# ── API Keys ───────────────────────────────────────────────
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "")

# ── API Endpoints ──────────────────────────────────────────
MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
WEATHER_API_BASE = "https://api.openweathermap.org/data/2.5"

# ── Statcast Settings ──────────────────────────────────────
# Rolling windows for batter stats (days)
BATTER_WINDOWS = [7, 14, 30]          # 7-day hot streak, 14-day trend, 30-day baseline
PITCHER_WINDOWS = [14, 30]             # last 2-3 starts, last month

# Season for historical backfill
HISTORICAL_SEASONS = [2024, 2025]      # 2 seasons is plenty for initial training
CURRENT_SEASON = 2026

# ── HR Model Factor Weights (starting point — will tune) ──
# These are initial weights. The model will learn better ones
# from backtesting, but you need a starting point.
HR_FACTOR_WEIGHTS = {
    "barrel_score":       0.25,   # barrel % is king for HR prediction
    "matchup_score":      0.20,   # batter vs pitcher handedness + ISO split
    "park_weather_score":  0.25,   # park factor × wind × temp composite
    "pitcher_vuln_score":  0.20,   # pitcher's HR/9, HR/FB, hard hit allowed
    "hot_cold_score":      0.10,   # recent wRC+ trend (regression-prone, lower weight)
}

# ── Signal Thresholds ──────────────────────────────────────
SIGNAL_THRESHOLDS = {
    "BET":  {"min_score": 75, "min_edge": 0.05},   # score ≥ 75 AND edge ≥ 5%
    "LEAN": {"min_score": 60, "min_edge": 0.03},   # score 60-74 AND edge 3-5%
    "FADE": {"max_score": 35, "max_edge": -0.03},  # negative edge, book overpriced
    # Everything else = SKIP
}

# ── Bankroll Settings ──────────────────────────────────────
STARTING_BANKROLL = 500.0              # adjust to your actual bankroll
UNIT_SIZE = 10.0                       # base unit ($10)
MAX_UNITS_PER_BET = 3                  # max 3 units on any single bet
MAX_DAILY_EXPOSURE = 10                # max 10 units in play per day

# ── Weather HR Impact Multipliers ──────────────────────────
# Wind blowing out at 10+ mph = more HRs, blowing in = fewer
# Temperature above 75°F = more carry, below 55°F = dead ball
WIND_OUT_MULTIPLIER = 1.15             # wind blowing out 10+ mph
WIND_IN_MULTIPLIER = 0.85              # wind blowing in 10+ mph
WIND_CROSS_MULTIPLIER = 1.02           # crosswind, minor effect
TEMP_HOT_THRESHOLD = 75                # degrees F
TEMP_COLD_THRESHOLD = 55
TEMP_HOT_MULTIPLIER = 1.08
TEMP_COLD_MULTIPLIER = 0.92

# ── Team Abbreviation Mapping ─────────────────────────────
TEAM_ABBRS = {
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC", "Chicago White Sox": "CHW",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET",
    "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Oakland Athletics": "OAK",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD", "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB", "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
}

# Reverse lookup
ABBR_TO_FULL = {v: k for k, v in TEAM_ABBRS.items()}
