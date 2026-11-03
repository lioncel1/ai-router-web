"""v0.9 Phase 2: durable Dialogue history, without Web replay or prompt injection.

Keep the existing provider invocation and turn-passing rules in Gateway. The
repository only captures durable events and never starts a browser or model.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any

from database import connection, repository
from task_manager import task_manager

LOG = logging.getLogger(__name__)
_SCHEMA_LOCK = threading.Lock()
_INTERRUPTED = (
    "Gateway restarted before Dialogue finished; the last Web call outcome "
    "may be uncertain. No message has been resent automatically."
)


def _ensure_schema() -> None:
    with _SCHEMA_LOCK:
        connection.init_db()


def create_dialogue_task(job_id: str, first: str, second: str,
                         prompt: str, max_messages: int) -> str:
    """Create the Task before scheduling any Web request; fail closed."""
    _ensure_schema()
    task_manager.create(
        "dialogue", prompt, source="web", task_id=job_id,
        title=f"Dialogue: {first} → {second} ({max_messages} messages)",
        metadata={"workflow": "dialogue", "job_id": job_id,
                  "first_provider": first, "second_provider": second,
                  "max_messages": max_messages},
    )
    try:
        repository.save_message(job_id, "user", prompt,
                                metadata={"dialogue_index": 0})
    except Exception:
        try:
            task_manager.fail(job_id, error_code="task_setup_failed",
                              error_message="Failed to record initial Dialogue prompt")
        except Exception:
            LOG.exception("Dialogue task setup failure could not be finalized: %s", job_id)
        raise
    return job_id


def start_dialogue_task(job_id: str) -> str:
    """Do not start if the task was stopped while queued."""
    task = task_manager.get(job_id)
    if task is None:
        raise KeyError(job_id)
    if task["status"] == "stopped":
        return "stopped"
    return task_manager.start(job_id)["status"]


def record_dialogue_step(job_id: str, *, index: int, provider: str,
                         action: str, prompt: str, conversation_name: str,
                         result: dict[str, Any]) -> str:
    """Record one finished provider call exactly once, never redispatch it.

    A failed call is recorded without a fabricated assistant response. A
    successful call saves the exact answer as a turn and an event for /status.
    """
    ok = bool(result.get("success") and (result.get("answer") or "").strip())
    answer = (result.get("answer") or "").strip() if ok else ""
    repository.save_provider_call(
        job_id, provider=provider, transport="web", action=action,
        status="done" if ok else "failed",
        duration_ms=result.get("duration_ms"), input_length=len(prompt),
        output_length=result.get("answer_length", len(answer)),
        quality_ok=result.get("quality_ok"),
        quality_reason=result.get("quality_reason"),
        conversation_url=result.get("conversation_url") or None,
        error_message=(result.get("error") or None) if not ok else None,
        metadata={"dialogue_index": index, "conversation_name": conversation_name,
                  "transport_success": result.get("transport_success"),
                  "wrapper_success": result.get("wrapper_success")},
    )
    if not ok:
        return "failed"
    message = {
        "index": index, "provider": provider, "answer": answer,
        "duration_ms": result.get("duration_ms", 0),
        "conversation_name": conversation_name,
        "conversation_url": result.get("conversation_url", ""),
    }
    repository.save_message(
        job_id, "assistant", answer, provider=provider, transport="web",
        metadata={"dialogue_index": index, "conversation_name": conversation_name,
                  "conversation_url": result.get("conversation_url", "")},
    )
    repository.save_task_event(job_id, "dialogue_turn", event_data=message)
    return "done"


def stop_requested(job_id: str) -> bool:
    return task_manager.stop_requested(job_id)


def checkpoint(job_id: str) -> str:
    return task_manager.checkpoint(job_id)["status"]


def complete_dialogue_task(job_id: str) -> str:
    return task_manager.complete(job_id, result_summary="Dialogue messages completed")["status"]


def fail_dialogue_task(job_id: str, *, code: str, reason: str) -> None:
    task = task_manager.get(job_id)
    if task and task["status"] not in {"done", "failed", "stopped"}:
        task_manager.fail(job_id, error_code=code, error_message=reason[:500])


def request_dialogue_stop(job_id: str) -> dict[str, Any]:
    return task_manager.request_stop(job_id)


def read_dialogue_job(job_id: str) -> dict[str, Any] | None:
    task = repository.get_task(job_id)
    if not task or task["task_type"] != "dialogue":
        return None
    try:
        meta = json.loads(task.get("metadata_json") or "{}")
    except (ValueError, TypeError):
        meta = {}
    messages: list[dict[str, Any]] = []
    for event in repository.get_task_events(job_id):
        if event["event_type"] != "dialogue_turn":
            continue
        try:
            item = json.loads(event["event_data_json"])
        except (ValueError, TypeError):
            continue
        if isinstance(item, dict) and item.get("provider") and isinstance(item.get("answer"), str):
            messages.append(item)
    status = task["status"]
    error = task.get("error_message") or ("Dialogue stopped" if status == "stopped" else "")
    return {
        "success": status not in {"failed", "stopped"},
        "job_id": job_id,
        "status": status,
        "first_provider": meta.get("first_provider", ""),
        "second_provider": meta.get("second_provider", ""),
        "max_messages": meta.get("max_messages", 0),
        "completed_messages": len(messages),
        "messages": messages,
        "stop_requested": stop_requested(job_id),
        "current_provider": "",  # Do not guess which Web call was in flight.
        "error": error,
    }


def reconcile_interrupted_dialogue_tasks() -> int:
    """Mark old queued/running Dialogue tasks failed. NEVER replay Web calls."""
    _ensure_schema()
    pending: list[str] = []
    for status in ("queued", "running"):
        offset = 0
        while True:
            batch = repository.list_tasks(status=status, limit=200, offset=offset)
            pending.extend(t["task_id"] for t in batch if t["task_type"] == "dialogue")
            if len(batch) < 200:
                break
            offset += len(batch)
    changed = 0
    for job_id in pending:
        try:
            fail_dialogue_task(job_id, code="gateway_restarted", reason=_INTERRUPTED)
            changed += 1
        except ValueError:
            LOG.warning("Dialogue task changed during restart recovery: %s", job_id)
    return changed
