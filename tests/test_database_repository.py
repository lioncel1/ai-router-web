"""Tests use a disposable SQLite DB, never the server's real data directory."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import connection, repository


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch_db = patch.object(connection, "DB_FILE", Path(self.temp.name) / "test_ai_router.db")
        self.patch_db.start()
        connection.init_db()

    def tearDown(self):
        self.patch_db.stop()
        self.temp.cleanup()

    def test_create_list_and_persistence(self):
        created = repository.create_task("chat", "hello", metadata={"mode": "web"})
        self.assertEqual(created["status"], "queued")
        self.assertEqual(created["original_input"], "hello")
        self.assertEqual(json.loads(created["metadata_json"]), {"mode": "web"})
        self.assertEqual(repository.get_task(created["task_id"])["task_type"], "chat")
        self.assertEqual(repository.list_tasks(status="queued")[0]["task_id"], created["task_id"])
        connection.init_db()  # Migration can run more than once.
        self.assertEqual(len(repository.list_tasks()), 1)

    def test_status_transitions_and_immutability(self):
        task = repository.create_task("auto", "test")
        tid = task["task_id"]
        with self.assertRaises(ValueError):
            repository.update_task_status(tid, "done")  # not started
        running = repository.update_task_status(tid, "running")
        self.assertIsNotNone(running["started_at"])
        done = repository.update_task_status(tid, "done", result_summary="ok")
        self.assertEqual(done["result_summary"], "ok")
        self.assertIsNotNone(done["finished_at"])
        events = repository.get_task_events(tid)
        self.assertEqual([e["event_type"] for e in events], ["task_created", "status_changed", "status_changed"])
        self.assertEqual(json.loads(events[-1]["event_data_json"]), {"from": "running", "to": "done"})
        with self.assertRaises(ValueError):
            repository.update_task_status(tid, "running")

    def test_message_event_and_provider_call(self):
        tid = repository.create_task("dialogue", "hi")["task_id"]
        repository.save_message(tid, "user", "hi")
        repository.save_message(tid, "assistant", "hello", provider="chatgpt", transport="web")
        messages = repository.get_messages(tid)
        self.assertEqual([m["sequence_no"] for m in messages], [1, 2])
        repository.save_task_event(tid, "custom", event_data={"source": "web"})
        self.assertEqual([e["event_type"] for e in repository.get_task_events(tid)], ["task_created", "custom"])
        repository.save_provider_call(tid, provider="chatgpt", transport="web", action="ask",
                                      status="done", quality_ok=True, output_length=5)
        call = repository.get_provider_calls(tid)[0]
        self.assertEqual(call["quality_ok"], 1)
        self.assertEqual(call["output_length"], 5)

    def test_concurrent_messages_have_unique_order(self):
        tid = repository.create_task("dialogue", "test concurrent messages")["task_id"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            message_ids = list(pool.map(lambda n: repository.save_message(tid, "assistant", str(n)), range(8)))
        self.assertEqual(len(set(message_ids)), 8)
        self.assertEqual([m["sequence_no"] for m in repository.get_messages(tid)], list(range(1, 9)))

    def test_failed_inserts_do_not_corrupt_previous_records(self):
        tid = "fixed-test-id"
        repository.create_task("chat", "first", task_id=tid)
        with self.assertRaises(sqlite3.IntegrityError):
            repository.create_task("chat", "second", task_id=tid)
        self.assertEqual(repository.get_task(tid)["original_input"], "first")
        self.assertEqual([e["event_type"] for e in repository.get_task_events(tid)], ["task_created"])
        with self.assertRaises(KeyError):
            repository.save_provider_call("nonexistent", provider="chatgpt", transport="web", action="new", status="done")
        self.assertEqual(repository.get_provider_calls(tid), [])

    def test_invalid_writes_fail_without_side_effects(self):
        with self.assertRaises(KeyError):
            repository.save_message("missing", "user", "hello")
        tid = repository.create_task("chat", "x")["task_id"]
        with self.assertRaises(ValueError):
            repository.save_message(tid, "invalid", "x")
        with self.assertRaises(TypeError):
            repository.save_task_event(tid, "event", event_data=[1, 2])
        self.assertEqual(repository.get_messages(tid), [])
        self.assertEqual([e["event_type"] for e in repository.get_task_events(tid)], ["task_created"])


if __name__ == "__main__":
    unittest.main()
