"""Durable DeepSeek API Chat sessions. No automatic replay or ambiguous retries.

A persisted pending reservation is acquired BEFORE any external API call.
Only confirmed user+assistant pairs are ever used as future model context.
Unknown outcomes permanently quarantine the session until a future manual
reconciliation feature is developed. Original per-call Task Center history is kept.
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from contextlib import closing
from typing import Any

from database import connection, repository
from database.models import utc_now
from deepseek_api import ALLOWED_MODELS, require_config
from provider_runtime import run_provider
from task_manager import task_manager

LOG = logging.getLogger(__name__)
MAX_CONTEXT_CHARS = 100_000


class SessionConflict(ValueError):
    status_code = 409


class SessionMissing(ValueError):
    status_code = 404


def _session_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("Session name must be text")
    name = name.strip()
    if not name or len(name) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("Session name must be 1-80 printable characters")
    return name


def list_sessions() -> list[dict[str, Any]]:
    connection.init_db()
    with closing(connection.get_connection()) as db:
        return [dict(r) for r in db.execute(
            "SELECT name,model,status,created_at,updated_at FROM deepseek_sessions ORDER BY updated_at DESC,name LIMIT 200"
        ).fetchall()]


def read_session(name: str) -> dict[str, Any]:
    connection.init_db()
    name = _session_name(name)
    with closing(connection.get_connection()) as db:
        session = db.execute("SELECT name,model,status,created_at,updated_at FROM deepseek_sessions WHERE name=?", (name,)).fetchone()
        if session is None:
            raise SessionMissing("DeepSeek session not found")
        turns = [dict(r) for r in db.execute(
            "SELECT sequence_no,role,content,task_id FROM deepseek_turns WHERE session_name=? ORDER BY sequence_no", (name,)
        ).fetchall()]
    return {"session": dict(session), "messages": turns}


def _reserve(*, task_id: str, name: str, model: str, action: str, prompt: str) -> list[dict[str, str]]:
    """Serialize competing calls; set pending before any request leaves the server."""
    db = connection.get_connection()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT name,model,status FROM deepseek_sessions WHERE name=?", (name,)).fetchone()
        if action == "new":
            if row is not None:
                raise SessionConflict("Session already exists; choose a new name")
            db.execute("INSERT INTO deepseek_sessions(name,model,status,pending_task_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                       (name, model, "pending", task_id, utc_now(), utc_now()))
            history = []
        elif action == "ask":
            if row is None:
                raise SessionMissing("Select an existing DeepSeek session")
            if row["status"] != "ready":
                raise SessionConflict("DeepSeek session is pending/uncertain; inspect history, never resend")
            if row["model"] != model:
                raise SessionConflict("Session model mismatch; select the existing model")
            history = [dict(r) for r in db.execute(
                "SELECT role,content FROM deepseek_turns WHERE session_name=? ORDER BY sequence_no", (name,)
            ).fetchall()]
            if len(history) % 2 != 0 or (history and history[-1]["role"] != "assistant"):
                raise SessionConflict("DeepSeek confirmed history is inconsistent; do not send")
            db.execute("UPDATE deepseek_sessions SET status='pending',pending_task_id=?,updated_at=? WHERE name=?",
                       (task_id, utc_now(), name))
        else:
            raise ValueError("action must be new or ask")
        messages = history + [{"role": "user", "content": prompt}]
        if sum(len(x["content"]) for x in messages) > MAX_CONTEXT_CHARS:
            # No silent truncation, and never leave a reserved session pending.
            raise SessionConflict("Conversation context too long; no message was sent")
        db.commit()
        return messages
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _commit_confirmed(name: str, task_id: str, prompt: str, answer: str) -> None:
    """Commit the full turn in one transaction; then release the next-send lock."""
    db = connection.get_connection()
    try:
        db.execute("BEGIN IMMEDIATE")
        s = db.execute("SELECT status,pending_task_id FROM deepseek_sessions WHERE name=?", (name,)).fetchone()
        if not s or s["status"] != "pending" or s["pending_task_id"] != task_id:
            raise SessionConflict("API session reservation changed; do not append transcript")
        seq = db.execute("SELECT COALESCE(MAX(sequence_no),0) FROM deepseek_turns WHERE session_name=?", (name,)).fetchone()[0]
        now = utc_now()
        for index, role, content in ((1,"user",prompt),(2,"assistant",answer)):
            db.execute("INSERT INTO deepseek_turns(turn_id,session_name,sequence_no,role,content,task_id,created_at) VALUES(?,?,?,?,?,?,?)",
                       (str(uuid.uuid4()),name,seq+index,role,content,task_id,now))
        db.execute("UPDATE deepseek_sessions SET status='ready',pending_task_id=NULL,updated_at=? WHERE name=?", (now,name))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _quarantine(name: str, task_id: str) -> None:
    with closing(connection.get_connection()) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE deepseek_sessions SET status='uncertain',updated_at=? WHERE name=? AND status='pending' AND pending_task_id=?",
                   (utc_now(),name,task_id))
        db.commit()


def reconcile_interrupted_sessions() -> int:
    """On process startup quarantine all previous pending sends; no network calls."""
    connection.init_db()
    with closing(connection.get_connection()) as db:
        db.execute("BEGIN IMMEDIATE")
        result = db.execute("UPDATE deepseek_sessions SET status='uncertain',updated_at=? WHERE status='pending'", (utc_now(),))
        db.commit()
        return result.rowcount


def execute_session(prompt: str, action: str = "new", name: str = "", model: str = "deepseek-flash") -> dict[str, Any]:
    if model not in ALLOWED_MODELS:
        raise ValueError("unsupported DeepSeek model")
    if action not in {"new", "ask"}:
        raise ValueError("unsupported action")
    if not isinstance(prompt, str) or not prompt.strip() or "\x00" in prompt:
        raise ValueError("prompt must be nonempty text without NUL")
    require_config()
    name = _session_name(name or ("api-" + uuid.uuid4().hex[:16] if action == "new" else ""))
    connection.init_db()
    task = task_manager.create("chat", prompt, source="api", title=f"deepseek/api {action}: {name}",
                               metadata={"provider":"deepseek", "transport":"api", "model":model, "session_name":name})
    tid = task["task_id"]
    try:
        repository.save_message(tid, "user", prompt, provider="deepseek", transport="api")
        task_manager.start(tid)
        messages = _reserve(task_id=tid, name=name, model=model, action=action, prompt=prompt)
    except (SessionConflict, SessionMissing):
        task_manager.fail(tid, error_code="api_session_not_ready", error_message="No API request sent")
        raise
    except Exception:
        try:
            task_manager.fail(tid, error_code="api_session_setup_failed", error_message="Request not sent")
        except Exception:
            LOG.exception("DeepSeek preflight audit failure for %s", tid)
        raise

    started = utc_now()
    try:
        result = run_provider("deepseek", action, name, prompt, transport="api", model=model,
                              messages=messages, timeout=120)
    except Exception as exc:
        LOG.error("DeepSeek API call exception type=%s task=%s", type(exc).__name__, tid)
        result = {"success":False,"answer":"","answer_length":0,"quality_ok":False,
                  "quality_reason":"api_outcome_unknown","error":"api_outcome_unknown",
                  "transport_success":False,"wrapper_success":False,"duration_ms":None,
                  "exit_code":1,"usage":{}}
    output = dict(result)
    output.update({"task_id": tid, "conversation_name":name, "provider":"deepseek", "action":action,
                   "transport":"api", "conversation_url":""})
    good = bool(output.get("success") and isinstance(output.get("answer"), str) and output["answer"].strip())
    try:
        repository.save_provider_call(tid, provider="deepseek", transport="api", action=action,
             status="done" if good else "failed", duration_ms=output.get("duration_ms"),
             input_length=len(prompt), output_length=output.get("answer_length",0), quality_ok=good,
             quality_reason=output.get("quality_reason"),
             error_message=None if good else output.get("error","api_failed"),
             started_at=started, finished_at=utc_now(),
             metadata={"model":model,"session_name":name,"usage":output.get("usage",{})})
        if good:
            repository.save_message(tid, "assistant", output["answer"], provider="deepseek", transport="api",
                                    metadata={"model":model,"session_name":name})
            _commit_confirmed(name, tid, prompt, output["answer"])
            task_manager.complete(tid, result_summary=f"deepseek/api session: {name}")
        else:
            _quarantine(name, tid)
            task_manager.fail(tid, error_code=output.get("error") or "api_failed",
                              error_message="Uncertain API outcome; session quarantined, no automatic replay")
    except Exception:
        # A successful API response without a durable confirmed turn must not be reused.
        LOG.exception("DeepSeek session audit incomplete for task %s", tid)
        output["history_warning"] = "Session history not confirmed; do not resend"
        output["success"] = False
        output["error"] = "api_history_unconfirmed"
        try:
            _quarantine(name, tid)
        except Exception:
            LOG.exception("Could not quarantine DeepSeek session %s", name)
    output["conversation_status"] = "ready" if good and output.get("success") else "uncertain"
    return output


def execute_dialogue_turn(prompt: str, action: str, name: str, model: str,
                          dialogue_task_id: str) -> dict[str, Any]:
    """Use the API session lock for a Dialogue turn without creating a Chat task.

    One logical Dialogue turn -> one provider_call in its parent Dialogue task.
    The reservation is committed before network dispatch. Unknown outcomes
    quarantine the API session and NEVER trigger a retry or Web fallback.
    """
    if not isinstance(prompt, str) or not prompt.strip() or "\x00" in prompt:
        raise ValueError("invalid dialogue prompt")
    if model not in ALLOWED_MODELS or action not in {"new", "ask"}:
        raise ValueError("invalid dialogue API model/action")
    if not isinstance(dialogue_task_id, str) or not dialogue_task_id.strip():
        raise ValueError("dialogue_task_id required")
    require_config()
    name = _session_name(name)
    connection.init_db()
    messages = _reserve(task_id=dialogue_task_id, name=name, model=model,
                        action=action, prompt=prompt)
    try:
        result = run_provider("deepseek", action, name, prompt, transport="api",
                              model=model, messages=messages, timeout=120)
    except Exception as exc:
        LOG.warning("Dialogue DeepSeek API outcome unknown: %s", type(exc).__name__)
        result = {"success": False, "answer": "", "answer_length": 0,
                  "quality_ok": False, "quality_reason": "api_outcome_unknown",
                  "error": "api_outcome_unknown", "transport_success": False,
                  "wrapper_success": False, "duration_ms": None, "usage": {}}
    output = dict(result)
    output.update({"provider": "deepseek", "transport": "api", "model": model,
                   "conversation_name": name, "conversation_url": ""})
    ok = bool(output.get("success") and isinstance(output.get("answer"), str)
              and output["answer"].strip())
    if ok:
        try:
            _commit_confirmed(name, dialogue_task_id, prompt, output["answer"])
        except Exception:
            LOG.exception("Dialogue DeepSeek API confirmation failed for %s", dialogue_task_id)
            _quarantine(name, dialogue_task_id)
            output.update({"success": False, "answer": "", "answer_length": 0,
                           "quality_ok": False, "quality_reason": "api_history_unconfirmed",
                           "error": "api_history_unconfirmed"})
    else:
        _quarantine(name, dialogue_task_id)
    output["conversation_status"] = "ready" if ok and output.get("success") else "uncertain"
    return output
