"""v0.9 Phase 1: durable Auto jobs without modifying provider execution.

The existing orchestrator remains responsible for its own bounded retry policy.
This service never retries, resumes or redispatches an uncertain Web call.
Task ID == Auto job ID so existing /status/{job_id} URLs keep working.
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from database import connection, repository
from task_manager import task_manager

LOG = logging.getLogger(__name__)
_SCHEMA_LOCK = threading.Lock()
_INTERRUPTED_MESSAGE = (
    "Gateway restarted before the Auto result was confirmed. "
    "The Web call outcome is uncertain; nothing was automatically resent."
)


def _ensure_schema() -> None:
    with _SCHEMA_LOCK:
        connection.init_db()


def create_auto_task(prompt: str, job_id: str) -> str:
    """Persist the request BEFORE the background worker can send a prompt."""
    _ensure_schema()
    task_manager.create(
        "auto", prompt, task_id=job_id, source="web",
        title="Auto: analysis → review → synthesis",
        metadata={"workflow": "auto_three_stage", "job_id": job_id},
    )
    try:
        repository.save_message(job_id, "user", prompt, metadata={"workflow": "auto_three_stage"})
    except Exception:
        try:
            task_manager.fail(job_id, error_code="task_setup_failed",
                              error_message="Failed to persist the initial message before dispatch")
        except Exception:
            LOG.exception("Auto setup finalization failed: %s", job_id)
        raise
    return job_id


def _record_stages(job_id: str, result: dict[str, Any]) -> None:
    """Save completed stage evidence. This runs AFTER the orchestrator returns."""
    for index, stage in enumerate(result.get("stages") or [], start=1):
        provider = stage.get("provider") or "unknown"
        role = stage.get("role") or "unknown"
        answer = stage.get("answer") or ""
        for attempt in stage.get("attempts") or []:
            selected = stage.get("selected_attempt") == attempt.get("attempt")
            ok = bool(attempt.get("quality_ok"))
            repository.save_provider_call(
                job_id, provider=provider, transport="web", action="new",
                status="done" if ok else "failed",
                duration_ms=attempt.get("duration_ms"),
                output_length=attempt.get("answer_length"),
                quality_ok=attempt.get("quality_ok"),
                quality_reason=attempt.get("quality_reason"),
                conversation_url=(stage.get("conversation_url") or None) if selected else None,
                error_message=attempt.get("error_summary") or None,
                metadata={
                    "stage_index": index, "role": role, "attempt": attempt.get("attempt"),
                    "selected": selected, "conversation_name": attempt.get("conversation_name"),
                    "transport_success": attempt.get("transport_success"),
                    "wrapper_success": attempt.get("wrapper_success"),
                },
            )
        if stage.get("success") and answer.strip():
            repository.save_message(
                job_id, "assistant", answer, provider=provider, transport="web",
                metadata={"stage_index": index, "role": role,
                          "conversation_url": stage.get("conversation_url") or ""},
            )


def _persist_result(job_id: str, result: dict[str, Any]) -> None:
    _record_stages(job_id, result)
    # Exact status response can be reconstructed on a later Gateway process.
    # Store the original result, including stage breakdown, without fabricating output.
    repository.save_task_event(job_id, "auto_result", event_data={"result": result})
    if result.get("success"):
        task_manager.complete(job_id, result_summary="Auto workflow completed")
    else:
        task_manager.fail(
            job_id, error_code="auto_failed",
            error_message=str(result.get("error") or "Auto workflow failed")[:500],
        )


def execute_auto_task(
    job_id: str, prompt: str,
    workflow: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Execute the *existing* workflow exactly once; preserve answer on DB errors."""
    # A failure to start the persisted task must never dispatch a Web prompt.
    task_manager.start(job_id)
    try:
        result = workflow(prompt)
    except Exception as exc:
        try:
            task_manager.fail(
                job_id, error_code="auto_exception",
                error_message=f"Orchestrator raised {type(exc).__name__}",
            )
        except Exception:
            LOG.exception("Could not record Auto exception: %s", job_id)
        raise

    output = dict(result)
    output["task_id"] = job_id
    try:
        _persist_result(job_id, result)
    except Exception as exc:
        LOG.exception("Auto result history incomplete; no replay: %s", job_id)
        output["history_warning"] = (
            "Auto result history could not be fully saved; no model call was retried. "
            f"({type(exc).__name__})"
        )
    return output


def read_auto_job(job_id: str) -> dict[str, Any] | None:
    """Read a previous job when the Gateway's in-memory cache is empty."""
    task = repository.get_task(job_id)
    if task is None or task["task_type"] != "auto":
        return None
    status = task["status"]
    response: dict[str, Any] = {
        "success": status not in {"failed", "stopped"},
        "job_id": job_id,
        "status": status,
    }
    if status == "failed":
        response["error"] = task["error_message"] or task["error_code"] or "Auto failed"
    elif status == "stopped":
        response["error"] = "Auto stopped"
    if status in {"done", "failed", "stopped"}:
        for event in reversed(repository.get_task_events(job_id)):
            if event["event_type"] == "auto_result":
                try:
                    result = json.loads(event["event_data_json"])["result"]
                except (ValueError, TypeError, KeyError):
                    LOG.warning("Auto result event unreadable, job_id=%s", job_id)
                else:
                    response["result"] = result
                break
    return response


def reconcile_interrupted_auto_tasks() -> int:
    """Mark previous-process queued/running Auto tasks failed at startup.

    Intentionally never replays any Web request. Other task types are untouched.
    Run only once when a new Gateway process starts, before it accepts jobs.
    """
    _ensure_schema()
    pending: list[str] = []
    for status in ("queued", "running"):
        offset = 0
        while True:
            batch = repository.list_tasks(status=status, limit=200, offset=offset)
            pending.extend(t["task_id"] for t in batch if t["task_type"] == "auto")
            if len(batch) < 200:
                break
            offset += len(batch)
    changed = 0
    for job_id in pending:
        try:
            task_manager.fail(job_id, error_code="gateway_restarted",
                              error_message=_INTERRUPTED_MESSAGE)
            changed += 1
        except ValueError:
            # Another process may have finished this task first. Do not overwrite.
            LOG.warning("Auto task already changed during reconciliation: %s", job_id)
    return changed
