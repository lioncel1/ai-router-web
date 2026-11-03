"""Task lifecycle coordinator (v0.8 Phase 2).

This module manages *records*, not model execution.  It never sends a
provider request, starts a background worker, or resumes an uncertain Web call.
Call ``checkpoint`` between provider calls and ``complete`` after the final
provider response; both respect persisted stop requests.
"""
from __future__ import annotations

from typing import Any

from database import repository


class TaskManager:
    """Explicit task lifecycle; the repository owns all database access."""

    def create(
        self,
        task_type: str,
        original_input: str,
        *,
        source: str = "web",
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        return repository.create_task(
            task_type, original_input, source=source, title=title,
            metadata=metadata, task_id=task_id,
        )

    def get(self, task_id: str) -> dict[str, Any] | None:
        return repository.get_task(task_id)

    def list(self, *, status: str | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return repository.list_tasks(status=status, limit=limit, offset=offset)

    def start(self, task_id: str) -> dict[str, Any]:
        """Start only a newly queued task; never dispatch a provider call."""
        task = repository.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        if task["status"] != "queued":
            raise ValueError("start requires a queued task; use resume for a paused task")
        return repository.update_task_status(task_id, "running")

    def wait_for_user(self, task_id: str, *, reason: str = "") -> dict[str, Any]:
        """Worker must call at a safe boundary, not during an in-flight call."""
        task = repository.update_task_status(task_id, "waiting_user")
        repository.save_task_event(task_id, "waiting_for_user", event_data={"reason": reason})
        return task

    def pause(self, task_id: str, *, reason: str = "") -> dict[str, Any]:
        """Worker must call at a safe boundary, not during an in-flight call."""
        task = repository.update_task_status(task_id, "paused")
        repository.save_task_event(task_id, "task_paused", event_data={"reason": reason})
        return task

    def resume(self, task_id: str, *, verified_safe: bool = False) -> dict[str, Any]:
        """Re-enter running state only after a human/worker verified it is safe.

        Especially for Web transports, a previously sent prompt must not be
        silently resent. This only updates the persisted state.
        """
        if not verified_safe:
            raise ValueError("resume requires verified_safe=True; do not replay uncertain provider calls")
        task = repository.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        if task["status"] not in {"paused", "waiting_user"}:
            raise ValueError("resume requires a paused or waiting_user task")
        return repository.update_task_status(task_id, "running")

    def request_stop(self, task_id: str) -> dict[str, Any]:
        """Persist stop intent atomically; running calls may finish first.

        The repository immediately stops non-running tasks. For running tasks
        the worker must check ``checkpoint`` or call ``complete`` when done.
        """
        return repository.request_task_stop(task_id)

    def stop_requested(self, task_id: str) -> bool:
        return repository.is_stop_requested(task_id)

    def checkpoint(self, task_id: str) -> dict[str, Any]:
        """At a safe boundary, transition a running task to stopped if asked.

        This does not cancel an in-flight provider request.
        """
        return repository.stop_task_at_checkpoint(task_id)

    def complete(self, task_id: str, *, result_summary: str | None = None) -> dict[str, Any]:
        """Atomically choose done or stopped, honoring earlier stop intent."""
        return repository.finish_task(task_id, result_summary=result_summary)

    def fail(self, task_id: str, *, error_code: str, error_message: str) -> dict[str, Any]:
        """Report an actual failure; no automatic retry or resending."""
        if not error_code:
            raise ValueError("error_code is required")
        return repository.update_task_status(
            task_id, "failed", error_code=error_code, error_message=error_message,
        )


# Importing this module has no database side effects.
task_manager = TaskManager()
