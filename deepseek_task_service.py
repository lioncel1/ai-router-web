"""Audit one DeepSeek API chat in Task Center before dispatching any network request.

Only single-turn Chat is supported. No automatic retries, fallback, API sessions,
or model-driven continuation. An uncertain result remains failed, not replayed.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from database import connection, repository
from database.models import utc_now
from deepseek_api import ALLOWED_MODELS, require_config
from provider_runtime import run_provider
from task_manager import task_manager

LOG = logging.getLogger(__name__)


def execute(prompt: str, model: str = "deepseek-flash") -> dict[str, Any]:
    if model not in ALLOWED_MODELS:
        raise ValueError("unsupported DeepSeek model")
    require_config()  # no task, network or dispatch if unconfigured
    connection.init_db()
    task = task_manager.create(
        "chat", prompt, source="api", title=f"deepseek/api: {model}",
        metadata={"provider": "deepseek", "transport": "api", "model": model,
                  "api_session": "single_turn"},
    )
    tid = task["task_id"]
    try:
        repository.save_message(tid, "user", prompt, provider="deepseek", transport="api")
        task_manager.start(tid)
    except Exception:
        try:
            task_manager.fail(tid, error_code="task_setup_failed", error_message="Failed before API dispatch")
        except Exception:
            LOG.exception("Failed to record setup failure: %s", tid)
        raise

    started_at = utc_now()
    try:
        result = run_provider("deepseek", "new", f"api-{uuid.uuid4().hex}", prompt,
                              transport="api", model=model, timeout=120)
    except Exception as exc:
        # No exception message from an API library is returned/logged (may contain URL/headers).
        result = {"success": False, "answer": "", "answer_length": 0,
                  "error": "api_adapter_exception", "quality_reason": "api_adapter_exception",
                  "quality_ok": False, "transport_success": False,
                  "wrapper_success": False, "duration_ms": None, "model": model,
                  "usage": {}, "exit_code": 1}
        LOG.error("DeepSeek adapter exception (type=%s, task=%s)", type(exc).__name__, tid)

    output = dict(result)
    output["task_id"] = tid
    good = bool(output.get("success") and output.get("answer", "").strip())
    try:
        repository.save_provider_call(
            tid, provider="deepseek", transport="api", action="new",
            status="done" if good else "failed",
            duration_ms=output.get("duration_ms"), input_length=len(prompt),
            output_length=output.get("answer_length", 0), quality_ok=good,
            quality_reason=output.get("quality_reason"),
            error_message=None if good else output.get("error", "api_failed"),
            started_at=started_at, finished_at=utc_now(),
            metadata={"model": model, "usage": output.get("usage", {}),
                      "finish_reason": output.get("finish_reason", "")},
        )
        if good:
            repository.save_message(tid, "assistant", output["answer"],
                                    provider="deepseek", transport="api",
                                    metadata={"model": model})
            task_manager.complete(tid, result_summary=f"deepseek: {len(output['answer'])} chars")
        else:
            task_manager.fail(tid, error_code=output.get("error", "api_failed"),
                              error_message="DeepSeek call did not complete; not automatically retried")
    except Exception:
        LOG.exception("DeepSeek audit write failed: %s", tid)
        output["history_warning"] = "Task history incomplete; API request was not retried"
    return output


def reconcile_interrupted_deepseek_tasks() -> int:
    """At Gateway startup, fail orphaned API tasks; NEVER replay an unknown send."""
    import json
    connection.init_db()
    pending = []
    for status in ("running", "queued"):
        offset = 0
        while True:
            rows = repository.list_tasks(status=status, limit=200, offset=offset)
            for row in rows:
                try:
                    meta = json.loads(row.get("metadata_json") or "{}")
                except (ValueError, TypeError):
                    meta = {}
                if row.get("source") == "api" and meta.get("provider") == "deepseek":
                    pending.append(row["task_id"])
            if len(rows) < 200:
                break
            offset += 200
    count = 0
    for tid in pending:
        try:
            task_manager.fail(tid, error_code="api_interrupted_unknown",
                              error_message="Gateway restarted while API outcome was not confirmed; no replay")
            count += 1
        except ValueError:
            # It may have completed between snapshot and reconciliation.
            pass
    return count
