"""
Lightweight SQL migration runner.

Usage:
    python db/migrate.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, Tuple
from urllib.parse import quote

PIPELINE_ROOT = Path(__file__).resolve().parents[1]
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from config import DB_PATH

try:
    import psycopg  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    psycopg = None


MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _get_postgres_url() -> str:
    # Priority: explicit URL env vars first.
    direct = (
        os.getenv("SUPABASE_DB_URL", "")
        or os.getenv("DATABASE_URL", "")
        or os.getenv("SUPABASE_DATABASE_URL", "")
        or os.getenv("POSTGRES_URL", "")
        or os.getenv("POSTGRESQL_URL", "")
    ).strip()
    if direct:
        return direct

    # Fallback: assemble from discrete Railway PG* variables.
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


def get_connection() -> Tuple[object, str]:
    """
    Return (connection, backend_name).

    - backend_name: "postgres" or "sqlite"
    """
    postgres_url = _get_postgres_url()
    if postgres_url.startswith("postgres"):
        if psycopg is None:
            raise RuntimeError(
                "Postgres URL detected but psycopg is not installed. "
                "Install psycopg to run migrations against Supabase/Postgres."
            )
        conn = psycopg.connect(postgres_url)
        return conn, "postgres"

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    return conn, "sqlite"


def _split_statements(sql_text: str) -> list[str]:
    # Migrations in this repo are plain SQL statements separated by semicolons.
    return [stmt.strip() for stmt in sql_text.split(";") if stmt.strip()]


def _ensure_schema_migrations(conn: object, backend: str) -> None:
    if backend == "postgres":
        sql = """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id BIGSERIAL PRIMARY KEY,
            filename TEXT NOT NULL UNIQUE,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    else:
        sql = """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL UNIQUE,
            applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    _execute_statement(conn, backend, sql)
    _commit(conn, backend)


def _fetch_applied_migrations(conn: object, backend: str) -> set[str]:
    cursor = _cursor(conn, backend)
    cursor.execute("SELECT filename FROM schema_migrations ORDER BY filename")
    rows = cursor.fetchall()
    return {row[0] for row in rows}


def _idempotent_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "already exists",
            "duplicate column",
            "duplicate key",
            "column already exists",
            "index already exists",
            "no such column",
            "undefined column",
        )
    )


def _cursor(conn: object, backend: str):
    return conn.cursor()


def _execute_statement(conn: object, backend: str, statement: str) -> None:
    cursor = _cursor(conn, backend)
    cursor.execute(statement)


def _commit(conn: object, backend: str) -> None:
    conn.commit()


def _rollback(conn: object, backend: str) -> None:
    conn.rollback()


def _record_applied(conn: object, backend: str, filename: str) -> None:
    cursor = _cursor(conn, backend)
    if backend == "postgres":
        cursor.execute(
            "INSERT INTO schema_migrations (filename) VALUES (%s)",
            (filename,),
        )
    else:
        cursor.execute(
            "INSERT INTO schema_migrations (filename) VALUES (?)",
            (filename,),
        )


def _migration_files() -> Iterable[Path]:
    if not MIGRATIONS_DIR.exists():
        return []
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def run_migrations() -> int:
    conn, backend = get_connection()
    print(f"Running migrations using backend={backend}")
    try:
        if backend != "postgres":
            print(
                "No Postgres URL detected. Skipping SQL migrations: "
                "sqlite fallback should use schema_sqlite.sql via run_pipeline --init."
            )
            return 0

        _ensure_schema_migrations(conn, backend)
        applied = _fetch_applied_migrations(conn, backend)
        pending = [p for p in _migration_files() if p.name not in applied]

        if not pending:
            print("No pending migrations.")
            return 0

        for migration_file in pending:
            print(f"Applying {migration_file.name} ...")
            sql_text = migration_file.read_text(encoding="utf-8")
            statements = _split_statements(sql_text)
            try:
                for statement in statements:
                    try:
                        _execute_statement(conn, backend, statement)
                    except Exception as exc:  # pragma: no cover - backend-specific
                        if _idempotent_error(exc):
                            print(f"  ↷ skipped idempotent statement: {exc}")
                            continue
                        raise
                _record_applied(conn, backend, migration_file.name)
                _commit(conn, backend)
                print(f"  ✓ applied {migration_file.name}")
            except Exception as exc:
                _rollback(conn, backend)
                print(f"  ✗ failed {migration_file.name}: {exc}")
                return 1

        print("All pending migrations applied successfully.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(run_migrations())
