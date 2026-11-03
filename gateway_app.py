"""第一版 Web Gateway：通过现有 router_call.sh 调用 Web Provider。"""

from __future__ import annotations

import asyncio
import threading
import uuid
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

from provider_runtime import run_provider
from orchestrator import run_orchestration
from chat_task_service import execute_chat_with_task
from auto_task_service import (create_auto_task, execute_auto_task,
                               read_auto_job, reconcile_interrupted_auto_tasks)
# AI_ROUTER_AUTO_TASK_V09_PHASE1


BASE_DIR = Path(__file__).resolve().parent
ROUTER_CALL = BASE_DIR / "router_call.sh"
INDEX_FILE = BASE_DIR / "templates" / "index.html"
MAX_CONVERSATION_NAME_LENGTH = 80
MAX_PROMPT_LENGTH = 100_000
CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

app = FastAPI(title="AI Router Gateway", version="0.6.0-rc")


class ChatRequest(BaseModel):
    provider: Literal["gemini", "chatgpt"]
    action: Literal["new", "ask"]
    conversation_name: str = Field(default="", max_length=MAX_CONVERSATION_NAME_LENGTH)
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)

    @field_validator("conversation_name")
    @classmethod
    def validate_conversation_name(cls, value: str) -> str:
        value = value.strip()
        if CONTROL_CHAR_RE.search(value):
            raise ValueError("conversation_name 不能包含控制字符")
        return value

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt 不能为空")
        if "\x00" in value:
            raise ValueError("prompt 不能包含 NUL 字符")
        return value


def make_conversation_name(value: str) -> str:
    if value:
        return value
    return datetime.now().strftime("web-%Y%m%d-%H%M%S-%f")[:-3]


def _run_provider(provider, action, conversation_name, prompt):
    return run_provider(provider, action, conversation_name, prompt)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX_FILE.read_text(encoding="utf-8"))


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "ai-router-gateway"}


@app.post("/api/chat")
async def chat(request: ChatRequest) -> dict:
    conversation_name = make_conversation_name(request.conversation_name)
    if request.action == "ask" and not request.conversation_name.strip():
        raise HTTPException(status_code=400, detail="继续会话时必须选择或填写 Conversation")
    if request.action == "new" and request.conversation_name.strip() and _conversation_exists(request.provider, conversation_name):
        raise HTTPException(status_code=409, detail=f"会话名称已存在：{conversation_name}")
    result = await asyncio.to_thread(
        execute_chat_with_task,
        _run_provider,
        request.provider,
        request.action,
        conversation_name,
        request.prompt,
    )
    return {
        "task_id": result["task_id"],
        "history_warning": result.get("history_warning", ""),
        "success": result["success"],
        "provider": request.provider,
        "action": request.action,
        "conversation_name": conversation_name,
        "exit_code": result["exit_code"],
        "answer": result["answer"],
        "conversation_url": result["conversation_url"],
        "transport_success": result["transport_success"],
        "wrapper_success": result["wrapper_success"],
        "quality_ok": result["quality_ok"],
        "quality_reason": result["quality_reason"],
        "error": result["error"],
        "duration_ms": result["duration_ms"],
    }


class OrchestrateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)

    validate_prompt = field_validator("prompt")(ChatRequest.validate_prompt.__func__)


@app.post("/api/orchestrate")
async def orchestrate(req: OrchestrateRequest):
    job_id = str(uuid.uuid4())
    try:
        await asyncio.to_thread(create_auto_task, req.prompt, job_id)
    except Exception:
        raise HTTPException(status_code=503, detail="Auto task storage unavailable; request not sent")
    return await asyncio.to_thread(execute_auto_task, job_id, req.prompt, run_orchestration)


# Browser-friendly async orchestration jobs. The existing /api/orchestrate
# endpoint remains synchronous for API/backward compatibility.
ORCHESTRATION_JOBS: dict[str, dict] = {}
ORCHESTRATION_JOBS_LOCK = threading.Lock()
ORCHESTRATION_TASKS: set[asyncio.Task] = set()

@app.on_event("startup")
def _auto_task_startup_recovery():
    """Close uncertain Auto jobs from a prior Gateway process, without replay."""
    import logging
    try:
        count = reconcile_interrupted_auto_tasks()
        if count:
            logging.getLogger(__name__).warning("Marked %d interrupted Auto tasks", count)
    except Exception:
        logging.getLogger(__name__).exception("Auto task startup recovery failed")


def _run_orchestration_job(job_id: str, prompt: str) -> None:
    with ORCHESTRATION_JOBS_LOCK:
        ORCHESTRATION_JOBS[job_id] = {"status": "running"}
    try:
        result = execute_auto_task(job_id, prompt, run_orchestration)
        status = "done" if result.get("success", True) else "failed"
        with ORCHESTRATION_JOBS_LOCK:
            ORCHESTRATION_JOBS[job_id] = {
                "status": status,
                "result": result,
                "error": result.get("error", ""),
            }
    except Exception as exc:
        with ORCHESTRATION_JOBS_LOCK:
            ORCHESTRATION_JOBS[job_id] = {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }


@app.post("/api/orchestrate/start")
async def orchestrate_start(req: OrchestrateRequest):
    job_id = str(uuid.uuid4())
    try:
        await asyncio.to_thread(create_auto_task, req.prompt, job_id)
    except Exception:
        raise HTTPException(status_code=503, detail="Auto task storage unavailable; request not sent")
    with ORCHESTRATION_JOBS_LOCK:
        ORCHESTRATION_JOBS[job_id] = {"status": "queued"}
    task = asyncio.create_task(
        asyncio.to_thread(_run_orchestration_job, job_id, req.prompt)
    )
    ORCHESTRATION_TASKS.add(task)
    task.add_done_callback(ORCHESTRATION_TASKS.discard)
    return {"success": True, "job_id": job_id, "status": "queued"}


@app.get("/api/orchestrate/status/{job_id}")
async def orchestrate_job_status(job_id: str):
    with ORCHESTRATION_JOBS_LOCK:
        job = ORCHESTRATION_JOBS.get(job_id)
        data = dict(job) if job is not None else None
    if data is None:
        persisted = await asyncio.to_thread(read_auto_job, job_id)
        if persisted is not None:
            return persisted
        return {
            "success": False,
            "job_id": job_id,
            "status": "missing",
            "error": "job not found",
        }
    return {
        "success": data.get("status") != "failed",
        "job_id": job_id,
        **data,
    }


# === conversation management v0.7 ===
from datetime import datetime as _conversation_datetime
from pathlib import Path as _ConversationPath
from urllib.parse import urlparse as _conversation_urlparse
from fastapi import HTTPException
import conversation_store as _conversation_store

_CONVERSATION_DATA_DIR = _ConversationPath(__file__).resolve().parent / "data"


def _conversation_db_path(provider: str):
    provider = (provider or "").strip().lower()
    if provider not in {"gemini", "chatgpt"}:
        raise HTTPException(status_code=400, detail="provider 必须是 gemini 或 chatgpt")
    return _CONVERSATION_DATA_DIR / f"{provider}_conversations.json"


def _conversation_exists(provider: str, conversation_name: str) -> bool:
    data = _conversation_store.load(_conversation_db_path(provider))
    return conversation_name in data


def _validate_conversation_url(provider: str, url: str) -> str:
    url = (url or "").strip()
    parsed = _conversation_urlparse(url)
    if parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="会话 URL 必须使用 https://")
    if provider == "gemini":
        valid = parsed.netloc == "gemini.google.com" and parsed.path.startswith("/app/") and len(parsed.path) > len("/app/")
    else:
        valid = parsed.netloc == "chatgpt.com" and re.search(r"(?:^|/)c/[^/]+/?$", parsed.path) is not None
    if not valid:
        expected = "https://gemini.google.com/app/..." if provider == "gemini" else "https://chatgpt.com/c/..."
        raise HTTPException(status_code=400, detail=f"URL 与 provider 不匹配，应类似：{expected}")
    return url


class ConversationImportRequest(BaseModel):
    provider: str
    conversation_name: str = Field(min_length=1, max_length=MAX_CONVERSATION_NAME_LENGTH)
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("conversation_name")
    @classmethod
    def validate_import_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("conversation_name 不能为空")
        if any(ord(ch) < 32 for ch in value):
            raise ValueError("conversation_name 不能包含控制字符")
        return value


@app.get("/api/conversations")
def api_conversations(provider: str):
    provider = (provider or "").strip().lower()
    data = _conversation_store.load(_conversation_db_path(provider))
    items = []
    for name, record in data.items():
        if not isinstance(record, dict):
            continue
        items.append({
            "name": name,
            "status": record.get("status", "done"),
            "url": record.get("url", ""),
            "updated_at": record.get("updated_at", ""),
        })
    items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return {"provider": provider, "conversations": items}


@app.post("/api/conversations/import")
def api_import_conversation(request: ConversationImportRequest):
    provider = request.provider.strip().lower()
    path = _conversation_db_path(provider)
    data = _conversation_store.load(path)
    name = request.conversation_name.strip()
    if name in data:
        raise HTTPException(status_code=409, detail=f"会话名称已存在：{name}")
    url = _validate_conversation_url(provider, request.url)
    for existing_name, record in data.items():
        if isinstance(record, dict) and record.get("url") == url:
            raise HTTPException(status_code=409, detail=f"该 URL 已绑定到会话：{existing_name}")
    data[name] = {
        "url": url,
        "status": "done",
        "updated_at": _conversation_datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _conversation_store.save(path, data)
    return {"ok": True, "provider": provider, "conversation_name": name, "url": url}


# dialogue mode v0.7
import dialogue_task_service as _dts
# AI_ROUTER_DIALOGUE_TASK_V09_PHASE2

@app.on_event("startup")
def _dialogue_task_startup_recovery():
    import logging
    try:
        count = _dts.reconcile_interrupted_dialogue_tasks()
        if count:
            logging.getLogger(__name__).warning("Marked %d interrupted Dialogue tasks", count)
    except Exception:
        logging.getLogger(__name__).exception("Dialogue startup recovery failed")

class DialogueRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)
    first_provider: Literal["gemini", "chatgpt"]
    second_provider: Literal["gemini", "chatgpt"]
    max_messages: int = Field(default=6, ge=1, le=100)

    validate_prompt = field_validator("prompt")(ChatRequest.validate_prompt.__func__)


DIALOGUE_JOBS: dict[str, dict] = {}
DIALOGUE_JOBS_LOCK = threading.Lock()
DIALOGUE_TASKS: set[asyncio.Task] = set()


def _run_dialogue_job(
    job_id: str,
    first_provider: str,
    second_provider: str,
    prompt: str,
    max_messages: int,
) -> None:
    # Keep original v0.7 routing: raw user prompt to A, then exact previous
    # model answer to the other provider. No hidden system or role injection.
    names = {
        first_provider: f"dialogue-{job_id[:8]}-{first_provider}",
        second_provider: f"dialogue-{job_id[:8]}-{second_provider}",
    }
    started: set[str] = set()
    current = first_provider
    incoming = prompt

    def end(status: str, error: str = "") -> None:
        with DIALOGUE_JOBS_LOCK:
            job = DIALOGUE_JOBS[job_id]
            job["status"] = status
            job["current_provider"] = ""
            job["error"] = error

    # A queued task may have been stopped before this worker was scheduled.
    try:
        state = _dts.start_dialogue_task(job_id)
    except Exception as exc:
        end("failed", f"Task start failed: {type(exc).__name__}")
        return
    if state == "stopped":
        end("stopped")
        return

    with DIALOGUE_JOBS_LOCK:
        DIALOGUE_JOBS[job_id]["status"] = "running"
        DIALOGUE_JOBS[job_id]["current_provider"] = current

    for index in range(max_messages):
        try:
            if _dts.checkpoint(job_id) == "stopped":
                end("stopped")
                return
        except Exception as exc:
            end("failed", f"Task checkpoint failed: {type(exc).__name__}")
            return

        with DIALOGUE_JOBS_LOCK:
            DIALOGUE_JOBS[job_id]["current_provider"] = current

        other = second_provider if current == first_provider else first_provider
        provider_prompt = incoming
        action = "ask" if current in started else "new"
        try:
            result = run_provider(current, action, names[current], provider_prompt)
        except Exception as exc:
            error = f"{current} 调用异常：{type(exc).__name__}"
            try:
                _dts.fail_dialogue_task(job_id, code="provider_exception", reason=error)
            except Exception:
                pass
            end("failed", error)
            return

        try:
            recorded = _dts.record_dialogue_step(
                job_id, index=index + 1, provider=current, action=action,
                prompt=provider_prompt, conversation_name=names[current], result=result,
            )
        except Exception as exc:
            # The call has already happened. Stop at this boundary; do not
            # send to the other model when we cannot persist the result.
            error = f"Dialogue history write failed: {type(exc).__name__}; no retry"
            try:
                _dts.fail_dialogue_task(job_id, code="history_write_failed", reason=error)
            except Exception:
                pass
            end("failed", error)
            return

        if recorded != "done":
            error = (result.get("error") or result.get("quality_reason")
                     or f"{current} 未返回有效回答")
            try:
                _dts.fail_dialogue_task(job_id, code="provider_failed", reason=error)
            except Exception:
                pass
            end("failed", error)
            return

        # Completed and checked model output; only this exact answer may be
        # forwarded as the next provider prompt.
        answer = (result.get("answer") or "").strip()
        started.add(current)
        incoming = answer
        message = {
            "index": index + 1, "provider": current, "answer": answer,
            "duration_ms": result.get("duration_ms", 0),
            "conversation_name": names[current],
            "conversation_url": result.get("conversation_url", ""),
        }
        with DIALOGUE_JOBS_LOCK:
            job = DIALOGUE_JOBS[job_id]
            job["messages"].append(message)
            job["completed_messages"] = len(job["messages"])

        try:
            if _dts.checkpoint(job_id) == "stopped":
                end("stopped")
                return
        except Exception as exc:
            end("failed", f"Task checkpoint failed: {type(exc).__name__}")
            return
        current = other

    try:
        final = _dts.complete_dialogue_task(job_id)
    except Exception as exc:
        end("failed", f"Task completion failed: {type(exc).__name__}")
        return
    end(final)


@app.post("/api/dialogue/start")
async def dialogue_start(req: DialogueRequest):
    if req.first_provider == req.second_provider:
        raise HTTPException(status_code=400, detail="发问模型和对话模型不能相同")

    job_id = str(uuid.uuid4())
    try:
        await asyncio.to_thread(_dts.create_dialogue_task, job_id, req.first_provider,
                                req.second_provider, req.prompt, req.max_messages)
    except Exception:
        raise HTTPException(status_code=503, detail="Dialogue task storage unavailable; request not sent")
    with DIALOGUE_JOBS_LOCK:
        DIALOGUE_JOBS[job_id] = {
            "status": "queued",
            "first_provider": req.first_provider,
            "second_provider": req.second_provider,
            "max_messages": req.max_messages,
            "completed_messages": 0,
            "messages": [],
            "stop_requested": False,
            "current_provider": "",
            "error": "",
        }

    task = asyncio.create_task(
        asyncio.to_thread(
            _run_dialogue_job,
            job_id,
            req.first_provider,
            req.second_provider,
            req.prompt,
            req.max_messages,
        )
    )
    DIALOGUE_TASKS.add(task)
    task.add_done_callback(DIALOGUE_TASKS.discard)
    return {"success": True, "job_id": job_id, "status": "queued"}


@app.get("/api/dialogue/status/{job_id}")
async def dialogue_status(job_id: str):
    with DIALOGUE_JOBS_LOCK:
        job = DIALOGUE_JOBS.get(job_id)
        if job is None:
            data = None
        else:
            data = dict(job)
            data["messages"] = [dict(item) for item in job.get("messages", [])]
    if data is None:
        data = await asyncio.to_thread(_dts.read_dialogue_job, job_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Dialogue job not found")
        return data
    return {"success": data.get("status") not in {"failed", "stopped"}, "job_id": job_id, **data}


@app.post("/api/dialogue/stop/{job_id}")
async def dialogue_stop(job_id: str):
    # Persist stop intent first. Do not cancel the in-flight Web request.
    try:
        outcome = await asyncio.to_thread(_dts.request_dialogue_stop, job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Dialogue job not found")
    except Exception:
        raise HTTPException(status_code=503, detail="Dialogue stop could not be persisted")
    with DIALOGUE_JOBS_LOCK:
        job = DIALOGUE_JOBS.get(job_id)
        if job is not None and outcome["accepted"]:
            job["stop_requested"] = True
            if outcome["status"] == "stopped":
                job["status"] = "stopped"
                job["current_provider"] = ""
    return {
        "success": True, "job_id": job_id,
        "status": outcome["status"],
        "stop_requested": bool(outcome["accepted"]),
    }


# AI_ROUTER_TASK_HISTORY_PHASE4
from task_history_api import router as _phase4_task_history_router
app.include_router(_phase4_task_history_router)
