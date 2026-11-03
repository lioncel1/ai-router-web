"""v0.9 Phase 2: durable Dialogue history, without Web replay or prompt injection.

Keep the existing provider invocation and turn-passing rules in Gateway. The
repository only captures durable events and never starts a browser or model.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path

from conversation_store import load as load_conversations
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


def record_dialogue_dispatch_intent(job_id: str, *, index: int, provider: str,
                                    action: str, conversation_name: str, prompt: str) -> None:
    """Durable send intent before provider invocation; no Web interaction."""
    import hashlib
    repository.save_task_event(job_id, "dialogue_dispatch_intent", event_data={
        "index": index, "provider": provider, "action": action,
        "conversation_name": conversation_name,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    })


def record_dialogue_step(job_id: str, *, index: int, provider: str,
                         action: str, prompt: str, conversation_name: str,
                         result: dict[str, Any]) -> str:
    """Record one finished provider call exactly once, never redispatch it.

    A failed call is recorded without a fabricated assistant response. A
    successful call saves the exact answer as a turn and an event for /status.
    """
    ok = bool(result.get("success") and (result.get("answer") or "").strip())
    answer = (result.get("answer") or "").strip() if ok else ""
    message = {
        "index": index, "provider": provider, "answer": answer,
        "duration_ms": result.get("duration_ms", 0),
        "conversation_name": conversation_name,
        "conversation_url": result.get("conversation_url", ""),
    } if ok else None
    repository.record_dialogue_step_atomic(
        job_id, provider=provider, action=action, status="done" if ok else "failed",
        duration_ms=result.get("duration_ms"), input_length=len(prompt),
        output_length=result.get("answer_length", len(answer)),
        quality_ok=result.get("quality_ok"), quality_reason=result.get("quality_reason"),
        conversation_url=result.get("conversation_url") or None,
        error_message=(result.get("error") or None) if not ok else None,
        metadata={"dialogue_index": index, "conversation_name": conversation_name,
                  "transport_success": result.get("transport_success"),
                  "wrapper_success": result.get("wrapper_success")},
        message=message,
    )
    return "done" if ok else "failed"


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
    messages = _read_turns(job_id)
    parent_id = meta.get("parent_job_id")
    previous = []
    if parent_id:
        try:
            for ancestor_task, _ in _lineage(parent_id):
                previous.extend(_read_turns(ancestor_task["task_id"]))
        except ResumeConflict:
            previous = []  # Historical recovery must not throw or send Web requests.
    messages = previous + messages
    status = task["status"]
    error = task.get("error_message") or ("Dialogue stopped" if status == "stopped" else "")
    return {
        "success": status not in {"failed", "stopped"},
        "job_id": job_id,
        "status": status,
        "first_provider": meta.get("first_provider", ""),
        "second_provider": meta.get("second_provider", ""),
        "max_messages": len(previous) + meta.get("max_messages", 0),
        "completed_messages": len(messages),
        "parent_job_id": parent_id,
        "resume_prompt_edited": meta.get("resume_prompt_edited", False),
        "messages": messages,
        "stop_requested": stop_requested(job_id),
        "current_provider": "",  # Do not guess which Web call was in flight.
        "error": error,
    }


# Dialogue continuation is an explicitly requested NEW Task, never a re-opened
# terminal task.  A deterministic child id makes a repeated POST fail closed
# instead of creating a second live Web request.
_RESUME_NS = uuid.UUID("bce11642-cceb-4c25-ac52-ac220320983a")
_RESUME_LIMIT = 100


class ResumeConflict(ValueError):
    """The source is not resumable, or has already been continued."""


def _read_turns(job_id: str) -> list[dict[str, Any]]:
    turns = []
    for event in repository.get_task_events(job_id):
        if event["event_type"] != "dialogue_turn":
            continue
        try:
            item = json.loads(event["event_data_json"])
        except (TypeError, ValueError):
            continue
        if isinstance(item, dict) and item.get("provider") in {"gemini", "chatgpt"} and isinstance(item.get("answer"), str):
            turns.append(item)
    return turns


def _lineage(job_id: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    lineage = []
    visited = set()
    current = job_id
    while current:
        if current in visited or len(lineage) >= _RESUME_LIMIT:
            raise ResumeConflict("对话续聊链路异常或过长")
        visited.add(current)
        task = repository.get_task(current)
        if not task or task["task_type"] != "dialogue":
            raise ResumeConflict("来源不是有效的 Dialogue 任务")
        try:
            meta = json.loads(task.get("metadata_json") or "{}")
        except (TypeError, ValueError):
            raise ResumeConflict("对话任务元数据无效")
        if not isinstance(meta, dict):
            raise ResumeConflict("对话任务元数据无效")
        lineage.append((task, meta))
        current = meta.get("parent_job_id")
        if current is not None and not isinstance(current, str):
            raise ResumeConflict("父任务引用无效")
    lineage.reverse()
    return lineage


def prepare_dialogue_continuation(job_id: str) -> dict[str, Any]:
    """Return a preview only; never start a provider call."""
    lineage = _lineage(job_id)
    task, meta = lineage[-1]
    if task["status"] not in {"done", "stopped"}:
        raise ResumeConflict("只有已完成或已停止的对话可以继续；失败任务不能自动续跑")
    root_task, root_meta = lineage[0]
    first = root_meta.get("first_provider")
    second = root_meta.get("second_provider")
    if {first, second} != {"gemini", "chatgpt"}:
        raise ResumeConflict("Dialogue 模型配置无效")
    messages = []
    names = {}
    started = set()
    for item_task, _ in lineage:
        for item in _read_turns(item_task["task_id"]):
            provider = item["provider"]
            if provider not in {first, second}:
                raise ResumeConflict("历史模型与当前配置不一致")
            cname = item.get("conversation_name")
            if not isinstance(cname, str) or not cname:
                raise ResumeConflict("历史缺少会话名称，不能安全继续")
            if provider in names and names[provider] != cname:
                raise ResumeConflict("历史会话名称不一致，不能安全继续")
            names[provider] = cname
            started.add(provider)
            messages.append(item)
    if not messages or not messages[-1].get("answer", "").strip():
        raise ResumeConflict("没有可供继续的最后一条有效回答")
    last = messages[-1]
    next_provider = second if last["provider"] == first else first
    for provider in (first, second):
        names.setdefault(provider, f"dialogue-{root_task['task_id'][:8]}-{provider}")
    return {
        "source_job_id": job_id,
        "first_provider": first, "second_provider": second,
        "next_provider": next_provider,
        "last_provider": last["provider"],
        "default_prompt": last["answer"],
        "completed_messages": len(messages),
        "messages": messages,
        "conversation_names": names,
        "started_providers": sorted(started),
        "next_action": "ask" if next_provider in started else "new",
        "root_job_id": root_task["task_id"],
    }


def _verify_existing_conversation(preview: dict[str, Any]) -> None:
    """Check both existing Web sessions before dispatching either provider.

    Loss of either cache is an explicit user-visible conflict, never an
    implicit switch to a fresh conversation or a replay of an old prompt.
    """
    for provider in preview["started_providers"]:
        name = preview["conversation_names"][provider]
        path = Path(__file__).resolve().parent / "data" / f"{provider}_conversations.json"
        try:
            record = load_conversations(path).get(name)
        except (OSError, ValueError) as exc:
            raise ResumeConflict(f"无法读取 {provider} 已有网页会话缓存") from exc
        if not isinstance(record, dict) or not record.get("url"):
            raise ResumeConflict(f"找不到 {provider} 原网页会话，拒绝自动新建或重发")
        previous = next((m for m in reversed(preview["messages"]) if m["provider"] == provider), None)
        if previous and record.get("url") != previous.get("conversation_url"):
            raise ResumeConflict(f"{provider} 网页会话 URL 与历史记录不一致，请人工确认")


def create_dialogue_continuation(parent_job_id: str, prompt: str,
                                 additional_messages: int) -> dict[str, Any]:
    """User-initiated continuation; original task remains terminal and immutable."""
    if not isinstance(additional_messages, int) or not 1 <= additional_messages <= 100:
        raise ValueError("additional_messages must be 1..100")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must not be empty")
    preview = prepare_dialogue_continuation(parent_job_id)
    _verify_existing_conversation(preview)
    new_job_id = str(uuid.uuid5(_RESUME_NS, parent_job_id))
    first, second = preview["first_provider"], preview["second_provider"]
    # Unique task_id ensures double clicks/retried POSTs cannot create two
    # child jobs, even across separate Gateway processes or after a restart.
    _ensure_schema()
    try:
        task_manager.create(
            "dialogue", prompt, source="web", task_id=new_job_id,
            title=f"Dialogue 续聊: {first} → {second} (+{additional_messages})",
            metadata={"workflow": "dialogue", "job_id": new_job_id,
                      "first_provider": first, "second_provider": second,
                      "max_messages": additional_messages,
                      "parent_job_id": parent_job_id,
                      "root_job_id": preview["root_job_id"],
                      "resume_prompt_edited": prompt != preview["default_prompt"]},
        )
    except Exception as exc:
        if repository.get_task(new_job_id):
            raise ResumeConflict("该对话已创建续聊任务，请从任务中心打开最新续聊记录") from exc
        raise
    try:
        repository.save_message(
            new_job_id, "user", prompt,
            metadata={"dialogue_index": preview["completed_messages"],
                      "parent_job_id": parent_job_id, "continuation": True,
                      "edited": prompt != preview["default_prompt"]},
        )
        repository.save_task_event(new_job_id, "dialogue_continued", event_data={
            "parent_job_id": parent_job_id,
            "edited": prompt != preview["default_prompt"],
            "previous_count": preview["completed_messages"],
        })
    except Exception:
        fail_dialogue_task(new_job_id, code="resume_setup_failed",
                           reason="续聊记录写入失败；不允许重新发送")
        raise
    return {"job_id": new_job_id, "preview": preview,
            "additional_messages": additional_messages, "prompt": prompt}


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
