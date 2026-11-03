"""Read-only task history HTTP API for AI Router v0.8 Phase 4.

The application's existing Nginx Basic Auth must guard this router along with
all other Gateway endpoints. Bind Gateway only on 127.0.0.1, not publicly.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query

from database.history_repository import query_task_summaries
from database.models import TASK_STATUSES
from database.repository import (
    get_messages,
    get_provider_calls,
    get_task,
    get_task_events,
)

router = APIRouter(prefix="/api/tasks", tags=["task-history"])
_TYPE_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


@router.get("")
def list_task_history(
    status: str | None = Query(default=None, max_length=30),
    task_type: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100000),
) -> dict:
    if status is not None and status not in TASK_STATUSES:
        raise HTTPException(status_code=422, detail="invalid task status")
    if task_type is not None and not _TYPE_RE.fullmatch(task_type):
        raise HTTPException(status_code=422, detail="invalid task type")
    return query_task_summaries(
        status=status, task_type=task_type, limit=limit, offset=offset,
    )


@router.get("/{task_id}")
def task_history_detail(task_id: str) -> dict:
    if not _TYPE_RE.fullmatch(task_id) or len(task_id) > 128:
        raise HTTPException(status_code=422, detail="invalid task id")
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return {
        "task": task,
        "messages": get_messages(task_id),
        "events": get_task_events(task_id),
        "provider_calls": get_provider_calls(task_id),
    }
