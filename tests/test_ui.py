import shutil
"""Browser UI regression tests using mocked HTTP responses; no provider calls."""
import json
import unittest
from pathlib import Path

from playwright.sync_api import Error, expect, sync_playwright


class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch(
            executable_path=shutil.which("google-chrome") or shutil.which("chromium") or "/usr/bin/google-chrome",
            headless=True,
            args=["--disable-gpu"],
        )
        cls.html = (
            Path(__file__).resolve().parents[1] / "templates" / "index.html"
        ).read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.p.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.page.route(
            "http://rc.test/",
            lambda route: route.fulfill(content_type="text/html", body=self.html),
        )
        try:
            self.page.goto("http://rc.test/")
        except Error as exc:
            if "ERR_BLOCKED_BY_ADMINISTRATOR" in str(exc):
                self.skipTest("Sandbox policy blocked mocked rc.test navigation")
            raise

    def mock_auto_start(self, payloads=None, job_id="test-auto-job"):
        def reply(route):
            if payloads is not None:
                payloads.append(route.request.post_data_json)
            route.fulfill(
                json={"success": True, "job_id": job_id, "status": "queued"}
            )

        self.page.route("**/api/orchestrate/start", reply)
        return job_id

    def mock_auto_status(self, status_payload, job_id="test-auto-job"):
        def reply(route):
            self.assertTrue(route.request.url.endswith("/" + job_id))
            route.fulfill(json=status_payload)

        self.page.route("**/api/orchestrate/status/*", reply)

    def send_auto(self):
        self.page.select_option("#provider", "auto")
        expect(self.page.locator("#normalFields")).to_be_hidden()
        self.page.fill("#prompt", "REAL WEB TASK")
        self.page.click("#send")
        expect(self.page.locator("#send")).to_be_enabled(timeout=10000)

    def test_auto_structured_response_and_safe_render(self):
        payloads = []
        job_id = self.mock_auto_start(payloads)
        self.mock_auto_status(
            {
                "success": True,
                "job_id": job_id,
                "status": "done",
                "result": {
                    "success": True,
                    "route_id": "route-6",
                    "duration_ms": 12,
                    "final_answer": "<script>window.unwanted=true</script>FINAL",
                    "stages": [
                        {
                            "provider": "gemini",
                            "role": "analysis",
                            "success": True,
                            "duration_ms": 5,
                            "retry_count": 0,
                            "answer": "ANALYSIS",
                        }
                    ],
                },
            },
            job_id,
        )
        self.send_auto()
        self.assertEqual(payloads, [{"prompt": "REAL WEB TASK"}])
        expect(self.page.locator("#result")).to_contain_text("route-6")
        expect(self.page.locator("#result")).to_contain_text("FINAL")
        self.assertFalse(self.page.locator("details").evaluate("e=>e.open"))
        self.assertIsNone(self.page.evaluate("window.unwanted"))
        self.assertEqual(self.page.locator("#result script").count(), 0)

    def test_proxy_errors_are_explained(self):
        cases = [
            ("", 504),
            ("<html>Gateway timeout</html>", 504),
            (json.dumps({"detail": [{"msg": "prompt invalid"}]}), 422),
        ]
        for body, status in cases:
            with self.subTest(status=status, body=body):
                self.page.route(
                    "**/api/orchestrate/start",
                    lambda route: route.fulfill(
                        status=status, body=body
                    ),
                )
                try:
                    self.send_auto()
                    text = self.page.locator("#result").inner_text()
                    self.assertIn("请求失败", text)
                    self.assertNotIn("Unexpected end of JSON input", text)
                finally:
                    self.page.unroute("**/api/orchestrate/start")

    def test_auto_polling_http_error_is_explained(self):
        self.mock_auto_start()
        self.page.route(
            "**/api/orchestrate/status/*",
            lambda route: route.fulfill(status=504, body=""),
        )
        self.send_auto()
        text = self.page.locator("#result").inner_text()
        self.assertIn("请求失败", text)
        self.assertNotIn("Unexpected end of JSON input", text)

    def test_failed_auto_job_reports_error(self):
        """The server marks an unsuccessful orchestration as a failed job."""
        job_id = self.mock_auto_start()
        self.mock_auto_status(
            {
                "success": False,
                "job_id": job_id,
                "status": "failed",
                "error": "quality_failed",
                "result": {
                    "success": False,
                    "route_id": "fail",
                    "duration_ms": 20,
                    "final_answer": "",
                    "error": "quality_failed",
                    "stages": [
                        {
                            "provider": "chatgpt",
                            "role": "review",
                            "success": False,
                            "quality_reason": "ui_status_only",
                            "retry_count": 1,
                            "duration_ms": 10,
                        }
                    ],
                },
            },
            job_id,
        )
        self.send_auto()
        expect(self.page.locator("#result")).to_contain_text("请求失败")
        expect(self.page.locator("#result")).to_contain_text("quality_failed")

    def test_missing_auto_job_reports_error(self):
        job_id = self.mock_auto_start()
        self.mock_auto_status(
            {
                "success": False,
                "job_id": job_id,
                "status": "missing",
                "error": "job not found",
            },
            job_id,
        )
        self.send_auto()
        expect(self.page.locator("#result")).to_contain_text("请求失败")
        expect(self.page.locator("#result")).to_contain_text("job not found")

    def test_single_provider_controls_remain(self):
        self.page.select_option("#provider", "auto")
        self.page.select_option("#provider", "chatgpt")
        expect(self.page.locator("#normalFields")).to_be_visible()
        self.page.select_option("#action", "ask")
        self.page.fill("#conversation", "existing")
        self.page.fill("#prompt", "followup")
        payloads = []

        def reply(route):
            payloads.append(route.request.post_data_json)
            route.fulfill(
                json={
                    "success": True,
                    "provider": "chatgpt",
                    "conversation_name": "existing",
                    "answer": "ANSWER",
                    "duration_ms": 1,
                    "exit_code": 0,
                }
            )

        self.page.route("**/api/chat", reply)
        self.page.click("#send")
        expect(self.page.locator("#result")).to_contain_text("ANSWER")
        self.assertEqual(payloads[0]["action"], "ask")
        self.assertEqual(payloads[0]["conversation_name"], "existing")
