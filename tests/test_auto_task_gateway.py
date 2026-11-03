"""Auto async/sync HTTP contract tests; no real model request is sent."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

import gateway_app as gateway
from auto_task_service import create_auto_task, read_auto_job
from database import connection, repository
from task_manager import task_manager
from test_auto_task_service import sample_result


class AutoGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(connection, "DB_FILE", Path(self.tmp.name) / "gateway.sqlite")
        self.db_patch.start()
        connection.init_db()
        with gateway.ORCHESTRATION_JOBS_LOCK:
            gateway.ORCHESTRATION_JOBS.clear()
        self.calls = []

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def fake_workflow(self, prompt):
        self.calls.append(prompt)
        return sample_result()

    def test_sync_endpoint_remains_compatible_and_records_task(self):
        with patch.object(gateway, "run_orchestration", self.fake_workflow):
            with TestClient(gateway.app) as client:
                response = client.post("/api/orchestrate", json={"prompt": "测试同步"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["final_answer"], "最终答案")
        self.assertEqual(self.calls, ["测试同步"])
        self.assertEqual(task_manager.get(data["task_id"])["status"], "done")

    def test_async_endpoint_tracks_job_and_restores_status_from_db(self):
        with patch.object(gateway, "run_orchestration", self.fake_workflow):
            with TestClient(gateway.app) as client:
                start = client.post("/api/orchestrate/start", json={"prompt": "测试异步"})
                self.assertEqual(start.status_code, 200)
                self.assertEqual(start.json()["status"], "queued")
                job_id = start.json()["job_id"]
                self.assertEqual(task_manager.get(job_id)["task_type"], "auto")
                for _ in range(100):
                    state = client.get(f"/api/orchestrate/status/{job_id}").json()
                    if state["status"] in {"done", "failed"}:
                        break
                    time.sleep(0.03)
                self.assertEqual(state["status"], "done")
                self.assertEqual(state["result"]["final_answer"], "最终答案")
                self.assertEqual(self.calls, ["测试异步"])
                with gateway.ORCHESTRATION_JOBS_LOCK:
                    gateway.ORCHESTRATION_JOBS.clear()
                recovered = client.get(f"/api/orchestrate/status/{job_id}")
                self.assertEqual(recovered.status_code, 200)
                data = recovered.json()
                self.assertEqual(data["status"], "done")
                self.assertEqual(data["result"]["stages"], sample_result()["stages"])
                self.assertEqual(len(self.calls), 1)

    def test_store_failure_returns_503_and_does_not_dispatch(self):
        with patch.object(gateway, "run_orchestration", self.fake_workflow):
            with TestClient(gateway.app) as client:
                with patch.object(gateway, "create_auto_task", side_effect=OSError("disk failure")):
                    r1 = client.post("/api/orchestrate/start", json={"prompt": "失败请求"})
                    r2 = client.post("/api/orchestrate", json={"prompt": "失败请求"})
                self.assertEqual(r1.status_code, 503)
                self.assertEqual(r2.status_code, 503)
                self.assertIn("not sent", r1.json()["detail"])
                self.assertEqual(self.calls, [])

    def test_orchestration_failure_is_persisted(self):
        with patch.object(gateway, "run_orchestration", lambda _: sample_result(False)):
            with TestClient(gateway.app) as client:
                start = client.post("/api/orchestrate/start", json={"prompt": "失败测试"})
                job_id = start.json()["job_id"]
                for _ in range(100):
                    state = client.get(f"/api/orchestrate/status/{job_id}").json()
                    if state["status"] in {"done", "failed"}:
                        break
                    time.sleep(0.03)
                self.assertEqual(state["status"], "failed")
                self.assertEqual(task_manager.get(job_id)["status"], "failed")
                self.assertEqual(len(repository.get_provider_calls(job_id)), 3)

    def test_startup_recovery_preserves_uncertain_job_as_failed(self):
        job_id = str(uuid4())
        create_auto_task("可能已发出请求", job_id)
        task_manager.start(job_id)
        with patch.object(gateway, "run_orchestration", self.fake_workflow):
            with TestClient(gateway.app) as client:
                recovered = client.get(f"/api/orchestrate/status/{job_id}").json()
                self.assertEqual(recovered["status"], "failed")
                self.assertIn("automatically resent", recovered["error"])
                self.assertEqual(self.calls, [])

    def test_missing_status_unchanged(self):
        with TestClient(gateway.app) as client:
            r = client.get("/api/orchestrate/status/unknown")
        self.assertEqual(r.json()["status"], "missing")


if __name__ == "__main__":
    unittest.main()
