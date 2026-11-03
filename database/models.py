"""Domain constants and validation for v0.8 task persistence.

These values match docs/TASK_MODEL.md and the initial SQLite schema.
The module has no side effects and does not open the database.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

TASK_STATUSES: Final[frozenset[str]] = frozenset(
    {"queued", "running", "waiting_user", "paused", "done", "failed", "stopped"}
)
TERMINAL_STATUSES: Final[frozenset[str]] = frozenset({"done", "failed", "stopped"})

# A completed, failed, or stopped task is immutable. Retry = a new task.
ALLOWED_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "queued": frozenset({"running", "paused", "failed", "stopped"}),
    "running": frozenset({"waiting_user", "paused", "done", "failed", "stopped"}),
    "waiting_user": frozenset({"running", "paused", "failed", "stopped"}),
    "paused": frozenset({"running", "failed", "stopped"}),
    "done": frozenset(),
    "failed": frozenset(),
    "stopped": frozenset(),
}
MESSAGE_ROLES: Final[frozenset[str]] = frozenset({"user", "assistant", "system", "tool"})


def utc_now() -> str:
    """ISO-8601 UTC timestamp, including timezone offset."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_transition(old_status: str, new_status: str) -> None:
    if new_status not in TASK_STATUSES:
        raise ValueError(f"Unknown task status: {new_status}")
    if new_status not in ALLOWED_TRANSITIONS[old_status]:
        raise ValueError(f"Invalid task transition: {old_status} -> {new_status}")
