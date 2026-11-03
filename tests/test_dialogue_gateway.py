"""Dialogue Gateway integration tests with mocked providers and disposable DB."""
import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import gateway_app as gw
from database import connection, repository


def payload(count=2):
    return {"prompt": "ORIGINAL USER QUESTION", "first_provider": "gemini",
            "second_provider": "chatgpt", "max_messages": count}


def provider_reply(answer):
    return {"success": True, "answer": answer, "quality_ok": True,
            "duration_ms": 11, "conversation_url": "https://chatgpt.com/c/abc"}


def await_status(client, job_id, terminal={"done", "stopped", "failed"}):
    end = time.monotonic() + 4
    while time.monotonic() < end:
        resp = client.get(f"/api/dialogue/status/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in terminal:
            return data
        time.sleep(.025)
    pytest.fail("Dialogue job did not finish")


def test_two_model_responses_and_saved_history():
    calls = []
    def fake(provider, action, name, prompt):
        calls.append((provider, action, name, prompt))
        return provider_reply("ANSWER A" if provider == "gemini" else "ANSWER B")
    connection.init_db()
    with TestClient(gw.app) as client, patch.object(gw, "run_provider", side_effect=fake):
        response = client.post("/api/dialogue/start", json=payload())
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        result = await_status(client, job_id)
        assert result["status"] == "done"
        assert result["completed_messages"] == 2
        assert calls[0][3] == "ORIGINAL USER QUESTION"
        assert calls[1][3] == "ANSWER A"
        assert [c[0] for c in calls] == ["gemini", "chatgpt"]
        assert [c[1] for c in calls] == ["new", "new"]
        with gw.DIALOGUE_JOBS_LOCK:
            del gw.DIALOGUE_JOBS[job_id]
        restored = client.get(f"/api/dialogue/status/{job_id}").json()
        assert restored["status"] == "done"
        assert [m["answer"] for m in restored["messages"]] == ["ANSWER A", "ANSWER B"]
        assert repository.get_task(job_id)["task_type"] == "dialogue"
        assert len(repository.get_provider_calls(job_id)) == 2


def test_stop_after_inflight_reply_no_second_dispatch():
    calls = []
    entered = threading.Event()
    release = threading.Event()
    def fake(provider, action, name, prompt):
        calls.append(provider)
        entered.set()
        if not release.wait(3):
            raise TimeoutError("test provider stuck")
        return provider_reply("ONLY FIRST RESPONSE")
    connection.init_db()
    with TestClient(gw.app) as client, patch.object(gw, "run_provider", side_effect=fake):
        response = client.post("/api/dialogue/start", json=payload(count=3))
        job_id = response.json()["job_id"]
        assert entered.wait(2)
        stopped = client.post(f"/api/dialogue/stop/{job_id}")
        assert stopped.status_code == 200
        assert stopped.json()["stop_requested"] is True
        release.set()
        finished = await_status(client, job_id)
        assert finished["status"] == "stopped"
        assert finished["completed_messages"] == 1
        assert calls == ["gemini"]
        assert repository.get_task(job_id)["status"] == "stopped"


def test_failed_provider_does_not_continue():
    calls = []
    def fake(provider, action, name, prompt):
        calls.append(provider)
        return {"success": False, "answer": "", "quality_ok": False, "error": "quality_failed"}
    connection.init_db()
    with TestClient(gw.app) as client, patch.object(gw, "run_provider", side_effect=fake):
        job_id = client.post("/api/dialogue/start", json=payload()).json()["job_id"]
        finished = await_status(client, job_id)
        assert finished["status"] == "failed"
        assert finished["completed_messages"] == 0
        assert calls == ["gemini"]
        assert repository.get_task(job_id)["error_code"] == "provider_failed"


def test_before_dispatch_storage_failure_503():
    with TestClient(gw.app) as client, patch.object(gw._dts, "create_dialogue_task", side_effect=OSError("offline")), \
            patch.object(gw, "run_provider") as send:
        response = client.post("/api/dialogue/start", json=payload())
        assert response.status_code == 503
        send.assert_not_called()


def test_missing_job_still_404():
    connection.init_db()
    with TestClient(gw.app) as client:
        assert client.get("/api/dialogue/status/nonexistent").status_code == 404
        assert client.post("/api/dialogue/stop/nonexistent").status_code == 404
