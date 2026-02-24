"""
Database connection and helper functions.
"""
import json
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


def _serialize_metadata(metadata: dict | None) -> str:
    if metadata is None:
        return "{}"
    return json.dumps(metadata)


def create_score_run(
    run_type: str,
    game_date: str | None = None,
    market: str | None = None,
    triggered_by: str = "system",
    metadata: dict | None = None,
) -> int:
    """
    Create and return a score_runs audit row id.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO score_runs (
                run_type, game_date, market, triggered_by, status, started_at, metadata_json, updated_at
            )
            VALUES (?, ?, ?, ?, 'started', CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP)
            """,
            (run_type, game_date, market, triggered_by, _serialize_metadata(metadata)),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def complete_score_run(
    score_run_id: int,
    status: str,
    rows_scored: int = 0,
    metadata: dict | None = None,
) -> None:
    """
    Mark a score_runs row complete and update summary fields.
    """
    conn = get_connection()
    try:
        if metadata is None:
            conn.execute(
                """
                UPDATE score_runs
                SET status = ?,
                    rows_scored = ?,
                    finished_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, rows_scored, score_run_id),
            )
        else:
            conn.execute(
                """
                UPDATE score_runs
                SET status = ?,
                    rows_scored = ?,
                    finished_at = CURRENT_TIMESTAMP,
                    metadata_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, rows_scored, _serialize_metadata(metadata), score_run_id),
            )
        conn.commit()
    finally:
        conn.close()


def fail_score_run(
    score_run_id: int,
    error_message: str,
    metadata: dict | None = None,
) -> None:
    """
    Mark a score run failed and attach an error payload.
    """
    payload = {"error_message": error_message}
    if metadata:
        payload.update(metadata)
    complete_score_run(
        score_run_id=score_run_id,
        status="failed",
        rows_scored=0,
        metadata=payload,
    )


def get_status() -> dict:
    """Get row counts for all tables."""
    conn = get_connection()
    tables = ["stadiums", "park_factors", "games", "batter_stats",
              "pitcher_stats", "weather", "hr_odds", "umpires",
              "hr_model_scores", "bets", "score_runs"]
    status = {}
    for table in tables:
        try:
            cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
            status[table] = cursor.fetchone()[0]
        except sqlite3.OperationalError:
            status[table] = "TABLE MISSING"
    conn.close()
    return status
