"""
Database connection and helper functions.
"""
import sqlite3
from pathlib import Path
from config import DB_PATH


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory enabled."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Initialize database from schema.sql."""
    schema_path = Path(__file__).parent / "schema.sql"
    conn = get_connection()
    with open(schema_path, "r") as f:
        conn.executescript(f.read())
    conn.close()
    print(f"✅ Database initialized at {DB_PATH}")


def insert_many(table: str, rows: list[dict]):
    """Bulk insert rows into a table, ignoring conflicts."""
    if not rows:
        return 0

    cols = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(cols))
    col_str = ", ".join(cols)
    sql = f"INSERT OR IGNORE INTO {table} ({col_str}) VALUES ({placeholders})"

    conn = get_connection()
    try:
        cursor = conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        conn.commit()
        count = cursor.rowcount
        return count
    finally:
        conn.close()


def upsert_many(table: str, rows: list[dict], conflict_cols: list[str]):
    """Insert or update rows based on conflict columns."""
    if not rows:
        return 0

    cols = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(cols))
    col_str = ", ".join(cols)
    conflict_str = ", ".join(conflict_cols)
    update_cols = [c for c in cols if c not in conflict_cols]
    update_str = ", ".join([f"{c}=excluded.{c}" for c in update_cols])

    sql = f"""INSERT INTO {table} ({col_str}) VALUES ({placeholders})
              ON CONFLICT({conflict_str}) DO UPDATE SET {update_str}"""

    conn = get_connection()
    try:
        cursor = conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def query(sql: str, params: tuple = ()) -> list[dict]:
    """Run a query and return results as list of dicts."""
    conn = get_connection()
    try:
        cursor = conn.execute(sql, params)
        results = [dict(row) for row in cursor.fetchall()]
        return results
    finally:
        conn.close()


def get_status() -> dict:
    """Get row counts for all tables."""
    conn = get_connection()
    tables = ["stadiums", "park_factors", "games", "batter_stats",
              "pitcher_stats", "weather", "hr_odds", "umpires",
              "hr_model_scores", "batter_game_outcomes", "bets"]
    status = {}
    for table in tables:
        try:
            cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
            status[table] = cursor.fetchone()[0]
        except sqlite3.OperationalError:
            status[table] = "TABLE MISSING"
    conn.close()
    return status
