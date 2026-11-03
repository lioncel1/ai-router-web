"""v0.10 Phase 1: explicit, fail-closed Provider transport dispatch.

No implicit environment-variable override, no API network call, no automatic
fallback or replay. The Web adapter remains the existing v0.9 executor.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

TRANSPORTS = frozenset({"web", "api"})


class TransportUnavailable(RuntimeError):
    """Selected transport cannot accept a request; nothing was dispatched."""


def dispatch_provider(
    provider: str,
    action: str,
    conversation_name: str,
    prompt: str,
    *,
    transport: str,
    web_call: Callable[..., dict[str, Any]],
    api_call: Callable[..., dict[str, Any]] | None = None,
    role: str = "answer",
    task: str | None = None,
    timeout: float = 300,
) -> dict[str, Any]:
    """Dispatch once, with no retries and no automatic Web/API fallback.

    An API call is deliberately unavailable until an adapter has been wired
    and tested in a later phase. In particular, do not silently use Web when
    API was explicitly requested.
    """
    if transport not in TRANSPORTS:
        raise ValueError("unsupported transport; expected 'web' or 'api'")
    if transport == "api":
        if api_call is None:
            raise TransportUnavailable(
                "API transport is not enabled in v0.10 Phase 1; request not sent"
            )
        selected = api_call
    else:
        selected = web_call
    return selected(
        provider, action, conversation_name, prompt,
        role=role, task=task, timeout=timeout,
    )
