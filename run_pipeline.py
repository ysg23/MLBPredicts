"""
MLB HR Prop Pipeline - Main Runner

Usage:
    python run_pipeline.py --init     # First time: setup DB + load reference data
    python run_pipeline.py --daily    # Daily: fetch all fresh data
    python run_pipeline.py --status   # Check database status
    python run_pipeline.py --test     # Quick test: just pull today's schedule
"""
import argparse
import sys
from datetime import datetime

from db.database import get_status
from utils.stadiums import load_stadiums_to_db, get_stadium_coords


def run_init():
    """One-time initialization: create DB, load reference data."""
    print("=" * 60)
    print("🚀 INITIALIZING MLB HR PROP PIPELINE")
    print("=" * 60)
    
    # 1. Verify Supabase connection
    print("\n📦 Step 1: Verifying Supabase connection...")
    try:
        from db.database import get_client
        client = get_client()
        print("  ✅ Connected to Supabase")
    except Exception as e:
        print(f"  ❌ Cannot connect to Supabase: {e}")
        print("  💡 Make sure SUPABASE_URL and SUPABASE_SERVICE_KEY are set in .env")
        print("  💡 Get these from: Supabase Dashboard → Settings → API")
        return
    
    # 2. Check if tables exist
    print("\n📋 Step 2: Checking database tables...")
    status = get_status()
    missing = [t for t, c in status.items() if c == "TABLE MISSING"]
    if missing:
        print(f"  ⚠️  Missing tables: {', '.join(missing)}")
        print("  💡 Run db/schema.sql in Supabase SQL Editor to create tables")
        print("  💡 Go to: Supabase Dashboard → SQL Editor → New Query → paste schema.sql → Run")
        return
    else:
        print("  ✅ All tables exist")
    
    # 2. Load stadium reference data
    print("\n🏟️  Step 2: Loading stadium data...")
    load_stadiums_to_db()
    
    # 3. Load historical park factors
    print("\n📊 Step 3: Park factors...")
    print("  ℹ️  Park factors loaded from stadium defaults")
    print("  💡 For season-specific factors, download from FanGraphs:")
    print("     https://www.fangraphs.com/guts.aspx?type=pf&season=2025")
    
    print("\n" + "=" * 60)
    print("✅ INITIALIZATION COMPLETE")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. Make sure your API keys are in .env")
    print("  2. Run: python run_pipeline.py --daily")
    print("  3. (Optional) Download FanGraphs park factors CSV")


def run_daily(date: str = None):
    """
    Daily pipeline run. Fetches all data sources for today's games.
    
    Pipeline order:
    1. Today's schedule + probable pitchers
    2. Batter rolling stats (Statcast)
    3. Pitcher rolling stats (Statcast)
    4. Weather conditions
    5. HR prop odds
    6. Umpire assignments
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    
    print("=" * 60)
    print(f"⚾ DAILY PIPELINE RUN — {date}")
    print(f"🕐 Started at {datetime.now().strftime('%I:%M %p ET')}")
    print("=" * 60)
    
    # ── Step 1: Schedule ──────────────────────────────────
    print("\n" + "─" * 40)
    print("STEP 1/6: Game Schedule")
    print("─" * 40)
    try:
        from fetchers.schedule import fetch_todays_games, fetch_umpire_assignments
        games = fetch_todays_games(date)
        
        if not games:
            print("\n⚠️  No games today. Pipeline complete.")
            return
        
        # Also fetch umpire assignments
        umpires = fetch_umpire_assignments(date)
        for game in games:
            game["umpire_name"] = umpires.get(game["game_id"])
            
    except Exception as e:
        print(f"  ❌ Schedule fetch failed: {e}")
        games = []

    # ── Step 2: Batter Stats ──────────────────────────────
    print("\n" + "─" * 40)
    print("STEP 2/6: Batter Rolling Stats (Statcast)")
    print("─" * 40)
    try:
        from fetchers.statcast import fetch_daily_batter_stats
        batter_stats = fetch_daily_batter_stats()
    except Exception as e:
        print(f"  ❌ Batter stats failed: {e}")
        print(f"  💡 This might be a pybaseball issue — check your connection")
        batter_stats = []

    # ── Step 3: Pitcher Stats ─────────────────────────────
    print("\n" + "─" * 40)
    print("STEP 3/6: Pitcher Rolling Stats")
    print("─" * 40)
    try:
        # For now, pitcher stats use the same Statcast pull
        # TODO: Build dedicated pitcher fetcher
        print("  ℹ️  Pitcher module coming soon — using schedule data for now")
    except Exception as e:
        print(f"  ❌ Pitcher stats failed: {e}")

    # ── Step 4: Weather ───────────────────────────────────
    print("\n" + "─" * 40)
    print("STEP 4/6: Game-Day Weather")
    print("─" * 40)
    try:
        from fetchers.weather import fetch_game_weather
        stadium_coords = get_stadium_coords()
        weather = fetch_game_weather(games, stadium_coords)
    except Exception as e:
        print(f"  ❌ Weather fetch failed: {e}")
        weather = []

    # ── Step 5: Odds ──────────────────────────────────────
    print("\n" + "─" * 40)
    print("STEP 5/6: HR Prop Odds")
    print("─" * 40)
    try:
        from fetchers.odds import fetch_hr_props
        odds = fetch_hr_props()
    except Exception as e:
        print(f"  ❌ Odds fetch failed: {e}")
        odds = []

    # ── Step 6: Summary ───────────────────────────────────
    print("\n" + "─" * 40)
    print("STEP 6/6: Pipeline Summary")
    print("─" * 40)
    
    print(f"\n  📅 Games today:        {len(games)}")
    print(f"  🏏 Batter stat rows:   {len(batter_stats) if batter_stats else 0}")
    print(f"  🌤️  Weather records:    {len(weather)}")
    print(f"  💰 Odds records:       {len(odds)}")
    
    print("\n" + "=" * 60)
    print(f"✅ DAILY PIPELINE COMPLETE — {datetime.now().strftime('%I:%M %p ET')}")
    print("=" * 60)
    print("\nNext: Run model scoring (coming soon)")
    print("  python run_pipeline.py --score")


def run_status():
    """Print database status."""
    print("\n📊 DATABASE STATUS")
    print("─" * 40)
    
    status = get_status()
    for table, count in status.items():
        icon = "✅" if isinstance(count, int) and count > 0 else "⬜"
        print(f"  {icon} {table:.<30} {count:>8}")
    
    total = sum(v for v in status.values() if isinstance(v, int))
    print(f"\n  Total rows: {total:,}")


def run_test():
    """Quick test: just pull today's schedule to verify API access."""
    print("\n🧪 QUICK TEST — Fetching today's schedule...")
    try:
        from fetchers.schedule import fetch_todays_games
        games = fetch_todays_games()
        
        if games:
            print(f"\n  Found {len(games)} games:")
            for g in games[:5]:
                print(f"    {g['away_team']} @ {g['home_team']}")
                print(f"      SP: {g['away_pitcher_name']} vs {g['home_pitcher_name']}")
            if len(games) > 5:
                print(f"    ... and {len(games) - 5} more")
        else:
            print("  No games found (off day or offseason)")
        
        print("\n✅ API connection works!")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        print("  Check your internet connection and try again")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MLB HR Prop Data Pipeline")
    parser.add_argument("--init", action="store_true", help="Initialize database + reference data")
    parser.add_argument("--daily", action="store_true", help="Run daily data pipeline")
    parser.add_argument("--status", action="store_true", help="Show database status")
    parser.add_argument("--test", action="store_true", help="Quick connection test")
    parser.add_argument("--date", type=str, help="Override date (YYYY-MM-DD)")
    
    args = parser.parse_args()
    
    if args.init:
        run_init()
    elif args.daily:
        run_daily(args.date)
    elif args.status:
        run_status()
    elif args.test:
        run_test()
    else:
        parser.print_help()
        print("\n💡 Start with: python run_pipeline.py --init")
