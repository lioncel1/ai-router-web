"""DeepSeek API single-turn tests. Every network call is mocked; no real key used."""
import json
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

import deepseek_api
from database import connection, repository
from deepseek_task_service import execute, reconcile_interrupted_deepseek_tasks
import gateway_app as gw
import provider_runtime
from task_manager import task_manager
from install_v12_deepseek import patch_gateway, patch_runtime


def test_unconfigured_fails_before_network(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert not deepseek_api.is_configured()
    with pytest.raises(deepseek_api.DeepSeekNotConfigured):
        deepseek_api.complete("hello")


def test_mocked_api_answer_model_usage_and_no_retry(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-placeholder")
    attempts = []
    def handle(req):
        attempts.append(req)
        assert req.url == deepseek_api.API_URL
        assert req.headers["Authorization"] == "Bearer sk-test-placeholder"
        body = json.loads(req.content)
        assert body["thinking"] == {"type": "disabled"}
        assert body["model"] == "deepseek-flash"
        return httpx.Response(200, json={"choices": [{"message": {"content": "收到"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        r = deepseek_api.complete("测试", client=client)
    assert r["success"] and r["answer"] == "收到"
    assert r["usage"]["total_tokens"] == 7
    assert len(attempts) == 1


@pytest.mark.parametrize("code,expected", [(401,"api_auth_failed"),(402,"api_insufficient_balance"),(429,"api_rate_limited"),(503,"api_upstream_error")])
def test_http_failure_never_retries(monkeypatch, code, expected):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-placeholder")
    attempts = []
    def handle(req):
        attempts.append(req)
        return httpx.Response(code, json={"error":{"message":"not for logging"}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        r = deepseek_api.complete("hello", client=client)
    assert r["success"] is False and r["error"] == expected
    assert len(attempts) == 1 and "not for logging" not in str(r)


def test_timeout_marked_uncertain_without_retry(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-placeholder")
    calls = []
    def handle(req):
        calls.append(req)
        raise httpx.ReadTimeout("time")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        r = deepseek_api.complete("hello", client=client)
    assert r["error"] == "api_outcome_unknown" and len(calls) == 1


def test_incomplete_response_not_claimed_success(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-placeholder")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
        "choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]}))) as client:
        r = deepseek_api.complete("hello", client=client)
    assert not r["success"] and r["error"] == "api_incomplete_response"


def test_web_contract_still_unchanged_and_other_api_unavailable():
    with patch.object(provider_runtime, "_run_web_provider", return_value={"answer":"web"}) as web:
        assert provider_runtime.run_provider("gemini", "new", "a", "hi")["answer"] == "web"
        assert web.call_count == 1
        with pytest.raises(Exception, match="not enabled|request not sent"):
            provider_runtime.run_provider("chatgpt", "new", "a", "hi", transport="api")
        with pytest.raises(Exception, match="single-turn"):
            provider_runtime.run_provider("deepseek", "new", "a", "hi", transport="web")
        with pytest.raises(Exception, match="single-turn"):
            provider_runtime.run_provider("deepseek", "ask", "a", "hi", transport="api")
        assert web.call_count == 1


def test_provider_runtime_dispatches_single_api_call(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-placeholder")
    with patch.object(deepseek_api, "complete", return_value={"success": True, "answer": "yes"}) as api:
        r = provider_runtime.run_provider("deepseek", "new", "unused", "hello", transport="api", model="deepseek-v4-pro")
    assert r["answer"] == "yes"
    api.assert_called_once_with("hello", model="deepseek-v4-pro", timeout=300)


def test_task_center_persists_api_transport_and_tokens(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-placeholder")
    with patch("deepseek_task_service.run_provider", return_value={
        "success": True, "answer": "收到", "answer_length": 2,
        "quality_reason": "ok", "quality_ok": True, "duration_ms": 11,
        "usage": {"total_tokens": 7}, "model": "deepseek-flash",
    }) as api:
        r = execute("hi")
    api.assert_called_once()
    t = repository.get_task(r["task_id"])
    assert t["status"] == "done" and t["source"] == "api"
    assert [m["role"] for m in repository.get_messages(r["task_id"])] == ["user", "assistant"]
    assert repository.get_provider_calls(r["task_id"])[0]["transport"] == "api"


def test_failed_api_call_saved_without_resend(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-placeholder")
    with patch("deepseek_task_service.run_provider", return_value={
        "success": False, "answer": "", "answer_length": 0,
        "quality_reason": "api_outcome_unknown", "error": "api_outcome_unknown",
        "quality_ok": False, "duration_ms": 100,
    }) as api:
        r = execute("hi")
    assert api.call_count == 1
    assert repository.get_task(r["task_id"])["status"] == "failed"
    assert len(repository.get_messages(r["task_id"])) == 1
    assert repository.get_provider_calls(r["task_id"])[0]["error_message"] == "api_outcome_unknown"


def test_gateway_key_status_and_unconfigured_rejected(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with TestClient(gw.app) as client:
        r = client.get("/api/deepseek/status")
        assert r.json()["configured"] is False
        response = client.post("/api/deepseek/chat", json={"prompt":"hello"})
    assert response.status_code == 503


def test_gateway_configured_calls_mock_only(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-placeholder")
    with patch.object(gw, "execute_deepseek_chat", return_value={"success":True,"answer":"收到"}) as run:
        with TestClient(gw.app) as client:
            r = client.post("/api/deepseek/chat", json={"prompt":"测试", "model":"deepseek-flash"})
    assert r.status_code == 200 and r.json()["answer"] == "收到"
    run.assert_called_once_with("测试", "deepseek-flash")


def test_installer_patch_idempotent():
    base = Path(__file__).resolve().parents[1]
    original = (base / "provider_runtime.py").read_text(encoding="utf-8")
    original2 = (base / "gateway_app.py").read_text(encoding="utf-8")
    assert patch_runtime(patch_runtime(original)) == patch_runtime(original)
    assert patch_gateway(patch_gateway(original2)) == patch_gateway(original2)


def test_restart_marks_orphaned_api_task_failed_without_resend(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-placeholder")
    connection.init_db()
    stale = task_manager.create("chat", "hello", source="api", metadata={"provider":"deepseek", "transport":"api"})
    task_manager.start(stale["task_id"])
    web = task_manager.create("chat", "web", source="web", metadata={"provider":"chatgpt"})
    task_manager.start(web["task_id"])
    with patch("deepseek_task_service.run_provider") as model:
        assert reconcile_interrupted_deepseek_tasks() == 1
        model.assert_not_called()
    assert repository.get_task(stale["task_id"])["status"] == "failed"
    assert repository.get_task(stale["task_id"])["error_code"] == "api_interrupted_unknown"
    assert repository.get_task(web["task_id"])["status"] == "running"
