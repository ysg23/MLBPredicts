"""Database connection and helper functions (Postgres-first, sqlite fallback)."""
from __future__ import annotations

import json
import os
import sqlite3
from urllib.parse import quote, urlsplit
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import DB_PATH

try:
    import psycopg  # type: ignore
    from psycopg.rows import dict_row  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    psycopg = None
    dict_row = None


def _get_postgres_url() -> str:
    # Priority order: explicit DB URLs first, then Railway/Postgres component vars.
    direct = (
        os.getenv("SUPABASE_DB_URL", "")
        or os.getenv("DATABASE_URL", "")
        or os.getenv("SUPABASE_DATABASE_URL", "")
        or os.getenv("POSTGRES_URL", "")
        or os.getenv("POSTGRESQL_URL", "")
    ).strip()
    if direct:
        return direct

    # Fallback: assemble from discrete PG* variables when URL is not provided.
    pg_host = os.getenv("PGHOST", "").strip()
    pg_port = os.getenv("PGPORT", "5432").strip() or "5432"
    pg_db = os.getenv("PGDATABASE", "postgres").strip() or "postgres"
    pg_user = os.getenv("PGUSER", "postgres").strip() or "postgres"
    pg_pass = os.getenv("PGPASSWORD", "").strip()
    if pg_host:
        user_enc = quote(pg_user, safe="")
        if pg_pass:
            pass_enc = quote(pg_pass, safe="")
            return f"postgresql://{user_enc}:{pass_enc}@{pg_host}:{pg_port}/{pg_db}"
        return f"postgresql://{user_enc}@{pg_host}:{pg_port}/{pg_db}"

    return ""




def _postgres_url_hint(postgres_url: str) -> str | None:
    if not postgres_url:
        return None
    if postgres_url.startswith("https://") or postgres_url.startswith("http://"):
        return (
            "SUPABASE_DB_URL must be a Postgres connection string (postgresql://...), "
            "not the Supabase Project URL."
        )

    parsed = urlsplit(postgres_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        return "Use a postgres:// or postgresql:// connection string for SUPABASE_DB_URL."

    if not parsed.hostname:
        return "Connection string is missing a database host."

    # Common copy/paste issue: password contains reserved chars like '@'.
    raw_creds = parsed.netloc.rsplit("@", 1)[0] if "@" in parsed.netloc else ""
    if ":" in raw_creds:
        raw_password = raw_creds.split(":", 1)[1]
        if "@" in raw_password:
            return (
                "Detected '@' in DB password. URL-encode special characters in the password "
                "(e.g. '@' -> '%40')."
            )

    if "supabase.co" in parsed.netloc and not parsed.hostname.startswith("db."):
        return (
            "Supabase direct DB host usually starts with 'db.' (Direct connection string), "
            "not the project API host."
        )
    return None

def _split_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False

    for ch in sql_text:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double

        if ch == ";" and not in_single and not in_double:
            statement = "".join(buf).strip()
            if statement:
                statements.append(statement)
            buf = []
            continue
        buf.append(ch)

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _adapt_paramstyle(sql: str, backend: str) -> str:
    if backend != "postgres" or "?" not in sql:
        return sql

    converted: list[str] = []
    in_single = False
    in_double = False

    for ch in sql:
        if ch == "'" and not in_double:
            in_single = not in_single
            converted.append(ch)
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            converted.append(ch)
            continue
        if ch == "?" and not in_single and not in_double:
            converted.append("%s")
            continue
        converted.append(ch)
    return "".join(converted)


@dataclass
class DBConnection:
    raw: Any
    backend: str

    def execute(self, sql: str, params: tuple | list | None = None):
        bound = tuple(params) if params is not None else ()
        return self.raw.execute(_adapt_paramstyle(sql, self.backend), bound)

    def executemany(self, sql: str, params_seq: list[tuple]):
        return self.raw.executemany(_adapt_paramstyle(sql, self.backend), params_seq)

    def cursor(self):
        return self.raw.cursor()

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        self.raw.close()

    def __getattr__(self, attr: str):
        return getattr(self.raw, attr)


def get_connection() -> DBConnection:
    """
    Get a backend-aware DB connection wrapper.

    - Postgres/Supabase when DATABASE_URL-style env is present
    - sqlite fallback otherwise
    """
    postgres_url = _get_postgres_url()
    if postgres_url:
        hint = _postgres_url_hint(postgres_url)
        if hint:
            raise RuntimeError(f"Invalid Postgres configuration. {hint}")
        if psycopg is None:
            raise RuntimeError(
                "Postgres URL detected but psycopg is not installed. "
                "Install psycopg[binary] in pipeline requirements."
            )
        try:
            raw = psycopg.connect(postgres_url, row_factory=dict_row)
        except Exception as exc:  # noqa: BLE001
            hint = _postgres_url_hint(postgres_url)
            if hint:
                raise RuntimeError(f"Postgres connection failed. {hint}") from exc
            raise
        return DBConnection(raw=raw, backend="postgres")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(DB_PATH))
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA foreign_keys=ON")
    return DBConnection(raw=raw, backend="sqlite")


def init_db() -> None:
    """Initialize database from the backend-appropriate schema file."""
    conn = get_connection()
    schema_name = "schema.sql" if conn.backend == "postgres" else "schema_sqlite.sql"
    schema_path = Path(__file__).parent / schema_name
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    sql_text = schema_path.read_text(encoding="utf-8")

    try:
        for statement in _split_statements(sql_text):
            conn.execute(statement)
        conn.commit()
    finally:
        conn.close()
    print(f"✅ Database initialized using {schema_name}")


def insert_many(table: str, rows: list[dict]) -> int:
    """Bulk insert rows into a table, ignoring conflicts."""
    if not rows:
        return 0

    cols = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(cols))
    col_str = ", ".join(cols)

    conn = get_connection()
    try:
        if conn.backend == "postgres":
            sql = f"INSERT INTO {table} ({col_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
            inserted = 0
            for row in rows:
                cursor = conn.execute(sql, tuple(row[c] for c in cols))
                if isinstance(cursor.rowcount, int) and cursor.rowcount > 0:
                    inserted += int(cursor.rowcount)
            conn.commit()
            return inserted
        else:
            sql = f"INSERT OR IGNORE INTO {table} ({col_str}) VALUES ({placeholders})"
        cursor = conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        conn.commit()
        return int(cursor.rowcount or 0) if isinstance(cursor.rowcount, int) else 0
    finally:
        conn.close()


def upsert_many(table: str, rows: list[dict], conflict_cols: list[str]) -> int:
    """Insert or update rows based on conflict columns."""
    if not rows:
        return 0

    cols = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(cols))
    col_str = ", ".join(cols)
    conflict_str = ", ".join(conflict_cols)
    update_cols = [c for c in cols if c not in conflict_cols]
    if not update_cols:
        # Degenerate case: conflict-only rows.
        return insert_many(table, rows)
    update_str = ", ".join([f"{c}=excluded.{c}" for c in update_cols])

    sql = (
        f"INSERT INTO {table} ({col_str}) VALUES ({placeholders}) "
        f"ON CONFLICT({conflict_str}) DO UPDATE SET {update_str}"
    )

    conn = get_connection()
    try:
        if conn.backend == "postgres":
            updated = 0
            for row in rows:
                cursor = conn.execute(sql, tuple(row[c] for c in cols))
                if isinstance(cursor.rowcount, int) and cursor.rowcount > 0:
                    updated += int(cursor.rowcount)
            conn.commit()
            return updated

        cursor = conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        conn.commit()
        return int(cursor.rowcount or 0) if isinstance(cursor.rowcount, int) else 0
    finally:
        conn.close()


def _rows_to_dicts(cursor_rows: list[Any], cursor: Any) -> list[dict]:
    if not cursor_rows:
        return []
    first = cursor_rows[0]
    if isinstance(first, dict):
        return [dict(r) for r in cursor_rows]
    if isinstance(first, sqlite3.Row):
        return [dict(r) for r in cursor_rows]
    if hasattr(cursor, "description") and cursor.description:
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor_rows]
    return [dict(r) for r in cursor_rows]


def query(sql: str, params: tuple = ()) -> list[dict]:
    """Run a query and return results as list of dicts."""
    conn = get_connection()
    try:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()
        return _rows_to_dicts(rows, cursor)
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
    """Create and return a score_runs audit row id."""
    conn = get_connection()
    try:
        if conn.backend == "postgres":
            cursor = conn.execute(
                """
                INSERT INTO score_runs (
                    run_type, game_date, market, triggered_by, status, started_at, metadata_json, updated_at
                )
                VALUES (?, ?, ?, ?, 'started', CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP)
                RETURNING id
                """,
                (run_type, game_date, market, triggered_by, _serialize_metadata(metadata)),
            )
            row = cursor.fetchone()
            conn.commit()
            if isinstance(row, dict):
                return int(row["id"])
            return int(row[0])

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
    """Mark a score_runs row complete and update summary fields."""
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
    """Mark a score run failed and attach an error payload."""
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
    """Get row counts for core tables."""
    conn = get_connection()
    tables = [
        "stadiums",
        "park_factors",
        "games",
        "batter_stats",
        "pitcher_stats",
        "weather",
        "hr_odds",
        "umpires",
        "hr_model_scores",
        "market_odds",
        "market_outcomes",
        "model_scores",
        "bets",
        "score_runs",
    ]
    status = {}
    try:
        for table in tables:
            try:
                cursor = conn.execute(f"SELECT COUNT(*) AS count_value FROM {table}")
                row = cursor.fetchone()
                if isinstance(row, dict):
                    status[table] = int(row["count_value"])
                else:
                    status[table] = int(row[0])
            except Exception:
                status[table] = "TABLE MISSING"
        return status
    finally:
        conn.close()
