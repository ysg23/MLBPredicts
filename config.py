"""
MLB HR Prop Pipeline - Configuration
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"          # for CSV caches and backfill files
DATA_DIR.mkdir(exist_ok=True)

# ── Supabase ───────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")  # backend writes
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")        # frontend reads

# ── API Keys ───────────────────────────────────────────────
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# ── API Endpoints ──────────────────────────────────────────
MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
WEATHER_API_BASE = "https://api.openweathermap.org/data/2.5"

# ── Statcast Settings ──────────────────────────────────────
BATTER_WINDOWS = [7, 14, 30]          # rolling windows in days
PITCHER_WINDOWS = [14, 30]             # last 2-3 starts, last month

HISTORICAL_SEASONS = [2023, 2024, 2025]
CURRENT_SEASON = 2026

# ── HR Model Factor Weights (starting point — backtest will tune) ──
HR_FACTOR_WEIGHTS = {
    "barrel_score":       0.25,   # barrel % is king for HR prediction
    "matchup_score":      0.20,   # batter vs pitcher handedness + ISO split
    "park_weather_score":  0.25,   # park factor × wind × temp composite
    "pitcher_vuln_score":  0.20,   # pitcher's HR/9, HR/FB, hard hit allowed
    "hot_cold_score":      0.10,   # recent wRC+ trend (regression-prone)
}

# ── Signal Thresholds ──────────────────────────────────────
SIGNAL_THRESHOLDS = {
    "BET":  {"min_score": 75, "min_edge": 0.05},
    "LEAN": {"min_score": 60, "min_edge": 0.03},
    "FADE": {"max_score": 35, "max_edge": -0.03},
}

# ── Bankroll Settings ──────────────────────────────────────
STARTING_BANKROLL = 500.0
UNIT_SIZE = 10.0
MAX_UNITS_PER_BET = 3
MAX_DAILY_EXPOSURE = 10

# ── Weather HR Impact Multipliers ──────────────────────────
WIND_OUT_MULTIPLIER = 1.15
WIND_IN_MULTIPLIER = 0.85
WIND_CROSS_MULTIPLIER = 1.02
TEMP_HOT_THRESHOLD = 75
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

ABBR_TO_FULL = {v: k for k, v in TEAM_ABBRS.items()}
