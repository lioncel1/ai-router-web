"""Task Manager lifecycle tests; every case uses disposable SQLite storage."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from database import connection, repository
from task_manager import TaskManager


class TaskManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(connection, "DB_FILE", Path(self.temp.name) / "test.sqlite3")
        self.db_patch.start()
        connection.init_db()
        self.manager = TaskManager()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def task(self, task_type="chat"):
        return self.manager.create(task_type, "sample request")["task_id"]

    def test_lifecycle_done_and_audit(self):
        tid = self.task()
        self.assertEqual(self.manager.get(tid)["status"], "queued")
        running = self.manager.start(tid)
        self.assertEqual(running["status"], "running")
        self.assertTrue(running["started_at"])
        self.assertEqual(self.manager.checkpoint(tid)["status"], "running")
        done = self.manager.complete(tid, result_summary="ok")
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["result_summary"], "ok")
        self.assertTrue(done["finished_at"])
        self.assertEqual([e["event_type"] for e in repository.get_task_events(tid)],
                         ["task_created", "status_changed", "status_changed"])
        self.assertEqual(self.manager.request_stop(tid)["accepted"], False)
        with self.assertRaises(ValueError):
            self.manager.complete(tid)

    def test_queued_stop_immediate_and_idempotent(self):
        tid = self.task()
        stop = self.manager.request_stop(tid)
        self.assertEqual(stop, {"accepted": True, "status": "stopped", "task_id": tid, "pending": False})
        self.assertTrue(self.manager.stop_requested(tid))
        self.assertEqual(self.manager.request_stop(tid)["accepted"], False)
        self.assertEqual(self.manager.get(tid)["status"], "stopped")
        self.assertEqual([e["event_type"] for e in repository.get_task_events(tid)].count("stop_requested"), 1)
        with self.assertRaises(ValueError):
            self.manager.start(tid)

    def test_running_stop_waits_for_safe_checkpoint(self):
        tid = self.task("dialogue")
        self.manager.start(tid)
        first = self.manager.request_stop(tid)
        second = self.manager.request_stop(tid)
        self.assertEqual(first["status"], "running")
        self.assertTrue(first["pending"])
        self.assertTrue(second["pending"])
        self.assertEqual(self.manager.get(tid)["status"], "running")
        self.assertEqual(self.manager.checkpoint(tid)["status"], "stopped")
        self.assertEqual(self.manager.checkpoint(tid)["status"], "stopped")
        events = repository.get_task_events(tid)
        self.assertEqual([e["event_type"] for e in events].count("stop_requested"), 1)
        self.assertEqual([e["event_type"] for e in events].count("status_changed"), 2)

    def test_complete_respects_stop_request_and_preserves_summary(self):
        tid = self.task()
        self.manager.start(tid)
        self.manager.request_stop(tid)
        task = self.manager.complete(tid, result_summary="response arrived before stopping")
        self.assertEqual(task["status"], "stopped")
        self.assertEqual(task["result_summary"], "response arrived before stopping")

    def test_paused_and_waiting_user_are_stopped_without_active_provider(self):
        for pause_kind in ("paused", "waiting_user"):
            with self.subTest(pause_kind=pause_kind):
                tid = self.task()
                self.manager.start(tid)
                if pause_kind == "paused":
                    self.manager.pause(tid, reason="manual")
                else:
                    self.manager.wait_for_user(tid, reason="approval")
                self.assertEqual(self.manager.get(tid)["status"], pause_kind)
                self.assertEqual(self.manager.request_stop(tid)["status"], "stopped")

    def test_resume_requires_explicit_safety_confirmation(self):
        tid = self.task()
        self.manager.start(tid)
        self.manager.pause(tid)
        with self.assertRaisesRegex(ValueError, "verified_safe"):
            self.manager.resume(tid)
        self.assertEqual(self.manager.get(tid)["status"], "paused")
        self.assertEqual(self.manager.resume(tid, verified_safe=True)["status"], "running")
        self.manager.wait_for_user(tid, reason="need review")
        self.assertEqual(self.manager.resume(tid, verified_safe=True)["status"], "running")

    def test_restart_retains_stop_request(self):
        tid = self.task("auto")
        self.manager.start(tid)
        self.manager.request_stop(tid)
        # A new manager instance represents another process after restart.
        second_manager = TaskManager()
        connection.init_db()
        self.assertTrue(second_manager.stop_requested(tid))
        self.assertEqual(second_manager.checkpoint(tid)["status"], "stopped")

    def test_atomic_stop_or_complete_wins(self):
        for i in range(12):
            tid = self.task()
            self.manager.start(tid)
            with ThreadPoolExecutor(max_workers=2) as executor:
                stop_future = executor.submit(self.manager.request_stop, tid)
                complete_future = executor.submit(self.manager.complete, tid, result_summary="complete")
                stop = stop_future.result()
                finished = complete_future.result()
            if stop["accepted"]:
                self.assertEqual(finished["status"], "stopped")
            else:
                self.assertEqual(finished["status"], "done")
            self.assertEqual(self.manager.get(tid)["status"], finished["status"])

    def test_failure_is_terminal_and_does_not_retry(self):
        tid = self.task()
        self.manager.start(tid)
        failed = self.manager.fail(tid, error_code="provider_timeout", error_message="No complete reply")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error_code"], "provider_timeout")
        self.assertTrue(failed["finished_at"])
        with self.assertRaises(ValueError):
            self.manager.start(tid)
        with self.assertRaises(ValueError):
            self.manager.resume(tid, verified_safe=True)

    def test_missing_task_errors_and_no_implicit_db_initialization(self):
        with self.assertRaises(KeyError):
            self.manager.start("missing")
        with self.assertRaises(KeyError):
            self.manager.stop_requested("missing")
        with self.assertRaises(KeyError):
            self.manager.request_stop("missing")
        with self.assertRaises(ValueError):
            self.manager.fail(self.task(), error_code="", error_message="invalid")

    def test_listing_and_recorded_stop_event(self):
        tid = self.task("auto")
        self.manager.start(tid)
        self.manager.request_stop(tid)
        running = self.manager.list(status="running")
        self.assertTrue(any(item["task_id"] == tid for item in running))
        events = repository.get_task_events(tid)
        stop_event = next(e for e in events if e["event_type"] == "stop_requested")
        self.assertEqual(json.loads(stop_event["event_data_json"]), {"status_at_request": "running"})


if __name__ == "__main__":
    unittest.main()
