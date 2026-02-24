"""
Database connection and helper functions.
Uses Supabase (Postgres) as the shared database.
Both the Railway pipeline and Vercel dashboard connect to the same instance.
"""
import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")  # use service key for backend writes


def get_client() -> Client:
    """Get a Supabase client instance."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env\n"
            "Get these from: Supabase Dashboard → Settings → API"
        )
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def insert_many(table: str, rows: list[dict]) -> int:
    """Bulk insert rows into a table, ignoring conflicts."""
    if not rows:
        return 0
    client = get_client()
    try:
        result = client.table(table).insert(rows).execute()
        return len(result.data) if result.data else 0
    except Exception as e:
        print(f"  ⚠️ Insert error on {table}: {e}")
        return 0


def upsert_many(table: str, rows: list[dict]) -> int:
    """
    Insert or update rows. Supabase upsert uses the table's primary key
    or unique constraints to determine conflicts automatically.
    """
    if not rows:
        return 0
    client = get_client()
    try:
        result = client.table(table).upsert(rows).execute()
        return len(result.data) if result.data else 0
    except Exception as e:
        print(f"  ⚠️ Upsert error on {table}: {e}")
        return 0


def query(table: str, filters: dict = None, select: str = "*",
          order_by: str = None, limit: int = None) -> list[dict]:
    """
    Query a table with optional filters, ordering, and limit.
    
    Usage:
        query("batter_stats", {"player_id": 592450, "window_days": 14})
        query("hr_model_scores", {"game_date": "2026-03-27", "signal": "BET"}, order_by="model_score.desc")
    """
    client = get_client()
    q = client.table(table).select(select)

    if filters:
        for key, value in filters.items():
            q = q.eq(key, value)
    if order_by:
        col, *direction = order_by.split(".")
        desc = direction[0] == "desc" if direction else False
        q = q.order(col, desc=desc)
    if limit:
        q = q.limit(limit)

    result = q.execute()
    return result.data if result.data else []


def get_status() -> dict:
    """Get row counts for all tables."""
    client = get_client()
    tables = ["stadiums", "park_factors", "games", "batter_stats",
              "pitcher_stats", "weather", "hr_odds", "umpires",
              "hr_model_scores", "bets"]
    status = {}
    for table in tables:
        try:
            result = client.table(table).select("*", count="exact").limit(0).execute()
            status[table] = result.count if result.count is not None else 0
        except Exception:
            status[table] = "TABLE MISSING"
    return status
