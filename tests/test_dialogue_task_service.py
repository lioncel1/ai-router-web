"""Dialogue persistence tests, all DB operations use disposable SQLite."""
import json
from pathlib import Path
from unittest.mock import patch
import pytest

from database import connection, repository
import dialogue_task_service as svc
from install_v09_phase2 import patched


def new_task(job_id="dialogue-123", max_messages=2):
    connection.init_db()
    svc.create_dialogue_task(job_id, "gemini", "chatgpt", "RAW USER PROMPT", max_messages)
    return job_id


def success(answer="A response"):
    return {
        "success": True, "answer": answer, "duration_ms": 11,
        "quality_ok": True, "conversation_url": "https://gemini.google.com/app/test",
        "transport_success": True, "wrapper_success": True,
    }


def test_create_before_dispatch_and_save_initial_user_prompt():
    job_id = new_task()
    task = repository.get_task(job_id)
    assert task["status"] == "queued"
    assert json.loads(task["metadata_json"])["max_messages"] == 2
    assert [m["content"] for m in repository.get_messages(job_id)] == ["RAW USER PROMPT"]
    assert svc.start_dialogue_task(job_id) == "running"


def test_two_turns_persist_and_rebuild_status():
    job_id = new_task()
    svc.start_dialogue_task(job_id)
    assert svc.record_dialogue_step(job_id, index=1, provider="gemini", action="new",
                                    conversation_name="dialogue-a", prompt="RAW USER PROMPT",
                                    result=success("A reply")) == "done"
    assert svc.record_dialogue_step(job_id, index=2, provider="chatgpt", action="new",
                                    conversation_name="dialogue-b", prompt="A reply",
                                    result=success("B reply")) == "done"
    assert svc.complete_dialogue_task(job_id) == "done"
    history = svc.read_dialogue_job(job_id)
    assert history["status"] == "done"
    assert history["completed_messages"] == 2
    assert [m["answer"] for m in history["messages"]] == ["A reply", "B reply"]
    assert [m["role"] for m in repository.get_messages(job_id)] == ["user", "assistant", "assistant"]
    calls = repository.get_provider_calls(job_id)
    assert len(calls) == 2
    assert [c["input_length"] for c in calls] == [len("RAW USER PROMPT"), len("A reply")]


def test_stopped_during_running_call_at_checkpoint():
    job_id = new_task()
    svc.start_dialogue_task(job_id)
    outcome = svc.request_dialogue_stop(job_id)
    assert outcome["accepted"] and outcome["status"] == "running"
    assert svc.stop_requested(job_id)
    assert svc.record_dialogue_step(job_id, index=1, provider="gemini", action="new",
                                    conversation_name="a", prompt="RAW USER PROMPT",
                                    result=success("first")) == "done"
    assert svc.checkpoint(job_id) == "stopped"
    assert svc.read_dialogue_job(job_id)["completed_messages"] == 1
    assert not svc.request_dialogue_stop(job_id)["accepted"]


def test_stop_queued_prevents_worker_start():
    job_id = new_task()
    assert svc.request_dialogue_stop(job_id)["status"] == "stopped"
    assert svc.start_dialogue_task(job_id) == "stopped"
    assert repository.get_provider_calls(job_id) == []


def test_fail_does_not_create_fake_assistant_reply():
    job_id = new_task()
    svc.start_dialogue_task(job_id)
    failed = dict(success(""), success=False, answer="", error="quality_failed", quality_ok=False)
    assert svc.record_dialogue_step(job_id, index=1, provider="gemini", action="new",
                                    conversation_name="a", prompt="RAW USER PROMPT",
                                    result=failed) == "failed"
    svc.fail_dialogue_task(job_id, code="provider_failed", reason="quality_failed")
    history = svc.read_dialogue_job(job_id)
    assert history["status"] == "failed"
    assert history["messages"] == []
    assert len(repository.get_provider_calls(job_id)) == 1
    assert repository.get_provider_calls(job_id)[0]["status"] == "failed"


def test_restart_marks_queued_and_running_only_and_no_replay():
    running = new_task("running-job")
    svc.start_dialogue_task(running)
    queued = new_task("queued-job")
    done = new_task("done-job")
    svc.start_dialogue_task(done)
    svc.complete_dialogue_task(done)
    assert svc.reconcile_interrupted_dialogue_tasks() == 2
    assert repository.get_task(running)["error_code"] == "gateway_restarted"
    assert repository.get_task(queued)["status"] == "failed"
    assert svc.read_dialogue_job(running)["status"] == "failed"
    assert repository.get_task(done)["status"] == "done"
    assert svc.reconcile_interrupted_dialogue_tasks() == 0


def test_db_failure_before_dispatch_fails_closed():
    connection.init_db()
    with patch.object(connection, "init_db", side_effect=OSError("disk")):
        with pytest.raises(OSError):
            svc.create_dialogue_task("missing-job", "gemini", "chatgpt", "prompt", 2)
    assert repository.get_task("missing-job") is None


def test_partial_initial_message_save_marks_failed():
    connection.init_db()
    with patch.object(repository, "save_message", side_effect=OSError("disk")):
        with pytest.raises(OSError):
            svc.create_dialogue_task("setup-failed", "gemini", "chatgpt", "prompt", 2)
    assert repository.get_task("setup-failed")["status"] == "failed"


def test_gateway_installer_idempotent_and_strict():
    base = Path(__file__).resolve().parents[1]
    source = (base / "gateway_app.py").read_text(encoding="utf-8")
    assert patched(source) == source
    with pytest.raises(RuntimeError):
        patched("not a gateway")
    with pytest.raises(RuntimeError):
        patched(source.replace("_dts.create_dialogue_task", "create_dialogue_task", 1))
