import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import router_analytics
from router_log import summarize_error
from maintenance.sanitize_orchestration_log import migrate


class PrivacyTests(unittest.TestCase):
    def test_legacy_log_backup_and_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'history.jsonl'
            raw = json.dumps({'route_id': 'route', 'original_prompt': 'SECRET_TASK',
                              'final_answer': 'SECRET_FINAL', 'stages': [{'provider': 'gemini', 'role': 'analysis', 'success': True, 'answer': 'SECRET_ANSWER', 'raw_output': 'SECRET_RAW'}]}) + '\n'
            path.write_text(raw)
            backup, count = migrate(path, root / 'backup')
            self.assertEqual(count, 1)
            self.assertEqual(gzip.decompress(backup.read_bytes()).decode(), raw)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('SECRET', path.read_text())
            self.assertIsNone(migrate(path, root / 'backup')[0])

    def test_malformed_history_is_not_modified(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'history'
            path.write_text('broken JSON')
            with self.assertRaises(ValueError):
                migrate(path, Path(folder) / 'backup')
            self.assertEqual(path.read_text(), 'broken JSON')

    def test_router_errors_never_echo_user_text(self):
        self.assertNotIn('SECRET', summarize_error('RuntimeError: SECRET', 1, False))

    def test_routing_v1_and_v2_remain_compatible(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'history'
            path.write_text(json.dumps({'route_id': 'old'}) + '\n' + json.dumps({'route_id': 'new', 'schema_version': 2, 'task_features': ['writing']}) + '\n')
            with patch.object(router_analytics, 'ROUTE_LOG_PATH', path):
                rows = router_analytics.load_route_records()
            self.assertEqual(rows[0]['schema_version'], 1)
            self.assertEqual(rows[0]['task_features'], [])
            self.assertEqual(rows[1]['task_features'], ['writing'])


class ConversationStoreTests(unittest.TestCase):
    def test_atomic_private_cache(self):
        import conversation_store
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cache.json'
            conversation_store.save(path, {'one': {'status': 'pending'}})
            self.assertEqual(conversation_store.load(path)['one']['status'], 'pending')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_text('broken')
            with self.assertRaises(ValueError):
                conversation_store.load(path)
            self.assertEqual(path.read_text(), 'broken')
