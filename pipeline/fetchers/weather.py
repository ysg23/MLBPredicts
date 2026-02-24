"""
Weather fetcher for game-day conditions.

Pulls temperature, wind speed/direction, and calculates
HR impact multiplier based on wind relative to stadium orientation.

Uses OpenWeatherMap free tier (1000 calls/day).
"""
import requests
import math
from datetime import datetime

from config import (WEATHER_API_BASE, WEATHER_API_KEY,
                    WIND_OUT_MULTIPLIER, WIND_IN_MULTIPLIER,
                    WIND_CROSS_MULTIPLIER, TEMP_HOT_THRESHOLD,
                    TEMP_COLD_THRESHOLD, TEMP_HOT_MULTIPLIER,
                    TEMP_COLD_MULTIPLIER)
from db.database import upsert_many, query


# Stadium orientations: degrees from home plate to center field
# 0° = North, 90° = East, 180° = South, 270° = West
STADIUM_CF_BEARING = {
    "ARI": 0,    # dome — weather doesn't matter
    "ATL": 195,
    "BAL": 0,    # retractable — check if open
    "BOS": 200,
    "CHC": 20,   # Wrigley — wind is EVERYTHING here
    "CHW": 200,
    "CIN": 185,
    "CLE": 175,
    "COL": 200,
    "DET": 210,
    "HOU": 0,    # dome
    "KC":  180,
    "LAA": 200,
    "LAD": 345,
    "MIA": 0,    # dome
    "MIL": 0,    # retractable
    "MIN": 200,
    "NYM": 150,
    "NYY": 225,
    "OAK": 220,
    "PHI": 200,
    "PIT": 20,
    "SD":  200,
    "SF":  200,
    "SEA": 0,    # dome
    "STL": 195,
    "TB":  0,    # dome
    "TEX": 0,    # retractable
    "TOR": 0,    # dome
    "WSH": 200,
}

# Domed stadiums where weather is irrelevant
DOMED_STADIUMS = {"ARI", "HOU", "MIA", "SEA", "TB", "TOR"}
RETRACTABLE_STADIUMS = {"BAL", "MIL", "TEX"}  # may or may not be open


def get_wind_hr_impact(wind_speed_mph: float, wind_dir_deg: int, 
                        stadium_team: str) -> tuple[float, str]:
    """
    Calculate how wind affects HR probability at this stadium.
    
    Returns:
        (multiplier, description)
        multiplier > 1.0 = helps HRs, < 1.0 = hurts HRs
    """
    if stadium_team in DOMED_STADIUMS:
        return 1.0, "dome"
    
    if stadium_team in RETRACTABLE_STADIUMS:
        # Assume open unless we know otherwise
        pass
    
    if wind_speed_mph < 5:
        return 1.0, "calm"

    cf_bearing = STADIUM_CF_BEARING.get(stadium_team, 180)
    
    # Calculate angle between wind direction and CF
    # Wind direction = where wind comes FROM (meteorological convention)
    # We want to know if wind blows TOWARD CF (out) or FROM CF (in)
    wind_toward = (wind_dir_deg + 180) % 360  # direction wind is blowing TO
    
    angle_diff = abs(wind_toward - cf_bearing)
    if angle_diff > 180:
        angle_diff = 360 - angle_diff

    # Scale impact by wind speed (stronger = more effect)
    speed_factor = min(wind_speed_mph / 15, 1.5)  # cap at 1.5x

    if angle_diff <= 45:
        # Wind blowing out toward CF — helps HRs
        impact = 1.0 + (WIND_OUT_MULTIPLIER - 1.0) * speed_factor
        desc = f"out to CF ({wind_speed_mph:.0f}mph)"
    elif angle_diff >= 135:
        # Wind blowing in from CF — hurts HRs  
        impact = 1.0 - (1.0 - WIND_IN_MULTIPLIER) * speed_factor
        desc = f"in from CF ({wind_speed_mph:.0f}mph)"
    else:
        # Crosswind — minor effect
        impact = 1.0 + (WIND_CROSS_MULTIPLIER - 1.0) * speed_factor
        if wind_toward > cf_bearing:
            desc = f"cross L→R ({wind_speed_mph:.0f}mph)"
        else:
            desc = f"cross R→L ({wind_speed_mph:.0f}mph)"

    return round(impact, 3), desc


def get_temp_hr_impact(temp_f: float) -> float:
    """Temperature impact on HR probability."""
    if temp_f >= TEMP_HOT_THRESHOLD:
        # Hotter = more carry
        excess = min((temp_f - TEMP_HOT_THRESHOLD) / 20, 1.0)
        return 1.0 + (TEMP_HOT_MULTIPLIER - 1.0) * excess
    elif temp_f <= TEMP_COLD_THRESHOLD:
        # Colder = dead ball
        deficit = min((TEMP_COLD_THRESHOLD - temp_f) / 20, 1.0)
        return 1.0 - (1.0 - TEMP_COLD_MULTIPLIER) * deficit
    return 1.0


def fetch_game_weather(games: list[dict], stadium_coords: dict) -> list[dict]:
    """
    Fetch weather for each game's stadium.
    
    Args:
        games: list of game dicts with 'game_id' and 'home_team'
        stadium_coords: dict mapping team_abbr → (lat, lon)
    """
    if not WEATHER_API_KEY:
        print("  ⚠️  No WEATHER_API_KEY set — skipping weather fetch")
        return []

    print(f"\n🌤️  Fetching weather for {len(games)} games...")
    weather_rows = []
    now = datetime.now().isoformat()

    for game in games:
        team = game["home_team"]
        
        if team in DOMED_STADIUMS:
            weather_rows.append({
                "game_id": game["game_id"],
                "fetch_time": now,
                "temperature_f": 72,  # climate controlled
                "humidity_pct": 50,
                "wind_speed_mph": 0,
                "wind_direction_deg": 0,
                "wind_description": "dome",
                "wind_hr_impact": 1.0,
                "precipitation_pct": 0,
                "conditions": "dome",
            })
            continue

        coords = stadium_coords.get(team)
        if not coords:
            continue

        try:
            resp = requests.get(f"{WEATHER_API_BASE}/weather", params={
                "lat": coords[0],
                "lon": coords[1],
                "appid": WEATHER_API_KEY,
                "units": "imperial",
            }, timeout=10)
            resp.raise_for_status()
            w = resp.json()

            temp = w["main"]["temp"]
            humidity = w["main"]["humidity"]
            wind_speed = w["wind"]["speed"]  # mph with imperial units
            wind_deg = w["wind"].get("deg", 0)
            conditions = w["weather"][0]["main"] if w.get("weather") else "Unknown"
            rain_chance = w.get("rain", {}).get("1h", 0)

            wind_impact, wind_desc = get_wind_hr_impact(wind_speed, wind_deg, team)
            temp_impact = get_temp_hr_impact(temp)
            
            # Combined weather HR impact
            combined_impact = round(wind_impact * temp_impact, 3)

            weather_rows.append({
                "game_id": game["game_id"],
                "fetch_time": now,
                "temperature_f": round(temp, 1),
                "humidity_pct": humidity,
                "wind_speed_mph": round(wind_speed, 1),
                "wind_direction_deg": wind_deg,
                "wind_description": wind_desc,
                "wind_hr_impact": combined_impact,
                "precipitation_pct": rain_chance,
                "conditions": conditions,
            })
            print(f"  ✅ {team}: {temp:.0f}°F, {wind_desc}, HR impact: {combined_impact}")

        except Exception as e:
            print(f"  ❌ {team}: weather fetch failed — {e}")

    count = upsert_many("weather", weather_rows, ["game_id", "fetch_time"])
    print(f"  💾 Saved {len(weather_rows)} weather records")
    return weather_rows
