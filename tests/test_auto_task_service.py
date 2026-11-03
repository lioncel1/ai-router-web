"""v0.9 Phase 1: no live provider calls; use a disposable SQLite DB."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from database import connection, repository
from task_manager import task_manager
from auto_task_service import (
    create_auto_task, execute_auto_task, read_auto_job,
    reconcile_interrupted_auto_tasks,
)


def sample_result(success=True):
    first = {
        "provider": "gemini", "role": "analysis", "success": True,
        "answer": "分析内容", "selected_attempt": 2,
        "conversation_url": "https://gemini.google.com/app/mock",
        "attempts": [
            {"attempt": 1, "conversation_name": "a1", "quality_ok": False,
             "quality_reason": "empty", "answer_length": 0, "duration_ms": 20,
             "transport_success": True, "wrapper_success": False, "error_summary": "empty"},
            {"attempt": 2, "conversation_name": "a2", "quality_ok": True,
             "quality_reason": "", "answer_length": 4, "duration_ms": 30,
             "transport_success": True, "wrapper_success": True, "error_summary": ""},
        ],
    }
    last = {
        "provider": "chatgpt", "role": "synthesis", "success": success,
        "answer": "最终答案" if success else "", "selected_attempt": 1 if success else None,
        "conversation_url": "https://chatgpt.com/c/mock" if success else "",
        "attempts": [{"attempt": 1, "conversation_name": "b1", "quality_ok": success,
                      "quality_reason": "" if success else "quality_failed",
                      "answer_length": 4 if success else 0, "duration_ms": 15,
                      "transport_success": True, "wrapper_success": success,
                      "error_summary": "" if success else "quality_failed"}],
    }
    return {"success": success, "route_id": "route-test", "stages": [first, last],
            "final_answer": "最终答案" if success else "",
            "duration_ms": 65, "error": "" if success else "chatgpt/synthesis: quality_failed"}


class AutoTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(connection, "DB_FILE", Path(self.tmp.name) / "test.sqlite3")
        self.db_patch.start()
        connection.init_db()
        self.job_id = str(uuid4())
        self.calls = []

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def fake(self, prompt):
        self.calls.append(prompt)
        return sample_result()

    def test_create_is_queued_and_saves_user_prompt_before_dispatch(self):
        create_auto_task("原始问题", self.job_id)
        task = task_manager.get(self.job_id)
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["task_type"], "auto")
        self.assertEqual(task["original_input"], "原始问题")
        self.assertEqual([m["role"] for m in repository.get_messages(self.job_id)], ["user"])
        self.assertEqual(self.calls, [])

    def test_success_persists_all_attempts_messages_and_exact_status(self):
        create_auto_task("原始问题", self.job_id)
        result = execute_auto_task(self.job_id, "原始问题", self.fake)
        self.assertEqual(self.calls, ["原始问题"])
        self.assertTrue(result["success"])
        self.assertEqual(result["task_id"], self.job_id)
        self.assertEqual(task_manager.get(self.job_id)["status"], "done")
        calls = repository.get_provider_calls(self.job_id)
        self.assertEqual(len(calls), 3)
        self.assertEqual([c["status"] for c in calls], ["failed", "done", "done"])
        self.assertIsNone(calls[0]["conversation_url"])
        self.assertEqual(calls[1]["conversation_url"], "https://gemini.google.com/app/mock")
        self.assertEqual(json.loads(calls[1]["metadata_json"])["role"], "analysis")
        messages = repository.get_messages(self.job_id)
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "assistant"])
        self.assertEqual(messages[-1]["content"], "最终答案")
        saved = read_auto_job(self.job_id)
        self.assertEqual(saved["status"], "done")
        self.assertEqual(saved["result"]["final_answer"], "最终答案")
        self.assertEqual(saved["result"]["stages"], sample_result()["stages"])

    def test_workflow_failure_preserves_partial_stage_records(self):
        create_auto_task("原始问题", self.job_id)
        res = execute_auto_task(self.job_id, "原始问题", lambda prompt: sample_result(False))
        self.assertFalse(res["success"])
        self.assertEqual(task_manager.get(self.job_id)["status"], "failed")
        self.assertEqual(len(repository.get_provider_calls(self.job_id)), 3)
        saved = read_auto_job(self.job_id)
        self.assertEqual(saved["status"], "failed")
        self.assertIn("quality_failed", saved["error"])
        self.assertFalse(saved["result"]["success"])

    def test_orchestrator_exception_records_failure_without_retry(self):
        create_auto_task("原始问题", self.job_id)
        def crashed(prompt):
            self.calls.append(prompt)
            raise RuntimeError("unavailable")
        with self.assertRaises(RuntimeError):
            execute_auto_task(self.job_id, "原始问题", crashed)
        self.assertEqual(self.calls, ["原始问题"])
        self.assertEqual(task_manager.get(self.job_id)["status"], "failed")
        self.assertEqual(read_auto_job(self.job_id)["status"], "failed")

    def test_setup_failure_never_sends(self):
        with patch("auto_task_service.repository.save_message", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                create_auto_task("原始问题", self.job_id)
        self.assertEqual(task_manager.get(self.job_id)["status"], "failed")
        self.assertEqual(self.calls, [])

    def test_db_failure_after_workflow_keeps_answer_and_does_not_retry(self):
        create_auto_task("原始问题", self.job_id)
        with patch("auto_task_service.repository.save_task_event", side_effect=OSError("disk full")):
            with self.assertLogs("auto_task_service", level="ERROR"):
                result = execute_auto_task(self.job_id, "原始问题", self.fake)
        self.assertEqual(result["final_answer"], "最终答案")
        self.assertIn("history_warning", result)
        self.assertEqual(self.calls, ["原始问题"])
        self.assertEqual(task_manager.get(self.job_id)["status"], "running")

    def test_worker_does_not_start_when_task_is_missing(self):
        with self.assertRaises(KeyError):
            execute_auto_task(self.job_id, "原始问题", self.fake)
        self.assertEqual(self.calls, [])

    def test_gateway_restart_marks_only_incomplete_auto_jobs(self):
        create_auto_task("原始问题", self.job_id)
        running_id = str(uuid4())
        create_auto_task("等待回复", running_id)
        task_manager.start(running_id)
        chat = task_manager.create("chat", "normal chat")
        task_manager.start(chat["task_id"])
        self.assertEqual(reconcile_interrupted_auto_tasks(), 2)
        self.assertEqual(reconcile_interrupted_auto_tasks(), 0)
        for job_id in (self.job_id, running_id):
            saved = read_auto_job(job_id)
            self.assertEqual(saved["status"], "failed")
            self.assertIn("automatically resent", saved["error"])
            self.assertNotIn("result", saved)
        self.assertEqual(task_manager.get(chat["task_id"])["status"], "running")
        self.assertEqual(self.calls, [])

    def test_recovery_does_not_change_completed_job(self):
        create_auto_task("原始问题", self.job_id)
        execute_auto_task(self.job_id, "原始问题", self.fake)
        self.assertEqual(reconcile_interrupted_auto_tasks(), 0)
        self.assertEqual(read_auto_job(self.job_id)["status"], "done")

    def test_unknown_task_and_non_auto_task_not_exposed(self):
        self.assertIsNone(read_auto_job(self.job_id))
        task_manager.create("chat", "chat", task_id=self.job_id)
        self.assertIsNone(read_auto_job(self.job_id))


if __name__ == "__main__":
    unittest.main()
