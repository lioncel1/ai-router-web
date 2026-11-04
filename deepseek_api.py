"""DeepSeek Chat Completions HTTP adapter.

No request retries, no Web fallback and no API key logging. Durable API
session lifecycle and confirmed multi-turn history live in deepseek_session_service.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

API_URL = "https://api.deepseek.com/chat/completions"
ALLOWED_MODELS = frozenset({"deepseek-flash", "deepseek-v4-pro"})


class DeepSeekNotConfigured(RuntimeError):
    """Nothing was sent, because the API key was not provided."""


def is_configured() -> bool:
    return bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())


def require_config() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise DeepSeekNotConfigured("DeepSeek API key is not configured; request not sent")
    return key


def _usage(value: Any) -> dict[str, int]:
    obj = value if isinstance(value, dict) else {}
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    return {k: v for k in keys if isinstance((v := obj.get(k)), int) and not isinstance(v, bool) and v >= 0}


def _error_for_status(status: int) -> str:
    return {
        400: "api_invalid_request", 401: "api_auth_failed",
        402: "api_insufficient_balance", 403: "api_forbidden",
        422: "api_invalid_parameters", 429: "api_rate_limited",
    }.get(status, "api_upstream_error" if status >= 500 else "api_http_error")


def complete(prompt: str, *, model: str = "deepseek-flash", timeout: float = 120, client=None, messages: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Send exactly one stateless prompt and return the Provider contract.

    `client` is test-only dependency injection (httpx-compatible). For a timeout
    or transport exception, the server may have received the prompt, so no retry.
    """
    if model not in ALLOWED_MODELS:
        raise ValueError("unsupported DeepSeek model")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must not be empty")
    if not 1 <= timeout <= 600:
        raise ValueError("timeout must be 1..600 seconds")
    # AI_ROUTER_V12_DEEPSEEK_SESSIONS
    payload_messages = messages if messages is not None else [{"role": "user", "content": prompt}]
    if not isinstance(payload_messages, list) or not payload_messages or len(payload_messages) > 500:
        raise ValueError("invalid API messages")
    if any(not isinstance(m, dict) or m.get("role") not in {"user", "assistant"}
           or not isinstance(m.get("content"), str) or not m["content"].strip()
           for m in payload_messages):
        raise ValueError("invalid API message content")
    if payload_messages[-1] != {"role": "user", "content": prompt}:
        raise ValueError("last API message must equal current prompt")
    if sum(len(m["content"]) for m in payload_messages) > 100_000:
        raise ValueError("API context exceeds safety limit; no message sent")
    key = require_config()  # fail before dispatch
    started = time.monotonic()
    owned = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=min(10, timeout)), follow_redirects=False)
    payload = {"model": model, "messages": payload_messages,
               "stream": False, "thinking": {"type": "disabled"}}
    answer, error, reason, finish_reason, usage = "", "", "", "", {}
    transport_ok = False
    wrapper_ok = False
    code = 0
    try:
        # Do not embed the API key in a URL, log or exception message.
        response = http.post(API_URL, headers={"Authorization": f"Bearer {key}",
                                                   "Content-Type": "application/json",
                                                   "Accept": "application/json"}, json=payload)
        code = response.status_code
        transport_ok = 200 <= code < 300
        if not transport_ok:
            error = reason = _error_for_status(code)
        else:
            try:
                data = response.json()
                choices = data.get("choices", []) if isinstance(data, dict) else []
                choice = choices[0] if isinstance(choices, list) and choices else {}
                msg = choice.get("message", {}) if isinstance(choice, dict) else {}
                answer = msg.get("content", "") if isinstance(msg, dict) else ""
                finish_reason = choice.get("finish_reason", "") if isinstance(choice, dict) else ""
                usage = _usage(data.get("usage", {}))
                if not isinstance(answer, str):
                    answer = ""
                wrapper_ok = bool(answer.strip()) and finish_reason == "stop"
                if not wrapper_ok:
                    reason = error = "api_incomplete_response"
            except (ValueError, TypeError, AttributeError, KeyError, IndexError):
                reason = error = "api_invalid_response"
    except (httpx.TimeoutException, httpx.TransportError):
        reason = error = "api_outcome_unknown"
    finally:
        if owned:
            http.close()
    success = transport_ok and wrapper_ok
    return {
        "success": success,
        "transport_success": transport_ok,
        "wrapper_success": wrapper_ok,
        "quality_ok": success,
        "quality_reason": "ok" if success else (reason or "api_failed"),
        "exit_code": 0 if success else (code or 1),
        "answer": answer if success else "",
        "answer_length": len(answer),
        "conversation_url": "", "conversation_status": "stateless",
        "error": "" if success else (error or "api_failed"),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "model": model, "usage": usage, "finish_reason": finish_reason,
    }
