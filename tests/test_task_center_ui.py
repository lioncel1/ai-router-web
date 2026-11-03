import shutil
"""Task Center UI regression with stubbed fetch. No live network, models or DB."""
import json
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright, expect


class TaskCenterUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch(
            executable_path=shutil.which("google-chrome") or shutil.which("chromium") or "/usr/bin/google-chrome",
            headless=True,
            args=['--disable-gpu'],
        )
        cls.html = (Path(__file__).resolve().parents[1] / 'templates/index.html').read_text(encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.p.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        listing = {
            'items': [{
                'task_id': 'task-123', 'task_type': 'chat',
                'title': '<img src=x onerror=window.p4Xss=1>',
                'created_at': '2026-11-02', 'status': 'done',
            }], 'total': 1, 'limit': 20, 'offset': 0,
        }
        detail = {
            'task': {
                'task_id': 'task-123', 'task_type': 'chat', 'status': 'done',
                'created_at': '2026-11-02',
                'original_input': '<script>window.p4Xss=1</script>',
                'result_summary': '完成',
            },
            'messages': [{
                'sequence_no': 1, 'role': 'assistant', 'provider': 'chatgpt',
                'content': '<img src=x onerror=window.p4Xss=1>',
            }],
            'events': [{'created_at': '2026-11-02', 'event_type': 'task_created'}],
            'provider_calls': [],
        }
        # Stub fetch BEFORE the production document's JS executes.
        # Escaping '<' prevents untrusted sample text from closing this script tag.
        fake_fetch = '''<script>
window.fetch = async (url) => {
  const path = String(url);
  const value = path.startsWith('/api/tasks?') ? LISTING
    : path.startsWith('/api/tasks/') ? DETAIL : {conversations: []};
  return new Response(JSON.stringify(value), {status: 200, headers: {'Content-Type':'application/json'}});
};
</script>'''.replace('LISTING', json.dumps(listing).replace('<', '\\u003c')).replace(
            'DETAIL', json.dumps(detail).replace('<', '\\u003c'))
        html = self.html.replace('<body>', '<body>\n' + fake_fetch, 1)
        self.page.set_content(html, wait_until='load')

    def test_safe_task_history_rendering(self):
        expect(self.page.locator('#taskRows button')).to_have_count(1)
        expect(self.page.locator('#taskRows')).to_contain_text('onerror=window.p4Xss=1')
        self.page.locator('#taskRows button').click()
        expect(self.page.locator('#taskDetail')).to_contain_text('window.p4Xss=1')
        self.assertIsNone(self.page.evaluate('window.p4Xss'))
        self.assertEqual(self.page.locator('#taskCenter img').count(), 0)
        self.assertEqual(self.page.locator('#taskCenter script').count(), 0)

    def test_existing_chat_elements_remain(self):
        expect(self.page.locator('#provider')).to_be_visible()
        expect(self.page.locator('#send')).to_be_enabled()
        expect(self.page.locator('#taskStatusFilter')).to_be_visible()
