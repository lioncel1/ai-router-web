"""v0.10 Phase 1: offline dispatch compatibility and fail-closed tests."""
from unittest import TestCase
from unittest.mock import Mock, patch

from provider_transport import TransportUnavailable, dispatch_provider
import provider_runtime


class TransportDispatchTests(TestCase):
    def setUp(self):
        self.web = Mock(return_value={"success": True, "answer": "web answer"})
        self.api = Mock(return_value={"success": True, "answer": "api answer"})
        self.args = ("gemini", "new", "example", "test prompt")

    def test_default_web_remains_unchanged(self):
        with patch.object(provider_runtime, "_run_web_provider", self.web):
            result = provider_runtime.run_provider(*self.args)
        self.assertEqual(result, {"success": True, "answer": "web answer"})
        self.web.assert_called_once_with(*self.args, role="answer", task=None, timeout=300)

    def test_explicit_web_preserves_role_task_and_timeout(self):
        with patch.object(provider_runtime, "_run_web_provider", self.web):
            result = provider_runtime.run_provider(
                *self.args, transport="web", role="review", task="original", timeout=5
            )
        self.assertTrue(result["success"])
        self.web.assert_called_once_with(*self.args, role="review", task="original", timeout=5)

    def test_unconfigured_api_rejected_without_web_dispatch(self):
        with patch.object(provider_runtime, "_run_web_provider", self.web):
            with self.assertRaisesRegex(TransportUnavailable, "request not sent"):
                provider_runtime.run_provider(*self.args, transport="api")
        self.web.assert_not_called()

    def test_invalid_transport_rejected_before_web_dispatch(self):
        with patch.object(provider_runtime, "_run_web_provider", self.web):
            with self.assertRaises(ValueError):
                provider_runtime.run_provider(*self.args, transport="auto")
        self.web.assert_not_called()

    def test_unknown_provider_action_still_fail(self):
        with patch.object(provider_runtime, "_run_web_provider", self.web):
            with self.assertRaises(ValueError):
                provider_runtime.run_provider("unknown", "new", "x", "hello")
            with self.assertRaises(ValueError):
                provider_runtime.run_provider("gemini", "delete", "x", "hello")
        self.web.assert_not_called()

    def test_future_api_adapter_never_falls_back_to_web(self):
        result = dispatch_provider(
            *self.args, transport="api", web_call=self.web, api_call=self.api,
            role="analysis", task="source question", timeout=13,
        )
        self.assertEqual(result["answer"], "api answer")
        self.api.assert_called_once_with(
            *self.args, role="analysis", task="source question", timeout=13
        )
        self.web.assert_not_called()

    def test_adapter_exception_propagates_without_retry_or_fallback(self):
        self.api.side_effect = TimeoutError("ambiguous upstream outcome")
        with self.assertRaises(TimeoutError):
            dispatch_provider(*self.args, transport="api", web_call=self.web, api_call=self.api)
        self.assertEqual(self.api.call_count, 1)
        self.web.assert_not_called()
