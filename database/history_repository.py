"""Read-only SQLite queries for Task Center v0.8 Phase 4.

Keep history-specific SQL in the repository layer, not in Gateway.
"""
from __future__ import annotations

from database.connection import get_connection


_TASK_LIST_COLS = (
    "task_id,task_type,source,status,title,created_at,updated_at,"
    "started_at,finished_at,error_code"
)


def query_task_summaries(
    *, status: str | None = None, task_type: str | None = None,
    limit: int = 20, offset: int = 0,
) -> dict:
    """List task headers and return matching total, omitting prompt/response bodies."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be 1..100")
    if type(offset) is not int or not 0 <= offset <= 100_000:
        raise ValueError("offset must be 0..100000")
    where = []
    params: list[str] = []
    if status is not None:
        where.append("status = ?")
        params.append(status)
    if task_type is not None:
        where.append("task_type = ?")
        params.append(task_type)
    predicate = " WHERE " + " AND ".join(where) if where else ""
    conn = get_connection()
    try:
        conn.execute("PRAGMA query_only = ON")
        total = conn.execute("SELECT COUNT(*) FROM tasks" + predicate, params).fetchone()[0]
        rows = conn.execute(
            f"SELECT {_TASK_LIST_COLS} FROM tasks" + predicate
            + " ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": total, "limit": limit, "offset": offset,
        }
    finally:
        conn.close()
