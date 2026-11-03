"""Web-safe Dialogue continuation: deliberate, editable, persistent, no automatic replay."""
import json
import time
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from database import connection, repository
import gateway_app as gw
import dialogue_task_service as svc


def response(answer):
    return {"success": True, "answer": answer, "duration_ms": 2,
            "quality_ok": True, "conversation_url": "https://chatgpt.com/c/abc"}


def done(client, task_id):
    for _ in range(100):
        result = client.get(f"/api/dialogue/status/{task_id}")
        assert result.status_code == 200
        if result.json()["status"] in {"done", "stopped", "failed"}:
            return result.json()
        time.sleep(.02)
    pytest.fail("Timed out")


def initial(client, count=2, wait_reply=None):
    r = client.post("/api/dialogue/start", json={
        "prompt": "START", "first_provider": "gemini",
        "second_provider": "chatgpt", "max_messages": count})
    assert r.status_code == 200
    task = r.json()["job_id"]
    assert done(client,task)["status"] == "done"
    return task


def test_resume_edited_prompt_reuses_original_names_and_ask():
    calls = []
    def fake(provider, action, name, prompt):
        calls.append((provider, action, name, prompt))
        return response("G" if provider == "gemini" else "C")
    connection.init_db()
    with TestClient(gw.app) as client, patch.object(gw, "run_provider", side_effect=fake), \
         patch.object(svc, "_verify_existing_conversation"):
        root = initial(client)
        prep = client.get(f"/api/dialogue/prepare/{root}")
        assert prep.status_code == 200
        assert prep.json()["default_prompt"] == "C"
        assert prep.json()["next_provider"] == "gemini"
        assert prep.json()["next_action"] == "ask"
        child = client.post(f"/api/dialogue/continue/{root}", json={
            "prompt": "EDITED TEXT", "additional_messages": 2})
        assert child.status_code == 200
        child_id = child.json()["job_id"]
        result = done(client, child_id)
        assert result["status"] == "done"
        assert result["completed_messages"] == 4
        assert result["max_messages"] == 4
        assert [m["index"] for m in result["messages"]] == [1,2,3,4]
        assert calls[2] == ("gemini", "ask", calls[0][2], "EDITED TEXT")
        assert calls[3] == ("chatgpt", "ask", calls[1][2], "G")
        assert repository.get_task(root)["status"] == "done"
        assert repository.get_task(child_id)["status"] == "done"
        assert len(repository.get_provider_calls(child_id)) == 2
        assert json.loads(repository.get_task(child_id)["metadata_json"])["resume_prompt_edited"] is True
        assert [m["content"] for m in repository.get_messages(child_id)][:1] == ["EDITED TEXT"]
        with gw.DIALOGUE_JOBS_LOCK:
            gw.DIALOGUE_JOBS.pop(child_id, None)
        reloaded = client.get(f"/api/dialogue/status/{child_id}").json()
        assert reloaded["completed_messages"] == 4
        assert reloaded["messages"] == result["messages"]


def test_default_prompt_and_only_one_child_per_parent():
    calls = []
    def fake(provider, action, name, prompt):
        calls.append((provider, action, name, prompt))
        return response("G" if provider == "gemini" else "C")
    connection.init_db()
    with TestClient(gw.app) as client, patch.object(gw, "run_provider", side_effect=fake), \
         patch.object(svc, "_verify_existing_conversation"):
        root = initial(client)
        content = client.get(f"/api/dialogue/prepare/{root}").json()["default_prompt"]
        child = client.post(f"/api/dialogue/continue/{root}", json={
            "prompt": content, "additional_messages": 1})
        assert child.status_code == 200
        assert done(client, child.json()["job_id"])["status"] == "done"
        assert calls[2][-1] == "C"
        again = client.post(f"/api/dialogue/continue/{root}", json={
            "prompt": content, "additional_messages": 1})
        assert again.status_code == 409
        assert len(calls) == 3
        next_child = client.post(f"/api/dialogue/continue/{child.json()['job_id']}", json={
            "prompt": "new next", "additional_messages": 1})
        assert next_child.status_code == 200
        assert done(client, next_child.json()["job_id"])["status"] == "done"
        assert calls[3][0] == "chatgpt"
        assert calls[3][-1] == "new next"


def test_stop_with_one_message_then_resume_other_provider_new():
    calls = []
    def fake(provider, action, name, prompt):
        calls.append((provider, action, name, prompt))
        return response("G")
    connection.init_db()
    with TestClient(gw.app) as client, patch.object(gw, "run_provider", side_effect=fake), \
         patch.object(svc, "_verify_existing_conversation"):
        root = initial(client, count=1)
        assert client.get(f"/api/dialogue/prepare/{root}").json()["next_provider"] == "chatgpt"
        child = client.post(f"/api/dialogue/continue/{root}", json={
            "prompt": "G", "additional_messages": 1})
        assert child.status_code == 200
        assert done(client, child.json()["job_id"])["status"] == "done"
        assert calls[1][0] == "chatgpt"
        assert calls[1][1] == "new"
        assert calls[1][2].endswith('-chatgpt')


def test_invalid_parent_and_missing_cache_never_dispatch():
    connection.init_db()
    with TestClient(gw.app) as client, patch.object(gw, "run_provider", return_value=response("X")) as send:
        assert client.get("/api/dialogue/prepare/missing").status_code == 409
        assert client.post("/api/dialogue/continue/missing", json={
            "prompt": "X", "additional_messages": 1}).status_code == 409
        assert client.post("/api/dialogue/continue/missing", json={
            "prompt": "", "additional_messages": 1}).status_code == 422
        send.assert_not_called()

    with TestClient(gw.app) as client, patch.object(gw, "run_provider", return_value=response("X")) as send:
        root = initial(client, count=2)
        n = send.call_count
        with patch.object(svc, "load_conversations", return_value={}):
            r = client.post(f"/api/dialogue/continue/{root}", json={
                "prompt": "X", "additional_messages": 2})
        assert r.status_code == 409
        assert send.call_count == n
        assert not repository.get_task(str(svc.uuid.uuid5(svc._RESUME_NS, root)))


def test_running_or_failed_parent_forbidden():
    connection.init_db()
    svc.create_dialogue_task("inflight", "gemini", "chatgpt", "abc", 3)
    with TestClient(gw.app) as client:
        r=client.get("/api/dialogue/prepare/inflight")
        assert r.status_code == 409
        r=client.post("/api/dialogue/continue/inflight", json={
            "prompt": "abc", "additional_messages": 2})
        assert r.status_code == 409


def test_resumed_job_can_be_stopped_and_persists_no_further_dispatch():
    import threading
    entered=threading.Event()
    release=threading.Event()
    calls=[]
    def fake(provider, action, name, prompt):
        calls.append(provider)
        if len(calls) == 3:
            entered.set()
            assert release.wait(2)
        return response("G" if provider == "gemini" else "C")
    connection.init_db()
    with TestClient(gw.app) as client, patch.object(gw,"run_provider",side_effect=fake), \
         patch.object(svc, "_verify_existing_conversation"):
        root = initial(client)
        child = client.post(f"/api/dialogue/continue/{root}",json={"prompt":"EDIT","additional_messages":5})
        cid=child.json()["job_id"]
        assert entered.wait(2)
        assert client.post(f"/api/dialogue/stop/{cid}").status_code == 200
        release.set()
        finished=done(client,cid)
        assert finished["status"] == "stopped"
        assert finished["completed_messages"] == 3
        assert calls == ["gemini", "chatgpt", "gemini"]
        with gw.DIALOGUE_JOBS_LOCK:
            gw.DIALOGUE_JOBS.pop(cid,None)
        assert client.get(f"/api/dialogue/status/{cid}").json()["status"] == "stopped"


def test_prepared_view_is_read_only():
    connection.init_db()
    svc.create_dialogue_task("just-one", "gemini", "chatgpt", "Hello", 2)
    svc.start_dialogue_task("just-one")
    svc.record_dialogue_step("just-one", index=1, provider="gemini", action="new",
                             conversation_name="dialogue-just-one-gemini", prompt="Hello",
                             result=response("A"))
    svc.request_dialogue_stop("just-one")
    svc.checkpoint("just-one")
    preview=svc.prepare_dialogue_continuation("just-one")
    assert preview["default_prompt"] == "A"
    assert preview["next_provider"] == "chatgpt"
    assert preview["next_action"] == "new"
    assert repository.get_task("just-one")["status"] == "stopped"
