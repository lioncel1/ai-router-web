"""DeepSeek multi-turn API sessions: isolated DB, mocked provider; no network calls."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from database import connection, repository
import deepseek_api
import deepseek_session_service as sessions
import gateway_app as gw
import provider_runtime
from install_v12_phase2 import patch_api, patch_runtime, patch_gateway, patch_html


def fake_ok(answer="回答"):
    return {"success":True,"answer":answer,"answer_length":len(answer),"duration_ms":7,
            "quality_ok":True,"quality_reason":"ok","transport_success":True,
            "wrapper_success":True,"model":"deepseek-flash", "usage":{"total_tokens":13},
            "finish_reason":"stop", "exit_code":0}


def test_adapter_sends_confirmed_messages_without_retry(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder")
    messages = [{"role":"user","content":"第一句"},{"role":"assistant","content":"你好"},
                {"role":"user","content":"第二句"}]
    seen=[]
    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"choices":[{"message":{"content":"回答"},"finish_reason":"stop"}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        r=deepseek_api.complete("第二句", client=c, messages=messages)
    assert r["success"]
    assert len(seen)==1
    assert seen[0]["messages"]==messages


def test_unsafe_messages_do_not_send(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder")
    with patch.object(deepseek_api.httpx, "Client") as caller:
        with pytest.raises(ValueError, match="last API message"):
            deepseek_api.complete("different", messages=[{"role":"user", "content":"oops"}])
        caller.assert_not_called()


def test_new_then_ask_uses_confirmed_full_history(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder")
    sent=[]
    def stub(provider, action, name, prompt, **kw):
        sent.append((action, name, kw["messages"]))
        return fake_ok("你好" if action=="new" else "我记得第一句")
    with patch.object(sessions, "run_provider", side_effect=stub):
        a=sessions.execute_session("第一句", "new", "chat-demo")
        b=sessions.execute_session("你记得吗？", "ask", "chat-demo")
    assert a["success"] and b["success"]
    assert sent==[
        ("new", "chat-demo", [{"role":"user", "content":"第一句"}]),
        ("ask", "chat-demo", [{"role":"user", "content":"第一句"},
          {"role":"assistant", "content":"你好"}, {"role":"user", "content":"你记得吗？"}]),
    ]
    item=sessions.read_session("chat-demo")
    assert item["session"]["status"]=="ready"
    assert [m["role"] for m in item["messages"]]==["user","assistant","user","assistant"]
    assert repository.get_task(b["task_id"])["status"]=="done"
    assert repository.get_provider_calls(b["task_id"])[0]["transport"]=="api"
    assert len(repository.get_messages(b["task_id"]))==2


def test_duplicate_new_is_rejected_before_any_model_send(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder")
    with patch.object(sessions, "run_provider", return_value=fake_ok() ) as run:
        sessions.execute_session("a", "new", "dupe")
        with pytest.raises(sessions.SessionConflict):
            sessions.execute_session("b", "new", "dupe")
    assert run.call_count==1


def test_second_call_blocked_while_api_request_pending(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder")
    sent=Event(); release=Event()
    def slow(*args, **kwargs):
        sent.set()
        assert release.wait(3)
        return fake_ok()
    with patch.object(sessions, "run_provider", side_effect=slow) as run:
        with ThreadPoolExecutor(max_workers=2) as pool:
            one=pool.submit(sessions.execute_session,"a","new","wait-test")
            assert sent.wait(3)
            try:
                with pytest.raises(sessions.SessionConflict):
                    sessions.execute_session("b","ask","wait-test")
            finally:
                release.set()
            assert one.result()["success"]
        assert run.call_count==1


def test_unknown_result_quarantines_and_blocks_resend(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder")
    with patch.object(sessions, "run_provider", return_value={
         "success":False,"error":"api_outcome_unknown","quality_reason":"api_outcome_unknown", "answer":""}) as run:
        a=sessions.execute_session("a", "new", "quarantine")
        with pytest.raises(sessions.SessionConflict):
            sessions.execute_session("b", "ask", "quarantine")
    assert run.call_count==1
    assert sessions.read_session("quarantine")["session"]["status"]=="uncertain"
    assert sessions.read_session("quarantine")["messages"]==[]
    assert repository.get_task(a["task_id"])["status"]=="failed"


def test_startup_reconciliation_never_replays(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder")
    connection.init_db()
    task=repository.create_task("chat","any",source="api")
    with connection.get_connection() as db:
        db.execute("INSERT INTO deepseek_sessions(name,model,status,pending_task_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                   ("orphan","deepseek-flash","pending",task["task_id"],"2026","2026"))
    with patch.object(sessions,"run_provider") as send:
        assert sessions.reconcile_interrupted_sessions()==1
        send.assert_not_called()
    assert sessions.read_session("orphan")["session"]["status"]=="uncertain"


def test_model_mismatch_and_missing_name_never_send(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder")
    with patch.object(sessions,"run_provider", return_value=fake_ok()) as run:
        sessions.execute_session("hello","new","fixed")
        with pytest.raises(sessions.SessionConflict):
            sessions.execute_session("hi","ask","fixed","deepseek-v4-pro")
        with pytest.raises(sessions.SessionMissing):
            sessions.execute_session("hi","ask","missing")
    assert run.call_count==1


def test_gateway_routes_mocked(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder")
    with patch.object(gw,"execute_session",return_value={"success":True,"answer":"你好","conversation_name":"x"}) as run:
        with TestClient(gw.app) as client:
            create=client.post('/api/deepseek/session/chat',json={"action":"new", "prompt":"你好", "conversation_name":"x"})
            lst=client.get('/api/deepseek/sessions')
    assert create.status_code==200 and create.json()["answer"]=="你好"
    assert lst.status_code==200 and isinstance(lst.json()["sessions"],list)
    run.assert_called_once_with("你好", "new", "x", "deepseek-flash")


def test_gateway_rejects_unconfigured_before_dispatch(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY",raising=False)
    with patch.object(gw,"execute_session") as run:
        with TestClient(gw.app) as client:
            r=client.post('/api/deepseek/session/chat',json={"prompt":"hello"})
    assert r.status_code==503
    run.assert_not_called()


def test_adapter_respects_dispatch_and_web_unchanged(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "placeholder")
    with patch.object(deepseek_api,"complete",return_value=fake_ok()) as api:
        m=[{"role":"user","content":"hello"}]
        provider_runtime.run_provider("deepseek","ask","api-test","hello",transport="api", messages=m)
        api.assert_called_once_with("hello",model="deepseek-flash",timeout=300,messages=m)
    with patch.object(provider_runtime,"_run_web_provider", return_value={"success":True}) as web:
        assert provider_runtime.run_provider("gemini","new","web","abc")["success"]
        web.assert_called_once()


def test_installer_idempotency_and_ui_changes():
    from pathlib import Path
    base=Path(__file__).resolve().parents[1]
    for f, p in (("deepseek_api.py",patch_api), ("provider_runtime.py",patch_runtime),
                 ("gateway_app.py",patch_gateway), ("templates/index.html",patch_html)):
        text=(base/f).read_text(encoding="utf-8")
        assert p(text)==text
    html=(base/"templates/index.html").read_text(encoding="utf-8")
    for marker in ('value="deepseek"', 'id="deepseekModel"', '/api/deepseek/session/chat', 'provider === \'deepseek\''):
        assert marker in html
