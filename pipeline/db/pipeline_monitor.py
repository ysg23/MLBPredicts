"""
MLB pipeline monitoring — writes run status to shared pipeline_runs and
pipeline_failures tables (shared with NHL on Supabase Pro). Also maintains
data_source_health and sends Discord alerts on repeated failures.

Usage:
    from db.pipeline_monitor import pipeline_run, update_source_health

    with pipeline_run("schedule_fetch", service_name="mlb-data-ingester", source="mlb_stats_api") as run:
        games = fetch_todays_games(date)
        run.records_processed = len(games)
    # success -> pipeline_runs row with status='success'
    # exception -> pipeline_runs row with status='failed' + pipeline_failures row

The context manager never propagates monitoring exceptions — a DB write failure
will be logged but the job itself is not interrupted.
"""
from __future__ import annotations

import json
import logging
import os
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, Optional

log = logging.getLogger(__name__)

_SERVICE_DEFAULT = "mlb-pipeline"
_DISCORD_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0",
}


# ── Discord ───────────────────────────────────────────────────────────────────

def _discord_alert(message: str) -> None:
    """Post a message to the configured Discord webhook. Silently no-ops if
    DISCORD_WEBHOOK_URL is not set or the request fails."""
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        return
    try:
        import requests  # local import — optional at module load time
        requests.post(url, json={"content": message}, headers=_DISCORD_HEADERS, timeout=10)
    except Exception as exc:
        log.warning("Discord alert failed: %s", exc)


# ── Internal DB helpers ───────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _insert_pipeline_run(
    conn,
    job_name: str,
    service_name: str,
    started_at: datetime,
) -> Optional[int]:
    """Insert a pipeline_runs row with status='running'. Returns the new row id."""
    try:
        if conn.backend == "postgres":
            cursor = conn.execute(
                """
                INSERT INTO pipeline_runs
                    (service_name, job_name, status, started_at, records_processed,
                     error_message, metadata, created_at)
                VALUES (%s, %s, 'running', %s, 0, NULL, '{}', %s)
                RETURNING id
                """,
                (service_name, job_name, started_at, started_at),
            )
            row = cursor.fetchone()
            conn.commit()
            if isinstance(row, dict):
                return int(row["id"])
            return int(row[0]) if row else None
        else:
            cursor = conn.execute(
                """
                INSERT INTO pipeline_runs
                    (service_name, job_name, status, started_at, records_processed,
                     error_message, metadata, created_at)
                VALUES (?, ?, 'running', ?, 0, NULL, '{}', ?)
                """,
                (service_name, job_name, started_at.isoformat(), started_at.isoformat()),
            )
            conn.commit()
            return int(cursor.lastrowid)
    except Exception as exc:
        log.warning("pipeline_monitor: could not insert pipeline_runs row: %s", exc)
        return None


def _update_pipeline_run_success(
    conn,
    run_id: int,
    completed_at: datetime,
    records_processed: int,
    metadata: dict,
) -> None:
    try:
        metadata_str = json.dumps(metadata)
        if conn.backend == "postgres":
            conn.execute(
                """
                UPDATE pipeline_runs
                SET status = 'success',
                    completed_at = %s,
                    records_processed = %s,
                    metadata = %s
                WHERE id = %s
                """,
                (completed_at, records_processed, metadata_str, run_id),
            )
        else:
            conn.execute(
                """
                UPDATE pipeline_runs
                SET status = 'success',
                    completed_at = ?,
                    records_processed = ?,
                    metadata = ?
                WHERE id = ?
                """,
                (completed_at.isoformat(), records_processed, metadata_str, run_id),
            )
        conn.commit()
    except Exception as exc:
        log.warning("pipeline_monitor: could not update pipeline_runs success: %s", exc)


def _update_pipeline_run_failed(
    conn,
    run_id: int,
    completed_at: datetime,
    records_processed: int,
    error_message: str,
    metadata: dict,
) -> None:
    try:
        metadata_str = json.dumps(metadata)
        if conn.backend == "postgres":
            conn.execute(
                """
                UPDATE pipeline_runs
                SET status = 'failed',
                    completed_at = %s,
                    records_processed = %s,
                    error_message = %s,
                    metadata = %s
                WHERE id = %s
                """,
                (completed_at, records_processed, error_message[:2000], metadata_str, run_id),
            )
        else:
            conn.execute(
                """
                UPDATE pipeline_runs
                SET status = 'failed',
                    completed_at = ?,
                    records_processed = ?,
                    error_message = ?,
                    metadata = ?
                WHERE id = ?
                """,
                (completed_at.isoformat(), records_processed, error_message[:2000], metadata_str, run_id),
            )
        conn.commit()
    except Exception as exc:
        log.warning("pipeline_monitor: could not update pipeline_runs failed: %s", exc)


def _insert_pipeline_failure(
    conn,
    service_name: str,
    job_name: str,
    error_type: str,
    error_message: str,
    stack_trace: str,
    context: dict,
    failed_at: datetime,
) -> None:
    try:
        context_str = json.dumps(context)
        if conn.backend == "postgres":
            conn.execute(
                """
                INSERT INTO pipeline_failures
                    (service_name, job_name, error_type, error_message, stack_trace,
                     context, failed_at, resolved, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE, %s)
                """,
                (
                    service_name, job_name, error_type, error_message[:2000],
                    stack_trace[:5000], context_str, failed_at, failed_at,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO pipeline_failures
                    (service_name, job_name, error_type, error_message, stack_trace,
                     context, failed_at, resolved, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    service_name, job_name, error_type, error_message[:2000],
                    stack_trace[:5000], context_str, failed_at.isoformat(), failed_at.isoformat(),
                ),
            )
        conn.commit()
    except Exception as exc:
        log.warning("pipeline_monitor: could not insert pipeline_failures row: %s", exc)


# ── Public API ────────────────────────────────────────────────────────────────

def update_source_health(source_name: str, success: bool) -> None:
    """Update data_source_health for the given source.

    On success: resets consecutive_failures to 0, marks is_healthy=True.
    On failure: increments consecutive_failures; sends a Discord alert when
    the count reaches 3.

    Never raises — any DB error is logged and swallowed.
    """
    from db.database import get_connection  # deferred to avoid circular at import

    now = _now_utc()
    try:
        conn = get_connection()
        try:
            if success:
                if conn.backend == "postgres":
                    conn.execute(
                        """
                        INSERT INTO data_source_health
                            (source_name, last_success_at, consecutive_failures, is_healthy, updated_at)
                        VALUES (%s, %s, 0, TRUE, %s)
                        ON CONFLICT (source_name) DO UPDATE SET
                            last_success_at = EXCLUDED.last_success_at,
                            consecutive_failures = 0,
                            is_healthy = TRUE,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (source_name, now, now),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO data_source_health
                            (source_name, last_success_at, consecutive_failures, is_healthy, updated_at)
                        VALUES (?, ?, 0, 1, ?)
                        ON CONFLICT(source_name) DO UPDATE SET
                            last_success_at = excluded.last_success_at,
                            consecutive_failures = 0,
                            is_healthy = 1,
                            updated_at = excluded.updated_at
                        """,
                        (source_name, now.isoformat(), now.isoformat()),
                    )
                conn.commit()
            else:
                # Read the current consecutive_failures count, then increment.
                if conn.backend == "postgres":
                    cursor = conn.execute(
                        "SELECT consecutive_failures FROM data_source_health WHERE source_name = %s",
                        (source_name,),
                    )
                else:
                    cursor = conn.execute(
                        "SELECT consecutive_failures FROM data_source_health WHERE source_name = ?",
                        (source_name,),
                    )
                row = cursor.fetchone()
                if isinstance(row, dict):
                    prev = int(row.get("consecutive_failures") or 0)
                elif row is not None:
                    prev = int(row[0] or 0)
                else:
                    prev = 0
                new_count = prev + 1
                is_healthy = new_count < 3

                if conn.backend == "postgres":
                    conn.execute(
                        """
                        INSERT INTO data_source_health
                            (source_name, last_failure_at, consecutive_failures, is_healthy, updated_at)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (source_name) DO UPDATE SET
                            last_failure_at = EXCLUDED.last_failure_at,
                            consecutive_failures = EXCLUDED.consecutive_failures,
                            is_healthy = EXCLUDED.is_healthy,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (source_name, now, new_count, is_healthy, now),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO data_source_health
                            (source_name, last_failure_at, consecutive_failures, is_healthy, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(source_name) DO UPDATE SET
                            last_failure_at = excluded.last_failure_at,
                            consecutive_failures = excluded.consecutive_failures,
                            is_healthy = excluded.is_healthy,
                            updated_at = excluded.updated_at
                        """,
                        (source_name, now.isoformat(), new_count, 0 if not is_healthy else 1, now.isoformat()),
                    )
                conn.commit()

                if new_count >= 3:
                    _discord_alert(
                        f"[MLBPredicts] Data source `{source_name}` has failed "
                        f"{new_count} consecutive times and is marked unhealthy."
                    )
        finally:
            conn.close()
    except Exception as exc:
        log.warning("pipeline_monitor: update_source_health(%s) failed: %s", source_name, exc)


class _RunHandle:
    """Mutable handle yielded by the pipeline_run context manager.
    Jobs set run.records_processed before the context exits."""

    def __init__(self, job_name: str, service_name: str, source: Optional[str]):
        self.job_name = job_name
        self.service_name = service_name
        self.source = source
        self.records_processed: int = 0
        self._run_id: Optional[int] = None
        self._started_at: datetime = _now_utc()
        self._conn = None

    def _open_conn(self):
        from db.database import get_connection
        try:
            self._conn = get_connection()
        except Exception as exc:
            log.warning("pipeline_monitor: could not open DB connection: %s", exc)
            self._conn = None

    def _close_conn(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


@contextmanager
def pipeline_run(
    job_name: str,
    service_name: str = _SERVICE_DEFAULT,
    source: Optional[str] = None,
) -> Generator[_RunHandle, None, None]:
    """Context manager that bookends a pipeline job with pipeline_runs rows.

    Parameters
    ----------
    job_name:     Short identifier for this job (e.g. "schedule_fetch").
    service_name: The Railway service name (e.g. "mlb-data-ingester").
    source:       Optional data source name to update in data_source_health on success.

    Yields a _RunHandle. Set run.records_processed inside the block.

    On clean exit  -> pipeline_runs status='success'
    On exception   -> pipeline_runs status='failed', pipeline_failures row inserted,
                      re-raises the original exception so _safe_run/caller sees it.
    """
    handle = _RunHandle(job_name=job_name, service_name=service_name, source=source)

    # ── open connection and insert 'running' row ──────────────────────────────
    handle._open_conn()
    if handle._conn is not None:
        handle._run_id = _insert_pipeline_run(
            handle._conn, job_name, service_name, handle._started_at
        )

    try:
        yield handle

        # ── success path ──────────────────────────────────────────────────────
        completed_at = _now_utc()
        if handle._conn is not None and handle._run_id is not None:
            try:
                _update_pipeline_run_success(
                    handle._conn,
                    run_id=handle._run_id,
                    completed_at=completed_at,
                    records_processed=handle.records_processed,
                    metadata={"source": source} if source else {},
                )
            except Exception as exc:
                log.warning("pipeline_monitor: success update failed: %s", exc)

    except Exception as job_exc:
        # ── failure path ──────────────────────────────────────────────────────
        completed_at = _now_utc()
        error_type = type(job_exc).__name__
        error_message = str(job_exc)
        stack = traceback.format_exc()

        if handle._conn is not None:
            if handle._run_id is not None:
                try:
                    _update_pipeline_run_failed(
                        handle._conn,
                        run_id=handle._run_id,
                        completed_at=completed_at,
                        records_processed=handle.records_processed,
                        error_message=error_message,
                        metadata={"source": source, "error_type": error_type} if source
                                 else {"error_type": error_type},
                    )
                except Exception as exc:
                    log.warning("pipeline_monitor: failed-update write failed: %s", exc)

            try:
                _insert_pipeline_failure(
                    handle._conn,
                    service_name=service_name,
                    job_name=job_name,
                    error_type=error_type,
                    error_message=error_message,
                    stack_trace=stack,
                    context={"source": source} if source else {},
                    failed_at=completed_at,
                )
            except Exception as exc:
                log.warning("pipeline_monitor: pipeline_failures insert failed: %s", exc)

        # Always re-raise so the job scheduler sees the failure.
        raise

    finally:
        handle._close_conn()
