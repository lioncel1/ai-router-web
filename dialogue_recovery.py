"""Read-only human reconciliation of a Dialogue Web call with an uncertain outcome.

Never sends a prompt. A read-only browser capture is a *candidate* that the user
must explicitly confirm. The failed Task and its provider_calls remain immutable.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from pathlib import Path

from conversation_store import load as load_conversations, save as save_conversations
from database import repository
from dialogue_task_service import ResumeConflict, _lineage, _read_turns, _RESUME_NS

ROOT = Path(__file__).resolve().parent
RECOVERY_LOCK = threading.Lock()
BASE_URL = {"gemini": "https://gemini.google.com/app/", "chatgpt": "https://chatgpt.com/c/"}
CDP = {"gemini": "http://127.0.0.1:9222", "chatgpt": "http://127.0.0.1:9223"}


def _unpack(item):
    try:
        x = json.loads(item or "{}")
        return x if isinstance(x, dict) else {}
    except (TypeError, ValueError):
        return {}


def _candidate(job_id):
    lineage = _lineage(job_id)
    task, meta = lineage[-1]
    if task["status"] != "failed":
        raise ResumeConflict("只允许从失败的 Dialogue 任务核对网页回答")
    root, rmeta = lineage[0]
    first, second = rmeta.get("first_provider"), rmeta.get("second_provider")
    if {first, second} != {"gemini", "chatgpt"}:
        raise ResumeConflict("模型配置无效")
    turns = [t for tsk, _ in lineage for t in _read_turns(tsk["task_id"])]
    expected_index = len(turns) + 1
    expected_provider = (second if turns[-1]["provider"] == first else first) if turns else first

    # After a crash we must not skip an unknown later dispatch.  Every call
    # with this task and an index >= expected must be the *one* unresolved call.
    calls = repository.get_provider_calls(job_id)
    unresolved = [c for c in calls if _unpack(c.get("metadata_json")).get("dialogue_index") == expected_index]
    other = [c for c in calls if _unpack(c.get("metadata_json")).get("dialogue_index", -1) > expected_index]
    intents = [_unpack(e.get("event_data_json")) for e in repository.get_task_events(job_id)
               if e["event_type"] == "dialogue_dispatch_intent"]
    matching = [i for i in intents if i.get("index") == expected_index]
    if len(unresolved) > 1 or len(matching) > 1 or other or any(i.get("index", -1) > expected_index for i in intents):
        raise ResumeConflict("存在多次或后续未确认调用，禁止猜测下一轮")
    if not unresolved and not matching:
        raise ResumeConflict("没有可以核对的未确认模型调用")
    call = unresolved[-1] if unresolved else None
    intent = matching[-1] if matching else {}
    provider = (call["provider"] if call else intent.get("provider"))
    metadata = _unpack(call.get("metadata_json")) if call else {}
    name = metadata.get("conversation_name") or intent.get("conversation_name")
    if provider != expected_provider or provider not in CDP or not isinstance(name, str) or not name:
        raise ResumeConflict("待确认模型与对话轮次不匹配")
    if call and intent and (call["provider"] != intent.get("provider") or name != intent.get("conversation_name")):
        raise ResumeConflict("模型调用与发送检查点不一致")
    if call and call.get("status") == "done" and any(t.get("index") == expected_index for t in turns):
        raise ResumeConflict("该回答已经持久化，不应再次恢复")

    cache_path = ROOT / "data" / f"{provider}_conversations.json"
    try:
        cache = load_conversations(cache_path)
        record = cache.get(name)
    except (OSError, ValueError) as exc:
        raise ResumeConflict("无法读取已有会话缓存") from exc
    if not isinstance(record, dict) or record.get("status") not in {"pending", "done"}:
        raise ResumeConflict("原会话没有待确认响应记录")
    url = record.get("url", "")
    if not isinstance(url, str) or not url.startswith(BASE_URL[provider]):
        raise ResumeConflict("原模型会话 URL 无效")
    actual_prompt = record.get("last_prompt")
    if not isinstance(actual_prompt, str):
        raise ResumeConflict("原模型会话缺少已发送提示词")
    if intent:
        if hashlib.sha256(actual_prompt.encode("utf-8")).hexdigest() != intent.get("prompt_sha256"):
            raise ResumeConflict("待确认提示词与发送检查点不一致，可能已发生后续提问")
    elif actual_prompt != task["original_input"]:
        # Legacy tasks have no dispatch intent. Recover only the first
        # attempted call in this child, whose prompt is stored as input.
        raise ResumeConflict("待确认提示词与原任务不一致，可能已发生后续提问")
    # Check this provider's session has not silently switched from historical URL.
    for turn in reversed(turns):
        if turn["provider"] == provider:
            if turn.get("conversation_name") != name or turn.get("conversation_url") != url:
                raise ResumeConflict("原模型会话与历史记录不一致")
            break
    if call and call.get("conversation_url") and call["conversation_url"] != url:
        raise ResumeConflict("失败调用 URL 与会话缓存不一致")
    return {"job_id": job_id, "source_task": task, "root_id": root["task_id"],
            "first": first, "second": second, "provider": provider,
            "index": expected_index, "conversation_name": name, "url": url,
            "original_prompt": actual_prompt, "record": record, "cache_path": cache_path,
            "next_provider": second if provider == first else first}


def _capture_web(provider, url):
    """Read existing browser conversation. No Enter, click, or model call."""
    from playwright.sync_api import sync_playwright
    from web_response import snapshot, _processing
    from router_log import is_valid_conversation_url
    if not is_valid_conversation_url(url, provider):
        raise ResumeConflict("会话 URL 未通过安全校验")
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(CDP[provider], timeout=15000)
        if not browser.contexts:
            raise ResumeConflict("浏览器没有可用登录上下文")
        context = browser.contexts[0]
        existing = next((p for p in context.pages if p.url.rstrip("/") == url.rstrip("/")), None)
        owned = existing is None
        page = context.new_page() if owned else existing
        try:
            if owned:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
            count, text, complete = snapshot(page, provider)
            if provider == "gemini" and count and not text:
                text = page.locator("model-response").last.inner_text().strip()
            if _processing(page):
                raise ResumeConflict("模型页面仍在生成，等待回答结束后再核对")
            if not count or not text.strip():
                raise ResumeConflict("原网页没有可供确认的模型回答")
            if page.url.rstrip("/") != url.rstrip("/"):
                raise ResumeConflict("模型网页跳转到其他会话，不能进行恢复")
            return {"answer": text.strip(), "response_count": count, "complete": bool(complete)}
        finally:
            if owned:
                page.close()


def preview(job_id):
    c = _candidate(job_id)
    page = _capture_web(c["provider"], c["url"])
    previous = c["record"]
    if int(page["response_count"]) <= int(previous.get("baseline_count") or 0):
        raise ResumeConflict("回答数量没有超过发送前基线，不能确认是本轮回答")
    answer = page["answer"]
    baseline = previous.get("baseline_hash")
    if baseline and hashlib.sha256(answer.encode()).hexdigest() == baseline:
        raise ResumeConflict("当前网页回答与发送前相同，不能确认是本轮回答")
    return {"source_job_id": job_id, "provider": c["provider"], "index": c["index"],
            "next_provider": c["next_provider"], "original_prompt": c["original_prompt"],
            "conversation_url": c["url"], "answer": answer,
            "answer_hash": hashlib.sha256(answer.encode()).hexdigest(),
            "response_count": page["response_count"],
            "requires_confirmation": True,
            "warning": "请在官方网页核对这是本轮对应的最新回答；确认后创建独立恢复记录，不自动转发。"}


def confirm(job_id, answer_hash: str, confirmed: bool):
    if not confirmed or not re.fullmatch(r"[0-9a-f]{64}", answer_hash or ""):
        raise ResumeConflict("需要明确确认网页回答及其完整校验值")
    with RECOVERY_LOCK:
        c = _candidate(job_id)
        current = preview(job_id)  # Fresh read; the page may have changed since preview.
        if current["answer_hash"] != answer_hash:
            raise ResumeConflict("网页回答在核对期间发生变化，请刷新预览重新确认")
        answer = current["answer"]
        new_id = str(uuid.uuid5(_RESUME_NS, job_id + ":confirmed-web-recovery"))
        name = c["conversation_name"]
        cache = load_conversations(c["cache_path"])
        record = cache.get(name)
        if not isinstance(record, dict) or record.get("last_prompt") != c["original_prompt"] or record.get("url") != c["url"] or record.get("status") not in {"pending", "done"}:
            raise ResumeConflict("原会话缓存已变化，请重新核对")
        if repository.get_task(new_id):
            raise ResumeConflict("该失败任务已经创建恢复检查点，不能再次恢复")
        # Commit the recovery Task, recovered answer and audit events in ONE
        # SQLite transaction. Do not pretend a browser call happened here.
        repository.create_dialogue_recovery_checkpoint(
            task_id=new_id, parent_job_id=job_id, root_job_id=c["root_id"],
            first=c["first"], second=c["second"], provider=c["provider"],
            index=c["index"], answer=answer, conversation_name=name,
            conversation_url=c["url"], answer_hash=answer_hash,
        )
        # JSON is separate storage: if this write fails, refuse continuation
        # until explicit recovery / reconciliation, NEVER auto resend.
        record["status"] = "done"
        record["last_answer"] = answer
        record["response_evidence"] = {"body_present": True, "completed": True,
                                       "processing": False, "confirmed_by_user": True}
        cache[name] = record
        try:
            save_conversations(c["cache_path"], cache)
        except Exception as exc:
            raise ResumeConflict("SQLite 已保存恢复结果，但会话缓存写入失败；禁止自动续聊，请检查缓存") from exc
        return {"recovered_job_id": new_id, "source_job_id": job_id,
                "next_provider": c["next_provider"], "index": c["index"],
                "answer": answer, "status": "stopped"}


# Read-only follow-up navigation for an already recovered failure.
# Deterministic IDs are used only to LOCATE existing tasks, never to replay them.
def recovery_status(job_id: str) -> dict:
    task = repository.get_task(job_id)
    if not task or task.get("task_type") != "dialogue":
        raise ResumeConflict("未找到 Dialogue 任务")
    checkpoint_id = str(uuid.uuid5(_RESUME_NS, job_id + ":confirmed-web-recovery"))
    checkpoint = repository.get_task(checkpoint_id)
    if checkpoint is None:
        return {"source_job_id": job_id, "already_recovered": False,
                "checkpoint_job_id": None, "latest_job_id": None,
                "latest_status": None, "can_continue": False}
    meta = _unpack(checkpoint.get("metadata_json"))
    if (checkpoint.get("task_type") != "dialogue" or
            not meta.get("recovered_from_web") or
            meta.get("parent_job_id") != job_id):
        raise ResumeConflict("恢复检查点关联异常，需要人工核对")
    latest = checkpoint
    latest_id = checkpoint_id
    visited = {job_id, checkpoint_id}
    for _ in range(100):
        child_id = str(uuid.uuid5(_RESUME_NS, latest_id))
        if child_id in visited:
            raise ResumeConflict("续聊链路存在循环")
        child = repository.get_task(child_id)
        if child is None:
            break
        child_meta = _unpack(child.get("metadata_json"))
        if child.get("task_type") != "dialogue" or child_meta.get("parent_job_id") != latest_id:
            raise ResumeConflict("后续任务关联异常，需要人工核对")
        visited.add(child_id)
        latest, latest_id = child, child_id
    else:
        raise ResumeConflict("续聊链路过长，需要人工核对")
    return {"source_job_id": job_id, "already_recovered": True,
            "checkpoint_job_id": checkpoint_id, "checkpoint_status": checkpoint["status"],
            "latest_job_id": latest_id, "latest_status": latest["status"],
            "can_continue": latest["status"] in ("done", "stopped")}
