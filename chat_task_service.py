"""v0.8 Phase 3: persist a single Chat call without changing its Web transport.

Never retries/replays a provider request. Database setup and initial Task+user
message happen BEFORE dispatch. A storage error AFTER a provider call is logged
and reported as a warning without discarding the provider's response.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from database import connection, repository
from database.models import utc_now
from task_manager import task_manager

LOG = logging.getLogger(__name__)
_SCHEMA_LOCK = threading.Lock()


def _ensure_schema() -> None:
    # Initialization is idempotent; serialize it within a Gateway process. Do
    # not cache the DB path: tests and future deployments may change storage.
    with _SCHEMA_LOCK:
        connection.init_db()


def _pre_dispatch(provider: str, action: str, name: str, prompt: str) -> str:
    _ensure_schema()  # A failed DB setup MUST NOT send a prompt.
    task = task_manager.create(
        "chat", prompt, source="web", title=f"{provider}/{action}: {name}",
        metadata={"provider": provider, "transport": "web", "action": action,
                  "conversation_name": name},
    )
    task_id = task["task_id"]
    try:
        repository.save_message(
            task_id, "user", prompt,
            metadata={"provider": provider, "action": action,
                      "conversation_name": name},
        )
        task_manager.start(task_id)
    except Exception:
        try:
            task_manager.fail(task_id, error_code="task_setup_failed",
                              error_message="Task setup failed before provider dispatch")
        except Exception:
            LOG.exception("Could not finalize pre-dispatch failure, task_id=%s", task_id)
        raise
    return task_id


def _record_provider_call(task_id: str, provider: str, action: str, prompt: str,
                          result: dict[str, Any], started_at: str) -> None:
    answer = result.get("answer") or ""
    ok = bool(result.get("success") and isinstance(answer, str) and answer.strip())
    repository.save_provider_call(
        task_id,
        provider=provider,
        transport="web",
        action=action,
        status="done" if ok else "failed",
        duration_ms=result.get("duration_ms"),
        input_length=len(prompt),
        output_length=result.get("answer_length", len(answer)),
        quality_ok=result.get("quality_ok"),
        quality_reason=result.get("quality_reason"),
        conversation_url=result.get("conversation_url") or None,
        error_message=None if ok else (result.get("error") or "provider_call_failed"),
        started_at=started_at, finished_at=utc_now(),
        metadata={"exit_code": result.get("exit_code"),
                  "transport_success": result.get("transport_success"),
                  "wrapper_success": result.get("wrapper_success")},
    )
    if ok:
        repository.save_message(
            task_id, "assistant", answer, provider=provider, transport="web",
            metadata={"conversation_url": result.get("conversation_url") or ""},
        )
        # result_summary is not a fabricated LLM summary or duplicated answer.
        task_manager.complete(task_id, result_summary=f"{provider}: {len(answer)} chars")
    else:
        task_manager.fail(
            task_id, error_code="provider_call_failed",
            error_message=str(result.get("error") or result.get("quality_reason") or "Call failed")[:500],
        )


def execute_chat_with_task(
    provider_call: Callable[[str, str, str, str], dict[str, Any]],
    provider: str, action: str, conversation_name: str, prompt: str,
) -> dict[str, Any]:
    """The only place the Gateway invokes a single Web provider.

    The returned provider fields are unchanged; task_id/history_warning are
    additive. A thrown provider exception follows the existing HTTP error path.
    """
    task_id = _pre_dispatch(provider, action, conversation_name, prompt)
    started_at = utc_now()
    try:
        # Exactly ONE call. Never retry after an ambiguous Web send.
        result = provider_call(provider, action, conversation_name, prompt)
    except Exception as exc:
        try:
            repository.save_provider_call(
                task_id, provider=provider, transport="web", action=action,
                status="failed", input_length=len(prompt), started_at=started_at,
                finished_at=utc_now(), error_message=type(exc).__name__,
            )
            task_manager.fail(
                task_id, error_code="provider_exception",
                error_message=f"Provider raised {type(exc).__name__}",
            )
        except Exception:
            LOG.exception("Failed to record provider exception, task_id=%s", task_id)
        raise

    # The provider has already sent the prompt; never discard an answer and
    # cause an implicit user resend merely because history persistence failed.
    output = dict(result)
    output["task_id"] = task_id
    try:
        _record_provider_call(task_id, provider, action, prompt, output, started_at)
    except Exception as exc:
        LOG.exception("Task history recording failed, task_id=%s", task_id)
        output["history_warning"] = (
            "Task history could not be fully saved; model call was not retried. "
            f"({type(exc).__name__})"
        )
    return output
