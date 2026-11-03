"""SQLite repository for v0.8 Tasks, Messages, Events and ProviderCalls.

No SQL should be added to the HTTP gateway. All writes use transactions;
call database.connection.init_db() once at application startup before use.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator
from uuid import uuid4

from database.connection import get_connection
from database.models import MESSAGE_ROLES, check_transition, utc_now


def _json(value: dict[str, Any] | None) -> str:
    if value is not None and not isinstance(value, dict):
        raise TypeError("metadata/event data must be a dict or None")
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"))


@contextmanager
def _tx() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _require_task(conn: sqlite3.Connection, task_id: str) -> None:
    if conn.execute("SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)).fetchone() is None:
        raise KeyError(f"Task not found: {task_id}")


def create_task(
    task_type: str,
    original_input: str,
    *,
    source: str = "web",
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(task_type, str) or not task_type.strip():
        raise ValueError("task_type is required")
    if not isinstance(original_input, str):
        raise TypeError("original_input must be text")
    task_id = task_id or str(uuid4())
    now = utc_now()
    with _tx() as conn:
        conn.execute(
            """INSERT INTO tasks
               (task_id,task_type,source,status,title,original_input,created_at,updated_at,metadata_json)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (task_id, task_type, source, "queued", title, original_input, now, now, _json(metadata)),
        )
        # Task and initial audit event are committed together.
        conn.execute(
            "INSERT INTO task_events (event_id,task_id,event_type,event_data_json,created_at) "
            "VALUES (?,?,?,?,?)",
            (str(uuid4()), task_id, "task_created", _json({"status": "queued"}), now),
        )
        return _dict(conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())


def get_task(task_id: str) -> dict[str, Any] | None:
    with _tx() as conn:
        return _dict(conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())


def list_tasks(*, status: str | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    if not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be nonnegative")
    query = "SELECT * FROM tasks"
    params: list[Any] = []
    if status is not None:
        from database.models import TASK_STATUSES
        if status not in TASK_STATUSES:
            raise ValueError(f"Unknown task status: {status}")
        query += " WHERE status=?"
        params.append(status)
    query += " ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?"
    params.extend((limit, offset))
    with _tx() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def update_task_status(
    task_id: str,
    new_status: str,
    *,
    result_summary: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """Strict lifecycle transition; terminated tasks cannot be reopened.

    Repeating a transition is not permitted; retrying an operation creates a
    new Task. This method never resends a provider request.
    """
    with _tx() as conn:
        # Lock before reading; lifecycle transitions cannot race each other.
        conn.execute("BEGIN IMMEDIATE")
        row = _get_task_locked(conn, task_id)
        return _transition_locked(
            conn, row, new_status, result_summary=result_summary,
            error_code=error_code, error_message=error_message,
        )



def _get_task_locked(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if row is None:
        raise KeyError(f"Task not found: {task_id}")
    return row


def _event_locked(
    conn: sqlite3.Connection, task_id: str, event_type: str, data: dict[str, Any], now: str,
) -> None:
    conn.execute(
        "INSERT INTO task_events (event_id,task_id,event_type,event_data_json,created_at) "
        "VALUES (?,?,?,?,?)",
        (str(uuid4()), task_id, event_type, _json(data), now),
    )


def _transition_locked(
    conn: sqlite3.Connection, row: sqlite3.Row, new_status: str, *,
    result_summary: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """Caller must hold a write transaction started with BEGIN IMMEDIATE."""
    check_transition(row["status"], new_status)
    now = utc_now()
    started_at = row["started_at"] or (now if new_status == "running" else None)
    finished_at = now if new_status in {"done", "failed", "stopped"} else None
    conn.execute(
        """UPDATE tasks SET status=?,updated_at=?,started_at=?,finished_at=?,
           result_summary=COALESCE(?,result_summary),
           error_code=COALESCE(?,error_code),error_message=COALESCE(?,error_message)
           WHERE task_id=?""",
        (new_status, now, started_at, finished_at, result_summary, error_code, error_message,
         row["task_id"]),
    )
    _event_locked(conn, row["task_id"], "status_changed",
                  {"from": row["status"], "to": new_status}, now)
    return _dict(conn.execute("SELECT * FROM tasks WHERE task_id=?", (row["task_id"],)).fetchone())


def _has_stop_locked(conn: sqlite3.Connection, task_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM task_events WHERE task_id=? AND event_type='stop_requested' LIMIT 1",
        (task_id,),
    ).fetchone() is not None


def is_stop_requested(task_id: str) -> bool:
    """Read durable stop intent. Missing task is an error, not 'False'."""
    with _tx() as conn:
        _require_task(conn, task_id)
        return _has_stop_locked(conn, task_id)


def request_task_stop(task_id: str) -> dict[str, Any]:
    """Atomically persist stop intent and stop an idle task immediately.

    An in-flight provider call cannot be canceled here. For running tasks,
    the worker checks this event at its next safe boundary. Repeated requests
    do not duplicate events. Terminal tasks never accept a new stop request.
    """
    with _tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _get_task_locked(conn, task_id)
        if row["status"] in {"done", "failed", "stopped"}:
            return {"accepted": False, "status": row["status"],
                    "task_id": task_id, "pending": False}
        if not _has_stop_locked(conn, task_id):
            _event_locked(conn, task_id, "stop_requested",
                          {"status_at_request": row["status"]}, utc_now())
        if row["status"] == "running":
            return {"accepted": True, "status": "running",
                    "task_id": task_id, "pending": True}
        # queued / paused / waiting_user have no active call by contract.
        stopped = _transition_locked(conn, row, "stopped")
        return {"accepted": True, "status": stopped["status"],
                "task_id": task_id, "pending": False}


def stop_task_at_checkpoint(task_id: str) -> dict[str, Any]:
    """Stop a running task only after its current provider call has ended."""
    with _tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _get_task_locked(conn, task_id)
        if row["status"] in {"done", "failed", "stopped"}:
            return _dict(row)
        if _has_stop_locked(conn, task_id):
            return _transition_locked(conn, row, "stopped")
        return _dict(row)


def finish_task(task_id: str, *, result_summary: str | None = None) -> dict[str, Any]:
    """Commit done vs stopped atomically with respect to stop requests.

    A stop request already committed before this transaction wins. If the
    operation completed first, a later stop request is rejected as terminal.
    """
    with _tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _get_task_locked(conn, task_id)
        if row["status"] != "running":
            raise ValueError("finish requires a running task")
        target = "stopped" if _has_stop_locked(conn, task_id) else "done"
        return _transition_locked(conn, row, target, result_summary=result_summary)

def save_message(
    task_id: str,
    role: str,
    content: str,
    *,
    conversation_id: str | None = None,
    provider: str | None = None,
    transport: str | None = None,
    metadata: dict[str, Any] | None = None,
    message_id: str | None = None,
) -> str:
    if role not in MESSAGE_ROLES:
        raise ValueError(f"Unknown message role: {role}")
    if not isinstance(content, str):
        raise TypeError("content must be text")
    message_id = message_id or str(uuid4())
    with _tx() as conn:
        # BEGIN IMMEDIATE prevents two writers from assigning the same sequence.
        conn.execute("BEGIN IMMEDIATE")
        _require_task(conn, task_id)
        next_seq = conn.execute(
            "SELECT COALESCE(MAX(sequence_no),0)+1 FROM messages WHERE task_id=?", (task_id,)
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO messages
               (message_id,task_id,conversation_id,role,provider,transport,content,sequence_no,created_at,metadata_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (message_id, task_id, conversation_id, role, provider, transport, content, next_seq, utc_now(), _json(metadata)),
        )
    return message_id


def get_messages(task_id: str) -> list[dict[str, Any]]:
    with _tx() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM messages WHERE task_id=? ORDER BY sequence_no, rowid", (task_id,)
        ).fetchall()]


def save_task_event(
    task_id: str,
    event_type: str,
    *,
    event_data: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> str:
    if not isinstance(event_type, str) or not event_type.strip():
        raise ValueError("event_type is required")
    event_id = event_id or str(uuid4())
    with _tx() as conn:
        _require_task(conn, task_id)
        conn.execute(
            """INSERT INTO task_events (event_id,task_id,event_type,event_data_json,created_at)
               VALUES (?,?,?,?,?)""",
            (event_id, task_id, event_type, _json(event_data), utc_now()),
        )
    return event_id


def get_task_events(task_id: str) -> list[dict[str, Any]]:
    with _tx() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM task_events WHERE task_id=? ORDER BY created_at,rowid", (task_id,)
        ).fetchall()]


def save_provider_call(
    task_id: str,
    *,
    provider: str,
    transport: str,
    action: str,
    status: str,
    duration_ms: int | None = None,
    input_length: int | None = None,
    output_length: int | None = None,
    quality_ok: bool | None = None,
    quality_reason: str | None = None,
    conversation_url: str | None = None,
    error_message: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    metadata: dict[str, Any] | None = None,
    call_id: str | None = None,
) -> str:
    if any(not isinstance(x, str) or not x.strip() for x in (provider, transport, action, status)):
        raise ValueError("provider, transport, action and status are required")
    call_id = call_id or str(uuid4())
    with _tx() as conn:
        _require_task(conn, task_id)
        conn.execute(
            """INSERT INTO provider_calls
               (call_id,task_id,provider,transport,action,status,duration_ms,input_length,
                output_length,quality_ok,quality_reason,conversation_url,error_message,
                started_at,finished_at,metadata_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (call_id, task_id, provider, transport, action, status, duration_ms, input_length,
             output_length, None if quality_ok is None else int(quality_ok), quality_reason,
             conversation_url, error_message, started_at, finished_at, _json(metadata)),
        )
    return call_id


def get_provider_calls(task_id: str) -> list[dict[str, Any]]:
    with _tx() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM provider_calls WHERE task_id=? ORDER BY started_at,rowid", (task_id,)
        ).fetchall()]
