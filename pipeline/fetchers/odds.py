"""
HR Prop Odds Fetcher.

Uses The Odds API (free tier: 500 requests/month).
Pulls HR prop lines from major sportsbooks.
"""
import requests
from datetime import datetime

from config import ODDS_API_KEY, ODDS_API_BASE
from db.database import upsert_many


def american_to_implied_prob(odds: int) -> float:
    """Convert American odds to implied probability."""
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)


def fetch_hr_props(sport: str = "baseball_mlb") -> list[dict]:
    """
    Fetch HR prop odds from all available sportsbooks.
    
    Free tier: 500 requests/month. Each call here = 1 request.
    Daily usage: ~1-2 calls per day = ~60/month, well within limits.
    """
    if not ODDS_API_KEY:
        print("  ⚠️  No ODDS_API_KEY set — skipping odds fetch")
        return []

    print("\n💰 Fetching HR prop odds...")
    
    url = f"{ODDS_API_BASE}/sports/{sport}/events"
    
    # First get today's events
    resp = requests.get(url, params={
        "apiKey": ODDS_API_KEY,
        "dateFormat": "iso",
    }, timeout=15)
    resp.raise_for_status()
    events = resp.json()
    
    print(f"  📋 Found {len(events)} games with odds")
    
    all_odds = []
    now = datetime.now().isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    
    for event in events:
        event_id = event["id"]
        
        # Fetch player HR props for this event
        try:
            props_url = f"{ODDS_API_BASE}/sports/{sport}/events/{event_id}/odds"
            props_resp = requests.get(props_url, params={
                "apiKey": ODDS_API_KEY,
                "regions": "us",
                "markets": "batter_home_runs",
                "dateFormat": "iso",
                "oddsFormat": "american",
            }, timeout=15)
            props_resp.raise_for_status()
            props_data = props_resp.json()
        except Exception as e:
            print(f"  ⚠️  Could not fetch props for event {event_id}: {e}")
            continue

        # Parse bookmaker odds
        for bookmaker in props_data.get("bookmakers", []):
            book_name = bookmaker["key"]
            
            for market in bookmaker.get("markets", []):
                if market["key"] != "batter_home_runs":
                    continue
                    
                for outcome in market.get("outcomes", []):
                    player_name = outcome.get("description", outcome.get("name", ""))
                    price = outcome.get("price", 0)
                    point = outcome.get("point", 0.5)  # usually 0.5 for HR yes/no
                    
                    # Determine if this is over (HR yes) or under (HR no)
                    is_over = outcome.get("name", "").lower() == "over"
                    
                    all_odds.append({
                        "game_id": None,  # will need to match by teams
                        "game_date": today,
                        "player_id": 0,   # will need to match by name
                        "player_name": player_name,
                        "sportsbook": book_name,
                        "market": "hr",
                        "over_price": price if is_over else None,
                        "under_price": price if not is_over else None,
                        "implied_prob_over": round(american_to_implied_prob(price), 4) if is_over else None,
                        "implied_prob_under": round(american_to_implied_prob(price), 4) if not is_over else None,
                        "fetch_time": now,
                    })

    print(f"  ✅ Collected {len(all_odds)} HR prop lines")
    
    # Consolidate: group by player + book, merge over/under into single row
    consolidated = consolidate_odds(all_odds)
    
    if consolidated:
        count = upsert_many("hr_odds", consolidated, 
                           ["game_date", "player_name", "sportsbook", "fetch_time"])
        print(f"  💾 Saved {len(consolidated)} consolidated odds rows")
    
    return consolidated


def consolidate_odds(raw_odds: list[dict]) -> list[dict]:
    """Merge over/under lines for the same player + book into single rows."""
    merged = {}
    
    for odds in raw_odds:
        key = (odds["player_name"], odds["sportsbook"], odds["game_date"])
        
        if key not in merged:
            merged[key] = odds.copy()
        else:
            # Merge over/under prices
            if odds["over_price"] is not None:
                merged[key]["over_price"] = odds["over_price"]
                merged[key]["implied_prob_over"] = odds["implied_prob_over"]
            if odds["under_price"] is not None:
                merged[key]["under_price"] = odds["under_price"]
                merged[key]["implied_prob_under"] = odds["implied_prob_under"]
    
    return list(merged.values())


def get_best_odds(player_name: str, game_date: str) -> dict:
    """
    Find the best available HR Yes odds across all books for a player.
    Returns the best line and which book has it.
    """
    from db.database import query
    
    results = query("""
        SELECT sportsbook, over_price, implied_prob_over
        FROM hr_odds
        WHERE player_name = ? AND game_date = ? AND over_price IS NOT NULL
        ORDER BY over_price DESC
        LIMIT 5
    """, (player_name, game_date))
    
    if not results:
        return {"best_odds": None, "book": None, "implied_prob": None}
    
    best = results[0]
    return {
        "best_odds": best["over_price"],
        "book": best["sportsbook"],
        "implied_prob": best["implied_prob_over"],
        "all_books": results,
    }
