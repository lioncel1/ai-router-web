"""Crash boundary and read-only recovery tests. No real browser or model calls."""
import hashlib
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from database import connection, repository
from conversation_store import load, save
import dialogue_task_service as svc
import dialogue_recovery as rec
import gateway_app as gw
from web_response import snapshot, wait_for_answer

GURL = "https://gemini.google.com/app/11223344"
CURL = "https://chatgpt.com/c/11223344"


def good(answer, url):
    return {"success": True, "answer": answer, "duration_ms": 10,
            "quality_ok": True, "quality_reason": "ok", "conversation_url": url}


def seed_failed(tmp_path, monkeypatch, *, future=False):
    connection.init_db()
    monkeypatch.setattr(rec, "ROOT", tmp_path)
    task = "root-reliability"
    svc.create_dialogue_task(task, "gemini", "chatgpt", "first question", 2)
    svc.start_dialogue_task(task)
    svc.record_dialogue_step(task, index=1, provider="gemini", action="new",
                             conversation_name="dialogue-root-gemini", prompt="first question",
                             result=good("G1", GURL))
    svc.record_dialogue_step(task, index=2, provider="chatgpt", action="new",
                             conversation_name="dialogue-root-chatgpt", prompt="G1",
                             result=good("C2", CURL))
    svc.complete_dialogue_task(task)
    with patch.object(svc, "_verify_existing_conversation"):
        child = svc.create_dialogue_continuation(task, "天气", 2)["job_id"]
    svc.start_dialogue_task(child)
    svc.record_dialogue_dispatch_intent(child, index=3, provider="gemini", action="ask",
                                       conversation_name="dialogue-root-gemini", prompt="天气")
    if future:
        svc.record_dialogue_step(child, index=3, provider="gemini", action="ask",
                                 conversation_name="dialogue-root-gemini", prompt="天气",
                                 result=good("G3", GURL))
        svc.record_dialogue_dispatch_intent(child, index=4, provider="chatgpt", action="ask",
                                           conversation_name="dialogue-root-chatgpt", prompt="G3")
        url, provider, name, prompt, index = CURL, "chatgpt", "dialogue-root-chatgpt", "G3", 4
    else:
        url, provider, name, prompt, index = GURL, "gemini", "dialogue-root-gemini", "天气", 3
    svc.record_dialogue_step(child, index=index, provider=provider, action="ask",
                             conversation_name=name, prompt=prompt,
                             result={"success": False, "answer": "", "quality_ok": False,
                                     "quality_reason": "transport_failed", "error": "transport_failed"})
    svc.fail_dialogue_task(child, code="provider_failed", reason="transport_failed")
    record = {"url": url, "status": "pending", "last_prompt": prompt, "last_answer": "",
              "baseline_count": 0, "baseline_hash": hashlib.sha256(b"previous").hexdigest()}
    save(tmp_path / "data" / f"{provider}_conversations.json", {name: record})
    return child, provider, name


def capture(answer="天气：多云，15°C", count=3):
    return {"answer": answer, "response_count": count, "complete": False}


def test_recovered_answer_is_checkpoint_not_a_second_model_call(tmp_path, monkeypatch):
    job, provider, name = seed_failed(tmp_path, monkeypatch)
    with patch.object(rec, "_capture_web", return_value=capture()) as browser, \
         patch.object(gw, "run_provider") as send, TestClient(gw.app) as client:
        p = client.get(f"/api/dialogue/recover/{job}")
        assert p.status_code == 200
        body = p.json()
        assert body["provider"] == "gemini"
        assert body["index"] == 3
        assert body["next_provider"] == "chatgpt"
        assert body["answer"] == "天气：多云，15°C"
        assert client.get(f"/api/dialogue/prepare/{job}").status_code == 409
        before = repository.get_provider_calls(job)
        c = client.post(f"/api/dialogue/recover/{job}", json={
            "answer_hash": body["answer_hash"], "confirmed": True})
        assert c.status_code == 200
        checkpoint = c.json()["recovered_job_id"]
        assert repository.get_task(job)["status"] == "failed"
        assert repository.get_provider_calls(job) == before
        assert repository.get_task(checkpoint)["status"] == "stopped"
        assert not repository.get_provider_calls(checkpoint)  # no fictitious dispatch
        assert repository.get_messages(checkpoint)[0]["content"] == "天气：多云，15°C"
        assert [e["event_type"] for e in repository.get_task_events(checkpoint)] == [
            "task_created", "dialogue_turn", "web_answer_recovered"]
        resume = client.get(f"/api/dialogue/prepare/{checkpoint}")
        assert resume.status_code == 200
        assert resume.json()["next_provider"] == "chatgpt"
        assert resume.json()["default_prompt"] == "天气：多云，15°C"
        assert resume.json()["completed_messages"] == 3
        assert load(tmp_path / "data/gemini_conversations.json")[name]["status"] == "done"
        again = client.post(f"/api/dialogue/recover/{job}", json={
            "answer_hash": body["answer_hash"], "confirmed": True})
        assert again.status_code == 409
        browser.assert_called()
        send.assert_not_called()


def test_confirmation_rejects_changed_content_and_no_automatic_sends(tmp_path, monkeypatch):
    job, _, name = seed_failed(tmp_path, monkeypatch)
    with patch.object(rec, "_capture_web", return_value=capture()), TestClient(gw.app) as client:
        p = client.get(f"/api/dialogue/recover/{job}").json()
        assert client.post(f"/api/dialogue/recover/{job}", json={
            "answer_hash": p["answer_hash"], "confirmed": False}).status_code == 409
        with patch.object(rec, "_capture_web", return_value=capture("另一个回答")):
            assert client.post(f"/api/dialogue/recover/{job}", json={
                "answer_hash": p["answer_hash"], "confirmed": True}).status_code == 409
        assert load(tmp_path / "data/gemini_conversations.json")[name]["status"] == "pending"
        assert repository.get_task(str(svc.uuid.uuid5(svc._RESUME_NS, job + ":confirmed-web-recovery"))) is None


def test_incomplete_snapshot_or_mismatched_cache_refuses_preview(tmp_path, monkeypatch):
    job, _, name = seed_failed(tmp_path, monkeypatch)
    with patch.object(rec, "_capture_web", return_value=capture(count=0)):
        with pytest.raises(svc.ResumeConflict, match="数量"):
            rec.preview(job)
    path = tmp_path / "data/gemini_conversations.json"
    cache = load(path)
    cache[name]["last_prompt"] = "不同的提问"
    save(path, cache)
    with patch.object(rec, "_capture_web", return_value=capture()):
        with pytest.raises(svc.ResumeConflict, match="提示词"):
            rec.preview(job)


def test_recovery_targets_unconfirmed_next_call_not_last_confirmed_turn(tmp_path, monkeypatch):
    job, _, name = seed_failed(tmp_path, monkeypatch, future=True)
    with patch.object(rec, "_capture_web", return_value=capture("C4")):
        p = rec.preview(job)
        assert p["index"] == 4
        assert p["provider"] == "chatgpt"
        assert p["next_provider"] == "gemini"
        result = rec.confirm(job, p["answer_hash"], True)
    next_step = svc.prepare_dialogue_continuation(result["recovered_job_id"])
    assert [x["provider"] for x in next_step["messages"]] == ["gemini", "chatgpt", "gemini", "chatgpt"]
    assert next_step["next_provider"] == "gemini"
    assert next_step["default_prompt"] == "C4"


def test_dispatch_intent_failure_blocks_provider_send():
    with TestClient(gw.app) as client, patch.object(svc, "record_dialogue_dispatch_intent", side_effect=OSError("disk")), \
         patch.object(gw, "run_provider") as send:
        r = client.post("/api/dialogue/start", json={"prompt":"Q", "first_provider":"gemini", "second_provider":"chatgpt", "max_messages":1})
        assert r.status_code == 200
        import time
        for _ in range(100):
            state = client.get("/api/dialogue/status/" + r.json()["job_id"]).json()
            if state["status"] == "failed":
                break
            time.sleep(.01)
        assert state["status"] == "failed"
        send.assert_not_called()


def test_atomic_outcome_does_not_store_partial_response_on_error():
    connection.init_db()
    svc.create_dialogue_task("atomic", "gemini", "chatgpt", "Q", 2)
    svc.start_dialogue_task("atomic")
    # Event serialization fails after provider_call and message insert,
    # proving the whole transaction rolls back.
    def invalid_result(answer):
        return {"success": True, "answer": answer, "conversation_url": GURL,
                "duration_ms": 1}
    with patch.object(repository, "_json", wraps=repository._json) as encode:
        old = encode.side_effect
        def fail_event(value):
            if value and value.get("index") == 1 and "answer" in value:
                raise ValueError("test event write failed")
            return repository.json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"))
        encode.side_effect = fail_event
        with pytest.raises(ValueError, match="test event"):
            svc.record_dialogue_step("atomic", index=1, provider="gemini", action="new",
                                     conversation_name="x", prompt="Q", result=invalid_result("G"))
    assert repository.get_provider_calls("atomic") == []
    assert repository.get_messages("atomic")[0]["role"] == "user"
    assert not [e for e in repository.get_task_events("atomic") if e["event_type"] == "dialogue_turn"]


def test_weather_card_stable_no_copy_action_requires_longer_stability():
    # When a rich Gemini answer has no Copy button but is otherwise stable,
    # the selector must not stall until the provider 180-second timeout.
    class FakeLocator:
        def count(self):
            return 0
        def locator(self, _):
            return self
        @property
        def last(self):
            return self
    class FakePage:
        def locator(self, _):
            return FakeLocator()
    clock = [0]
    def advance(seconds):
        clock[0] += seconds
    with patch('web_response.snapshot', return_value=(3,"洛杉矶天气：局部多云，15°C",True)), \
         patch('web_response.time.monotonic', side_effect=lambda: clock[0]), \
         patch('web_response.time.sleep', side_effect=advance):
        value = wait_for_answer(FakePage(), 'gemini', old_count=2, old_text='', timeout=30)
    assert value.startswith('洛杉矶天气')
    assert clock[0] >= 15
