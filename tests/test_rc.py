import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gateway_app
import orchestrator as o
import provider_runtime as runtime
from quality import check_quality


def result(answer='有效回答', success=True, reason='ok'):
    return dict(success=success, transport_success=True, wrapper_success=True,
                quality_ok=success, quality_reason=reason, answer=answer if success else '',
                answer_length=len(answer), conversation_url='https://chatgpt.com/c/test',
                error='' if success else reason, duration_ms=1, exit_code=0)


class QualityTests(unittest.TestCase):
    def test_short_valid_answers(self):
        for text in ['4', '是', 'OK', '夏天', 'Hello world']:
            self.assertEqual(check_quality(text), (True, 'ok'))

    def test_bad_responses(self):
        for text in ['', 'Thinking', 'Gemini 说', 'Framing the Inquiry',
                     'Investigating Novel Constraints', 'Traceback (most recent call last):\nTimeoutError: wait',
                     'Something went wrong. Please retry.']:
            self.assertFalse(check_quality(text)[0], text)

    def test_evidence_overrides_surface_text(self):
        self.assertFalse(check_quality('Long answer that is still being streamed.', evidence={'processing': True})[0])
        self.assertFalse(check_quality('A dynamically changing UI title', evidence={'body_present': False})[0])
        self.assertTrue(check_quality('Investigating Novel Constraints', evidence={'completed': True, 'body_present': True})[0])
        self.assertFalse(check_quality('4', wrapper_ok=False)[0])
        self.assertFalse(check_quality('4', transport_ok=False)[0])
        self.assertFalse(check_quality('审查意见', role='synthesis')[0])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.log = patch.object(o, 'save_log')
        self.saved = self.log.start()
        self.addCleanup(self.log.stop)

    def test_full_context_and_final_synthesis(self):
        with patch.object(o, 'run_provider', side_effect=[result('ANALYSIS'), result('REVIEW'), result('FINAL')]) as run:
            output = o.run_orchestration('ORIGINAL SECRET')
        self.assertTrue(output['success'])
        self.assertEqual(output['final_answer'], 'FINAL')
        prompts = [c.args[3] for c in run.call_args_list]
        self.assertTrue(all('ORIGINAL SECRET' in p for p in prompts))
        self.assertIn('ANALYSIS', prompts[1])
        self.assertIn('ANALYSIS', prompts[2])
        self.assertIn('REVIEW', prompts[2])
        self.assertIn('Current Role: synthesis', prompts[2])
        log = json.dumps(self.saved.call_args.args[0])
        for text in ['ORIGINAL SECRET', 'ANALYSIS', 'REVIEW', 'FINAL', 'raw_output', 'original_prompt']:
            self.assertNotIn(text, log)

    def test_retry_once_new_name_same_provider_original_task(self):
        with patch.object(o, 'run_provider', side_effect=[result('Thinking', False, 'ui_status_only'), result('4')]) as run:
            output = o.run_orchestration('2+2?', [{'provider': 'gemini', 'role': 'synthesis'}])
        self.assertTrue(output['success'])
        self.assertEqual(run.call_count, 2)
        a, b = run.call_args_list
        self.assertEqual(a.args[0], b.args[0])
        self.assertNotEqual(a.args[2], b.args[2])
        self.assertIn('2+2?', b.args[3])
        self.assertNotIn('Thinking', b.args[3])
        self.assertEqual(output['stages'][0]['retry_count'], 1)
        self.assertEqual(output['stages'][0]['selected_attempt'], 2)

    def test_double_failure_stops_no_fallback(self):
        with patch.object(o, 'run_provider', return_value=result('ERR', False, 'transport_failed')) as run:
            output = o.run_orchestration('task')
        self.assertEqual(run.call_count, 2)
        self.assertFalse(output['success'])
        self.assertEqual(len(output['stages']), 1)
        self.assertEqual(output['final_answer'], '')
        self.assertEqual(output['stages'][0]['status'], 'failed')
        self.assertIsNone(output['stages'][0]['selected_attempt'])

    def test_role_provider_decoupled(self):
        workflow = [{'provider': 'chatgpt', 'role': 'analysis'}, {'provider': 'gemini', 'role': 'review'}, {'provider': 'gemini', 'role': 'synthesis'}]
        with patch.object(o, 'run_provider', return_value=result()) as run:
            output = o.run_orchestration('task', workflow)
        self.assertTrue(output['success'])
        self.assertEqual([c.args[0] for c in run.call_args_list], ['chatgpt', 'gemini', 'gemini'])

    def test_deadline_is_structured_failure(self):
        with patch.object(o, 'ROUTE_TIMEOUT_SECONDS', 0), patch.object(o, 'run_provider') as run:
            output = o.run_orchestration('task')
        run.assert_not_called()
        self.assertFalse(output['success'])
        self.assertEqual(output['stages'][0]['quality_reason'], 'route_timeout')

    def test_log_failure_does_not_discard_answer(self):
        self.saved.side_effect = OSError('disk full')
        with patch.object(o, 'run_provider', return_value=result()):
            output = o.run_orchestration('task')
        self.assertTrue(output['success'])
        self.assertEqual(output['log_error'], 'orchestration_log_write_failed')


class RuntimeTests(unittest.TestCase):
    def run_fake(self, status='done', answer='4', prompt='task', url='https://gemini.google.com/app/test', exit_code=0):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data').mkdir()
            (root / 'data/gemini_conversations.json').write_text(json.dumps({'test': {'status': status, 'last_prompt': prompt, 'last_answer': answer, 'url': url}}))
            stdout = f'Gemini回复：\n{"="*60}\n{answer}\n会话地址： {url}'
            with patch.object(runtime, 'BASE_DIR', root), patch.object(runtime.subprocess, 'Popen') as popen:
                popen.return_value.communicate.return_value = (stdout, None)
                popen.return_value.returncode = exit_code
                return runtime.run_provider('gemini', 'new', 'test', 'task')

    def test_validated_db_answer(self):
        self.assertTrue(self.run_fake()['success'])

    def test_process_success_is_not_answer_success(self):
        for kwargs in [dict(status='pending'), dict(status='send_failed'), dict(answer='Thinking'),
                       dict(prompt='stale'), dict(url='https://gemini.google.com/app'), dict(exit_code=1)]:
            output = self.run_fake(**kwargs)
            self.assertFalse(output['success'], kwargs)
            self.assertEqual(output['answer'], '')

    def test_timeout_cleans_owned_group_only(self):
        with patch.object(runtime.subprocess, 'Popen') as popen, patch.object(runtime.os, 'killpg') as kill:
            popen.return_value.pid = 123456
            popen.return_value.communicate.side_effect = [subprocess.TimeoutExpired('test', 1), ('', None)]
            output = runtime.run_provider('gemini', 'new', 'missing', 'task', timeout=1)
        kill.assert_called_once_with(123456, runtime.signal.SIGTERM)
        self.assertEqual(output['error'], 'provider_timeout')


async def request(path, payload):
    """Minimal ASGI client keeps unit tests independent of optional httpx."""
    sent = []
    body = json.dumps(payload).encode()
    async def receive():
        return {'type': 'http.request', 'body': body, 'more_body': False}
    async def send(message):
        sent.append(message)
    await gateway_app.app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                           'method': 'POST', 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
                           'query_string': b'', 'headers': [(b'content-type', b'application/json')],
                           'client': ('127.0.0.1', 1), 'server': ('127.0.0.1', 80), 'root_path': ''}, receive, send)
    return sent[0]['status'], json.loads(b''.join(x.get('body', b'') for x in sent[1:]))


class GatewayTests(unittest.TestCase):
    def test_auto_real_prompt_and_structured_response(self):
        expected = {'success': True, 'route_id': 'test', 'stages': [], 'final_answer': '4', 'duration_ms': 2}
        with patch.object(gateway_app, 'run_orchestration', return_value=expected) as run:
            status, data = asyncio.run(request('/api/orchestrate', {'prompt': 'REAL INPUT'}))
        self.assertEqual(status, 200)
        run.assert_called_once_with('REAL INPUT')
        self.assertEqual({k: v for k, v in data.items() if k != "task_id"}, expected)
        self.assertTrue(data.get("task_id"))
        self.assertNotIn('output', data)

    def test_invalid_inputs(self):
        for prompt in ['', '  ', '\x00']:
            self.assertEqual(asyncio.run(request('/api/orchestrate', {'prompt': prompt}))[0], 422)
        self.assertEqual(asyncio.run(request('/api/chat', {'provider': 'other', 'action': 'new', 'prompt': 'task'}))[0], 422)

    def test_chat_new_and_ask_both_providers(self):
        for provider in ['gemini', 'chatgpt']:
            for action in ['new', 'ask']:
                with patch.object(gateway_app, '_run_provider', return_value=result()) as run:
                    status, data = asyncio.run(request('/api/chat', {'provider': provider, 'action': action, 'conversation_name': 'keep', 'prompt': 'task'}))
                self.assertEqual(status, 200)
                self.assertTrue(data['success'])
                run.assert_called_once_with(provider, action, 'keep', 'task')


if __name__ == '__main__':
    unittest.main()
