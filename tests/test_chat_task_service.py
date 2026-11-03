"""Phase 3 tests: mocked Web provider; all SQLite access is disposable."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import connection, repository
from task_manager import task_manager
from chat_task_service import execute_chat_with_task
from install_phase3 import patched, IMPORT_OLD, IMPORT_NEW, CALL_OLD, CALL_NEW, RESPONSE_OLD, RESPONSE_NEW


class ChatTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(connection, "DB_FILE", Path(self.tmp.name) / "test.sqlite3")
        self.db_patch.start()
        connection.init_db()
        self.calls = []

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def provider(self, provider, action, name, prompt):
        self.calls.append((provider, action, name, prompt))
        return {
            "success": True, "answer": "测试成功", "answer_length": 4,
            "duration_ms": 7, "exit_code": 0, "quality_ok": True,
            "quality_reason": "", "transport_success": True, "wrapper_success": True,
            "conversation_url": "https://chatgpt.com/c/test",
            "error": "",
        }

    def task_call(self, fn=None):
        return execute_chat_with_task(
            fn or self.provider, "chatgpt", "new", "phase3-test", "仅回复测试成功"
        )

    def test_success_persists_task_messages_call_and_event(self):
        result = self.task_call()
        tid = result["task_id"]
        self.assertEqual(self.calls, [("chatgpt", "new", "phase3-test", "仅回复测试成功")])
        self.assertEqual(result["answer"], "测试成功")
        self.assertEqual(result.get("history_warning"), None)
        task = task_manager.get(tid)
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["source"], "web")
        self.assertEqual(task["original_input"], "仅回复测试成功")
        self.assertEqual(json.loads(task["metadata_json"])["conversation_name"], "phase3-test")
        self.assertEqual([m["role"] for m in repository.get_messages(tid)], ["user", "assistant"])
        self.assertEqual(repository.get_messages(tid)[-1]["content"], "测试成功")
        calls = repository.get_provider_calls(tid)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["status"], "done")
        self.assertEqual(calls[0]["quality_ok"], 1)
        self.assertEqual([e["event_type"] for e in repository.get_task_events(tid)],
                         ["task_created", "status_changed", "status_changed"])

    def test_provider_quality_failure_no_assistant_message(self):
        def fail(*args):
            self.calls.append(args)
            return {"success": False, "answer": "", "answer_length": 3,
                    "error": "quality_failed", "quality_ok": False,
                    "quality_reason": "missing_response", "duration_ms": 81,
                    "exit_code": 1, "conversation_url": "", "transport_success": True,
                    "wrapper_success": False}
        res = self.task_call(fail)
        self.assertFalse(res["success"])
        self.assertEqual(task_manager.get(res["task_id"])["status"], "failed")
        self.assertEqual([m["role"] for m in repository.get_messages(res["task_id"])], ["user"])
        self.assertEqual(repository.get_provider_calls(res["task_id"])[0]["status"], "failed")
        self.assertEqual(len(self.calls), 1)

    def test_exception_is_recorded_and_reraised_without_retry(self):
        def crashed(*args):
            self.calls.append(args)
            raise RuntimeError("crashed")
        with self.assertRaisesRegex(RuntimeError, "crashed"):
            self.task_call(crashed)
        self.assertEqual(len(self.calls), 1)
        task = task_manager.list()[0]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error_code"], "provider_exception")
        self.assertEqual(repository.get_provider_calls(task["task_id"])[0]["status"], "failed")

    def test_database_init_failure_does_not_send_prompt(self):
        with patch("chat_task_service.connection.init_db", side_effect=OSError("db unavailable")):
            with self.assertRaises(OSError):
                self.task_call()
        self.assertEqual(self.calls, [])

    def test_pre_dispatch_message_failure_does_not_send_prompt(self):
        with patch("chat_task_service.repository.save_message", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.task_call()
        self.assertEqual(self.calls, [])
        self.assertEqual(task_manager.list()[0]["status"], "failed")

    def test_post_dispatch_storage_failure_preserves_provider_answer(self):
        with patch("chat_task_service.repository.save_provider_call", side_effect=OSError("disk failure")):
            with self.assertLogs("chat_task_service", level="ERROR"):
                result = self.task_call()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["answer"], "测试成功")
        self.assertIn("history_warning", result)
        self.assertEqual(task_manager.get(result["task_id"])["status"], "running")

    def test_task_id_is_unique_for_separate_calls(self):
        first = self.task_call()
        second = self.task_call()
        self.assertNotEqual(first["task_id"], second["task_id"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(task_manager.list()), 2)


class GatewayPatchTests(unittest.TestCase):
    def test_patcher_is_strict_and_idempotent(self):
        original = IMPORT_OLD + "stub\n" + CALL_OLD + RESPONSE_OLD
        updated = patched(original)
        self.assertIn(IMPORT_NEW, updated)
        self.assertIn(CALL_NEW, updated)
        self.assertIn(RESPONSE_NEW, updated)
        self.assertEqual(patched(updated), updated)

    def test_partial_patch_refused(self):
        with self.assertRaisesRegex(RuntimeError, "partial"):
            patched(IMPORT_NEW + CALL_OLD + RESPONSE_OLD)

    def test_drifted_gateway_refused(self):
        with self.assertRaisesRegex(RuntimeError, "anchor mismatch"):
            patched("totally different version")


if __name__ == "__main__":
    unittest.main()
