"""
MLB Schedule & Lineup Fetcher.

Uses the free MLB Stats API (statsapi.mlb.com) — no auth required.
Pulls today's games, probable pitchers, and lineup data.
"""
import requests
from datetime import datetime

from config import MLB_STATS_BASE, TEAM_ABBRS
from db.database import upsert_many, query


def _lookup_stadium_ids() -> dict[str, int]:
    """Build a team_abbr -> stadium_id map from the stadiums table."""
    rows = query("SELECT stadium_id, team_abbr FROM stadiums WHERE team_abbr IS NOT NULL")
    return {str(r["team_abbr"]): int(r["stadium_id"]) for r in rows}


def _resolve_pitcher_hands(pitcher_ids: list[int]) -> dict[int, str | None]:
    """Batch-fetch pitcher handedness from the MLB Stats API /people endpoint."""
    if not pitcher_ids:
        return {}
    ids_str = ",".join(str(pid) for pid in pitcher_ids if pid)
    if not ids_str:
        return {}
    try:
        resp = requests.get(
            f"{MLB_STATS_BASE}/people",
            params={"personIds": ids_str, "hydrate": "currentTeam"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {}
    result: dict[int, str | None] = {}
    for person in data.get("people", []):
        pid = person.get("id")
        hand = (person.get("pitchHand") or {}).get("code")
        if pid is not None:
            result[int(pid)] = hand
    return result


def fetch_todays_games(date: str = None) -> list[dict]:
    """
    Fetch today's MLB schedule with probable pitchers.

    Args:
        date: YYYY-MM-DD format, defaults to today

    Returns:
        List of game dicts ready for DB insertion
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    print(f"\n📅 Fetching games for {date}...")

    url = f"{MLB_STATS_BASE}/schedule"
    params = {
        "date": date,
        "sportId": 1,  # MLB
        "hydrate": "probablePitcher,linescore,team",
    }

    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    # Lookup stadium_id by home team abbreviation
    stadium_map = _lookup_stadium_ids()

    # Collect all pitcher IDs for batch hand resolution
    all_pitcher_ids: list[int] = []

    games = []
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            game_id = game["gamePk"]
            status = game["status"]["detailedState"].lower()

            home = game["teams"]["home"]
            away = game["teams"]["away"]

            home_team = home["team"]["name"]
            away_team = away["team"]["name"]

            # Probable pitchers
            home_pitcher = home.get("probablePitcher", {})
            away_pitcher = away.get("probablePitcher", {})

            hp_id = home_pitcher.get("id")
            ap_id = away_pitcher.get("id")
            if hp_id:
                all_pitcher_ids.append(int(hp_id))
            if ap_id:
                all_pitcher_ids.append(int(ap_id))

            game_time = game.get("gameDate", "")  # UTC ISO format
            home_abbr = TEAM_ABBRS.get(home_team, home_team)

            games.append({
                "game_id": game_id,
                "game_date": date,
                "game_time": game_time,
                "home_team": home_abbr,
                "away_team": TEAM_ABBRS.get(away_team, away_team),
                "home_pitcher_id": hp_id,
                "away_pitcher_id": ap_id,
                "home_pitcher_name": home_pitcher.get("fullName", "TBD"),
                "away_pitcher_name": away_pitcher.get("fullName", "TBD"),
                "home_pitcher_hand": None,  # resolved below
                "away_pitcher_hand": None,
                "stadium_id": stadium_map.get(home_abbr),
                "umpire_name": None,  # filled separately
                "status": "scheduled" if "scheduled" in status or "pre" in status
                          else "live" if "in progress" in status
                          else "final" if "final" in status
                          else status,
                "home_score": home.get("score"),
                "away_score": away.get("score"),
            })

    # Batch-resolve pitcher handedness from MLB Stats API
    hand_map = _resolve_pitcher_hands(list(set(all_pitcher_ids)))
    for g in games:
        if g["home_pitcher_id"] and g["home_pitcher_id"] in hand_map:
            g["home_pitcher_hand"] = hand_map[g["home_pitcher_id"]]
        if g["away_pitcher_id"] and g["away_pitcher_id"] in hand_map:
            g["away_pitcher_hand"] = hand_map[g["away_pitcher_id"]]

    count = upsert_many("games", games, ["game_id"])
    print(f"  ✅ {len(games)} games found, {count} inserted/updated")
    return games


def fetch_game_lineups(game_id: int) -> dict:
    """
    Fetch confirmed lineups for a specific game.
    Returns dict with 'home' and 'away' lists of player IDs.
    
    Note: Lineups are usually posted 1-3 hours before game time.
    """
    url = f"{MLB_STATS_BASE}/game/{game_id}/boxscore"
    
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  ⚠️  Could not fetch lineup for game {game_id}: {e}")
        return {"home": [], "away": []}

    lineups = {"home": [], "away": []}
    
    for side in ["home", "away"]:
        team_data = data.get("teams", {}).get(side, {})
        batting_order = team_data.get("battingOrder", [])
        players = team_data.get("players", {})
        
        for player_id in batting_order:
            player_key = f"ID{player_id}"
            player = players.get(player_key, {})
            lineups[side].append({
                "player_id": player_id,
                "name": player.get("person", {}).get("fullName", "Unknown"),
                "position": player.get("position", {}).get("abbreviation", ""),
                "bat_side": player.get("person", {}).get("batSide", {}).get("code", "R"),
            })

    return lineups


def fetch_umpire_assignments(date: str = None) -> dict:
    """
    Fetch home plate umpire assignments for today's games.
    Returns dict mapping game_id → umpire_name.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    print(f"  👨‍⚖️ Fetching umpire assignments for {date}...")

    url = f"{MLB_STATS_BASE}/schedule"
    params = {
        "date": date,
        "sportId": 1,
        "hydrate": "officials",
    }

    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    assignments = {}
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            game_id = game["gamePk"]
            officials = game.get("officials", [])
            
            for official in officials:
                if official.get("officialType") == "Home Plate":
                    assignments[game_id] = official.get("official", {}).get("fullName", "Unknown")
                    break

    print(f"  ✅ Found {len(assignments)} umpire assignments")
    return assignments
