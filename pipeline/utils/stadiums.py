"""
Stadium reference data.

Coordinates, dimensions, and park factors for all 30 MLB stadiums.
Used for weather lookups and park factor adjustments.
"""

STADIUMS = [
    {"stadium_id": 1, "name": "Chase Field", "team_abbr": "ARI", "city": "Phoenix", "state": "AZ", "latitude": 33.4455, "longitude": -112.0667, "elevation_ft": 1059, "roof_type": "retractable", "lf_distance": 330, "cf_distance": 407, "rf_distance": 335, "hr_park_factor": 1.04},
    {"stadium_id": 2, "name": "Truist Park", "team_abbr": "ATL", "city": "Atlanta", "state": "GA", "latitude": 33.8907, "longitude": -84.4677, "elevation_ft": 1050, "roof_type": "open", "lf_distance": 335, "cf_distance": 400, "rf_distance": 325, "hr_park_factor": 1.00},
    {"stadium_id": 3, "name": "Camden Yards", "team_abbr": "BAL", "city": "Baltimore", "state": "MD", "latitude": 39.2838, "longitude": -76.6218, "elevation_ft": 33, "roof_type": "open", "lf_distance": 333, "cf_distance": 410, "rf_distance": 318, "hr_park_factor": 1.12},
    {"stadium_id": 4, "name": "Fenway Park", "team_abbr": "BOS", "city": "Boston", "state": "MA", "latitude": 42.3467, "longitude": -71.0972, "elevation_ft": 21, "roof_type": "open", "lf_distance": 310, "cf_distance": 390, "rf_distance": 302, "hr_park_factor": 1.05},
    {"stadium_id": 5, "name": "Wrigley Field", "team_abbr": "CHC", "city": "Chicago", "state": "IL", "latitude": 41.9484, "longitude": -87.6553, "elevation_ft": 600, "roof_type": "open", "lf_distance": 355, "cf_distance": 400, "rf_distance": 353, "hr_park_factor": 1.06},
    {"stadium_id": 6, "name": "Guaranteed Rate Field", "team_abbr": "CHW", "city": "Chicago", "state": "IL", "latitude": 41.8299, "longitude": -87.6338, "elevation_ft": 595, "roof_type": "open", "lf_distance": 330, "cf_distance": 400, "rf_distance": 335, "hr_park_factor": 1.08},
    {"stadium_id": 7, "name": "Great American Ball Park", "team_abbr": "CIN", "city": "Cincinnati", "state": "OH", "latitude": 39.0975, "longitude": -84.5070, "elevation_ft": 482, "roof_type": "open", "lf_distance": 328, "cf_distance": 404, "rf_distance": 325, "hr_park_factor": 1.18},
    {"stadium_id": 8, "name": "Progressive Field", "team_abbr": "CLE", "city": "Cleveland", "state": "OH", "latitude": 41.4959, "longitude": -81.6852, "elevation_ft": 653, "roof_type": "open", "lf_distance": 325, "cf_distance": 405, "rf_distance": 325, "hr_park_factor": 0.96},
    {"stadium_id": 9, "name": "Coors Field", "team_abbr": "COL", "city": "Denver", "state": "CO", "latitude": 39.7559, "longitude": -104.9942, "elevation_ft": 5280, "roof_type": "open", "lf_distance": 347, "cf_distance": 415, "rf_distance": 350, "hr_park_factor": 1.38},
    {"stadium_id": 10, "name": "Comerica Park", "team_abbr": "DET", "city": "Detroit", "state": "MI", "latitude": 42.3390, "longitude": -83.0485, "elevation_ft": 600, "roof_type": "open", "lf_distance": 345, "cf_distance": 412, "rf_distance": 330, "hr_park_factor": 0.91},
    {"stadium_id": 11, "name": "Minute Maid Park", "team_abbr": "HOU", "city": "Houston", "state": "TX", "latitude": 29.7573, "longitude": -95.3555, "elevation_ft": 42, "roof_type": "retractable", "lf_distance": 315, "cf_distance": 409, "rf_distance": 326, "hr_park_factor": 1.04},
    {"stadium_id": 12, "name": "Kauffman Stadium", "team_abbr": "KC", "city": "Kansas City", "state": "MO", "latitude": 39.0517, "longitude": -94.4803, "elevation_ft": 800, "roof_type": "open", "lf_distance": 330, "cf_distance": 410, "rf_distance": 330, "hr_park_factor": 0.88},
    {"stadium_id": 13, "name": "Angel Stadium", "team_abbr": "LAA", "city": "Anaheim", "state": "CA", "latitude": 33.8003, "longitude": -117.8827, "elevation_ft": 157, "roof_type": "open", "lf_distance": 330, "cf_distance": 396, "rf_distance": 330, "hr_park_factor": 0.95},
    {"stadium_id": 14, "name": "Dodger Stadium", "team_abbr": "LAD", "city": "Los Angeles", "state": "CA", "latitude": 34.0739, "longitude": -118.2400, "elevation_ft": 515, "roof_type": "open", "lf_distance": 330, "cf_distance": 395, "rf_distance": 330, "hr_park_factor": 0.93},
    {"stadium_id": 15, "name": "LoanDepot Park", "team_abbr": "MIA", "city": "Miami", "state": "FL", "latitude": 25.7781, "longitude": -80.2196, "elevation_ft": 7, "roof_type": "retractable", "lf_distance": 344, "cf_distance": 407, "rf_distance": 335, "hr_park_factor": 0.82},
    {"stadium_id": 16, "name": "American Family Field", "team_abbr": "MIL", "city": "Milwaukee", "state": "WI", "latitude": 43.0280, "longitude": -87.9712, "elevation_ft": 600, "roof_type": "retractable", "lf_distance": 344, "cf_distance": 400, "rf_distance": 345, "hr_park_factor": 1.02},
    {"stadium_id": 17, "name": "Target Field", "team_abbr": "MIN", "city": "Minneapolis", "state": "MN", "latitude": 44.9817, "longitude": -93.2776, "elevation_ft": 841, "roof_type": "open", "lf_distance": 339, "cf_distance": 411, "rf_distance": 328, "hr_park_factor": 0.94},
    {"stadium_id": 18, "name": "Citi Field", "team_abbr": "NYM", "city": "New York", "state": "NY", "latitude": 40.7571, "longitude": -73.8458, "elevation_ft": 20, "roof_type": "open", "lf_distance": 335, "cf_distance": 408, "rf_distance": 330, "hr_park_factor": 0.89},
    {"stadium_id": 19, "name": "Yankee Stadium", "team_abbr": "NYY", "city": "New York", "state": "NY", "latitude": 40.8296, "longitude": -73.9262, "elevation_ft": 55, "roof_type": "open", "lf_distance": 318, "cf_distance": 408, "rf_distance": 314, "hr_park_factor": 1.15},
    {"stadium_id": 20, "name": "Sutter Health Park", "team_abbr": "OAK", "city": "West Sacramento", "state": "CA", "latitude": 38.5802, "longitude": -121.5111, "elevation_ft": 26, "roof_type": "open", "lf_distance": 330, "cf_distance": 403, "rf_distance": 325, "hr_park_factor": 1.00},
    {"stadium_id": 21, "name": "Citizens Bank Park", "team_abbr": "PHI", "city": "Philadelphia", "state": "PA", "latitude": 39.9061, "longitude": -75.1665, "elevation_ft": 30, "roof_type": "open", "lf_distance": 329, "cf_distance": 401, "rf_distance": 330, "hr_park_factor": 1.10},
    {"stadium_id": 22, "name": "PNC Park", "team_abbr": "PIT", "city": "Pittsburgh", "state": "PA", "latitude": 40.4469, "longitude": -80.0058, "elevation_ft": 730, "roof_type": "open", "lf_distance": 325, "cf_distance": 399, "rf_distance": 320, "hr_park_factor": 0.85},
    {"stadium_id": 23, "name": "Petco Park", "team_abbr": "SD", "city": "San Diego", "state": "CA", "latitude": 32.7076, "longitude": -117.1570, "elevation_ft": 17, "roof_type": "open", "lf_distance": 336, "cf_distance": 396, "rf_distance": 322, "hr_park_factor": 0.88},
    {"stadium_id": 24, "name": "Oracle Park", "team_abbr": "SF", "city": "San Francisco", "state": "CA", "latitude": 37.7786, "longitude": -122.3893, "elevation_ft": 3, "roof_type": "open", "lf_distance": 339, "cf_distance": 399, "rf_distance": 309, "hr_park_factor": 0.83},
    {"stadium_id": 25, "name": "T-Mobile Park", "team_abbr": "SEA", "city": "Seattle", "state": "WA", "latitude": 47.5914, "longitude": -122.3325, "elevation_ft": 10, "roof_type": "retractable", "lf_distance": 331, "cf_distance": 405, "rf_distance": 326, "hr_park_factor": 0.90},
    {"stadium_id": 26, "name": "Busch Stadium", "team_abbr": "STL", "city": "St. Louis", "state": "MO", "latitude": 38.6226, "longitude": -90.1928, "elevation_ft": 455, "roof_type": "open", "lf_distance": 336, "cf_distance": 400, "rf_distance": 335, "hr_park_factor": 0.96},
    {"stadium_id": 27, "name": "Tropicana Field", "team_abbr": "TB", "city": "St. Petersburg", "state": "FL", "latitude": 27.7682, "longitude": -82.6534, "elevation_ft": 44, "roof_type": "dome", "lf_distance": 315, "cf_distance": 404, "rf_distance": 322, "hr_park_factor": 0.91},
    {"stadium_id": 28, "name": "Globe Life Field", "team_abbr": "TEX", "city": "Arlington", "state": "TX", "latitude": 32.7474, "longitude": -97.0845, "elevation_ft": 551, "roof_type": "retractable", "lf_distance": 329, "cf_distance": 407, "rf_distance": 326, "hr_park_factor": 0.97},
    {"stadium_id": 29, "name": "Rogers Centre", "team_abbr": "TOR", "city": "Toronto", "state": "ON", "latitude": 43.6414, "longitude": -79.3894, "elevation_ft": 269, "roof_type": "retractable", "lf_distance": 328, "cf_distance": 400, "rf_distance": 328, "hr_park_factor": 1.05},
    {"stadium_id": 30, "name": "Nationals Park", "team_abbr": "WSH", "city": "Washington", "state": "DC", "latitude": 38.8730, "longitude": -77.0074, "elevation_ft": 25, "roof_type": "open", "lf_distance": 336, "cf_distance": 403, "rf_distance": 335, "hr_park_factor": 0.98},
]


# Per-park HR multipliers by batter hand (lhb_factor, rhb_factor).
# Values are relative to the generic hr_park_factor stored in STADIUMS.
# Only parks with meaningful handedness asymmetry are listed; all others default to 1.0.
HANDEDNESS_HR_FACTORS: dict[str, tuple[float, float]] = {
    "NYY": (1.22, 0.93),   # short RF porch (314 ft) vs deep left-center
    "BOS": (0.85, 1.08),   # Green Monster suppresses LHB HR
    "SF":  (0.80, 0.90),   # deep RF 421 ft, marine air
    "COL": (1.38, 1.38),   # elevation boost, symmetric
    "CIN": (1.18, 1.15),   # small park, both sides
    "BAL": (0.95, 1.10),   # deep LF favors RHB
    "HOU": (0.90, 1.05),   # deep CF, favors pull hitters
    "CHC": (1.10, 1.05),   # Wrigley wind baseline (out)
    "TEX": (1.12, 1.08),   # Globe Life, warm air
    "PHI": (1.05, 1.08),   # smaller dimensions
}


def get_handedness_hr_factor(team_abbr: str, bat_hand: str | None) -> float:
    """Return the HR park-factor multiplier for a batter's hand at the given team's park.

    Returns 1.0 for parks not in HANDEDNESS_HR_FACTORS (no asymmetry data).
    For unknown hand (None or switch hitter) returns the average of LHB/RHB.
    """
    factors = HANDEDNESS_HR_FACTORS.get(team_abbr)
    if not factors:
        return 1.0
    lhb, rhb = factors
    if bat_hand == "L":
        return lhb
    if bat_hand == "R":
        return rhb
    return (lhb + rhb) / 2  # switch hitter or unknown hand


def get_stadium_coords() -> dict:
    """Return dict mapping team_abbr → (latitude, longitude)."""
    return {s["team_abbr"]: (s["latitude"], s["longitude"]) for s in STADIUMS}


def load_stadiums_to_db():
    """Load all stadium data into the database."""
    from db.database import upsert_many
    count = upsert_many("mlb_stadiums", STADIUMS, ["team_abbr"])
    print(f"  🏟️  Loaded {count} stadiums into database")
    return count
