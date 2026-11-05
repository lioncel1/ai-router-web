"""DOM fixtures exercise real Playwright selectors without model calls."""
import unittest
import shutil
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from web_response import snapshot, wait_for_answer


class DOMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch(executable_path=shutil.which('google-chrome') or shutil.which('chromium') or '/usr/bin/google-chrome', headless=True, args=['--disable-gpu'])
        cls.page = cls.browser.new_page()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.p.stop()

    def test_gemini_ui_title_is_not_body(self):
        self.page.set_content('<model-response><h3>Investigating a New Dynamic Topic</h3><span>Gemini 说</span></model-response>')
        self.assertEqual(snapshot(self.page, 'gemini'), (1, '', False))

    def test_completed_short_gemini(self):
        self.page.set_content('<model-response><h3>Unrelated UI title</h3><message-content><div class="markdown"><p>4</p></div></message-content><button aria-label="复制"></button></model-response>')
        self.assertEqual(snapshot(self.page, 'gemini'), (1, '4', True))

    def test_streaming_does_not_complete(self):
        self.page.set_content('<button data-testid="stop-button">Stop</button><section data-testid="conversation-turn-2"><div data-message-author-role="assistant"><div class="markdown">Partial answer</div></div><button data-testid="copy-turn-action-button"></button></section>')
        self.assertFalse(snapshot(self.page, 'chatgpt')[2])

    def test_chatgpt_only_latest_turn_completion_counts(self):
        self.page.set_content('<section data-testid="conversation-turn-1"><div data-message-author-role="assistant"><div class="markdown">old</div></div><button data-testid="copy-turn-action-button"></button></section><section data-testid="conversation-turn-2"><div data-message-author-role="assistant">Thinking</div></section>')
        self.assertEqual(snapshot(self.page, 'chatgpt'), (2, '', False))

    def test_completed_short_chatgpt(self):
        self.page.set_content('<section data-testid="conversation-turn-2"><div data-message-author-role="assistant"><div class="markdown">是</div></div><button data-testid="copy-turn-action-button"></button></section>')
        self.assertEqual(snapshot(self.page, 'chatgpt'), (1, '是', True))

    def test_stability_requires_completion_and_new_response(self):
        clock = [0]
        def sleep(seconds):
            clock[0] += seconds
        snapshots = [(1, 'old', True)] * 2 + [(2, 'partial', False)] * 7 + [(2, '4', True)] * 6
        with patch('web_response.snapshot', side_effect=snapshots), patch('web_response.time.monotonic', side_effect=lambda: clock[0]), patch('web_response.time.sleep', side_effect=sleep):
            self.assertEqual(wait_for_answer(None, 'gemini', 1, 'old', timeout=30), '4')
        self.assertEqual(clock[0], 14)
