"""
MLB HR Prop Pipeline - Main Runner

Usage:
    python run_pipeline.py --init     # First time: setup DB + load reference data
    python run_pipeline.py --daily    # Daily: fetch all fresh data
    python run_pipeline.py --build-features --date 2026-03-27
    python run_pipeline.py --score --market HR --date 2026-03-27
    python run_pipeline.py --status   # Check database status
    python run_pipeline.py --test     # Quick test: just pull today's schedule
"""
import argparse
import sys
from datetime import datetime

from db.database import init_db, get_status
from utils.stadiums import load_stadiums_to_db, get_stadium_coords


def run_init():
    """One-time initialization: create DB, load reference data."""
    print("=" * 60)
    print("🚀 INITIALIZING MLB HR PROP PIPELINE")
    print("=" * 60)
    
    # 1. Create database tables
    print("\n📦 Step 1: Creating database...")
    init_db()
    
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
    print("  1. Add your API keys to .env file")
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
        from fetchers.pitchers import fetch_daily_pitcher_stats
        pitcher_ids = []
        for g in games:
            if g.get("home_pitcher_id"):
                pitcher_ids.append(g["home_pitcher_id"])
            if g.get("away_pitcher_id"):
                pitcher_ids.append(g["away_pitcher_id"])
        pitcher_ids = [p for p in pitcher_ids if p]
        saved = fetch_daily_pitcher_stats(pitcher_ids, as_of_date=date)
        print(f"  ✅ Saved {saved} pitcher_stat rows")
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




def run_score(date: str = None, market: str = "HR"):
    """
    Run model scoring for a given market and date.
    Requires that --daily has already been run for that date (games, stats, weather, odds).
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    season = int(date.split("-")[0])

    from scoring.base_engine import score_market_for_date

    market = market.upper()
    if market in ("HR",):
        from scoring import hr_model as mod
    elif market in ("K","KS","SO"):
        from scoring import k_model as mod
    elif market in ("ML",):
        from scoring import ml_model as mod
    elif market in ("TOTAL","TOTALS"):
        from scoring import totals_model as mod
    elif market in ("F5_ML","F5ML"):
        from scoring import f5_ml_model as mod
    elif market in ("F5_TOTAL","F5TOTAL"):
        from scoring import f5_total_model as mod
    else:
        raise ValueError(f"Unknown market: {market}")

    saved = score_market_for_date(mod, game_date=date, season=season)
    print(f"✅ Saved {saved} model_scores rows for market={mod.MARKET} date={date}")


def run_build_features(date: str = None, all_dates: bool = False):
    """Run feature-store builders for one date or all known game dates."""
    from build_features import run_build_features as run_features_job

    results = run_features_job(date=date, all_dates=all_dates)
    total_rows = sum(int(r.get("rows_upserted_total", 0)) for r in results)
    print(f"✅ Feature build complete: runs={len(results)} rows_upserted={total_rows}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MLB Multi-Market Data Pipeline")
    parser.add_argument("--init", action="store_true", help="Initialize database + reference data")
    parser.add_argument("--daily", action="store_true", help="Run daily data pipeline")
    parser.add_argument("--build-features", action="store_true", help="Build daily feature snapshots")
    parser.add_argument("--all-dates", action="store_true", help="Use all known game dates")
    parser.add_argument("--score", action="store_true", help="Run market scoring")
    parser.add_argument("--market", type=str, default="HR", help="Market code for --score")
    parser.add_argument("--status", action="store_true", help="Show database status")
    parser.add_argument("--test", action="store_true", help="Quick connection test")
    parser.add_argument("--date", type=str, help="Override date (YYYY-MM-DD)")
    
    args = parser.parse_args()
    
    if args.init:
        run_init()
    elif args.daily:
        run_daily(args.date)
    elif args.build_features:
        run_build_features(date=args.date, all_dates=args.all_dates)
    elif args.score:
        run_score(args.date, args.market)
    elif args.status:
        run_status()
    elif args.test:
        run_test()
    else:
        parser.print_help()
        print("\n💡 Start with: python run_pipeline.py --init")
