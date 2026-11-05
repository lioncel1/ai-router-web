"""v1.2 Phase 4: configurable Auto workflow, explicit API transport and audit.

Offline tests: no calls to any real provider, browser, or API.
"""
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import gateway_app as gw
import orchestrator as orch
from auto_task_service import create_auto_task, execute_auto_task, read_auto_job
from database import connection, repository
from task_manager import task_manager


STAGES = [
    {'provider': 'gemini', 'role': 'analysis'},
    {'provider': 'deepseek', 'role': 'review', 'model': 'deepseek-flash'},
    {'provider': 'chatgpt', 'role': 'synthesis'},
]


def fake_result(provider, prompt):
    api = provider == 'deepseek'
    return {
        'success': True, 'transport_success': True, 'wrapper_success': True,
        'quality_ok': True, 'quality_reason': 'ok', 'answer': provider + ' answer',
        'answer_length': len(provider + ' answer'), 'conversation_url': '' if api else 'https://example.org/mock',
        'duration_ms': 12, 'error': '', 'exit_code': 0,
        'usage': {'prompt_tokens': 13, 'completion_tokens': 8, 'total_tokens': 21} if api else {},
        'finish_reason': 'stop' if api else '',
    }


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as directory:
        with patch.object(connection, 'DB_FILE', Path(directory) / 'test.sqlite3'):
            connection.init_db()
            with gw.ORCHESTRATION_JOBS_LOCK:
                gw.ORCHESTRATION_JOBS.clear()
            yield


def test_mixed_auto_three_stages_usage_and_context(monkeypatch):
    calls = []
    intents = []

    def provider(p, action, name, prompt, **kw):
        calls.append((p, action, prompt, kw))
        return fake_result(p, prompt)

    monkeypatch.setattr(orch, 'run_provider', provider)
    monkeypatch.setattr(orch, 'save_log', lambda _: None)
    result = orch.run_orchestration('原始问题', STAGES, before_dispatch=lambda **kw: intents.append(kw))
    assert result['success'] is True
    assert len(calls) == len(intents) == 3
    assert [p for p, _, _, _ in calls] == ['gemini', 'deepseek', 'chatgpt']
    assert 'gemini answer' in calls[1][2]
    assert 'deepseek answer' in calls[2][2]
    assert calls[1][3]['transport'] == 'api'
    assert calls[1][3]['model'] == 'deepseek-flash'
    assert 'transport' not in calls[0][3]
    assert result['usage'] == {'prompt_tokens': 13, 'completion_tokens': 8, 'total_tokens': 21}
    assert result['stages'][1]['usage']['total_tokens'] == 21


def test_api_unknown_never_retries_or_forwards(monkeypatch):
    calls = []
    def provider(p, action, name, prompt, **kw):
        calls.append(p)
        if p == 'deepseek':
            return {**fake_result(p, prompt), 'success':False, 'transport_success':False,
                    'wrapper_success':False,'quality_ok':False, 'error':'api_outcome_unknown',
                    'quality_reason':'api_outcome_unknown', 'answer':''}
        return fake_result(p,prompt)
    monkeypatch.setattr(orch, 'run_provider', provider)
    monkeypatch.setattr(orch,'save_log',lambda _: None)
    res = orch.run_orchestration('测试',STAGES,before_dispatch=lambda **kw: None)
    assert calls == ['gemini','deepseek']
    assert not res['success']
    assert len(res['stages'][1]['attempts']) == 1
    assert res['stages'][1]['quality_reason'] == 'api_outcome_unknown'


def test_api_definite_http_error_also_not_retried(monkeypatch):
    calls = []
    def provider(p, action, name, prompt, **kw):
        calls.append(p)
        return {**fake_result(p,prompt), 'success':False, 'quality_ok':False,
                'error':'api_rate_limited','quality_reason':'api_rate_limited'}
    monkeypatch.setattr(orch, 'run_provider', provider)
    monkeypatch.setattr(orch,'save_log',lambda _: None)
    result = orch.run_orchestration('测试', [{'provider':'deepseek', 'model':'deepseek-flash','role':'synthesis'}])
    assert not result['success']
    assert calls == ['deepseek']


def test_custom_web_uncertain_does_not_retry(monkeypatch):
    calls=[]
    def provider(p,a,n,prompt,**kw):
        calls.append(p)
        return {**fake_result(p,prompt), 'success':False, 'transport_success':False,
                'quality_ok':False, 'quality_reason':'transport_failed', 'error':'transport_failed'}
    monkeypatch.setattr(orch,'run_provider',provider)
    monkeypatch.setattr(orch,'save_log',lambda _: None)
    res=orch.run_orchestration('测试', STAGES, before_dispatch=lambda **kw: None)
    assert len(calls)==1 and not res['success']


def test_failed_dispatch_intent_refuses_model_call(monkeypatch):
    monkeypatch.setattr(orch,'run_provider',lambda *a,**k: pytest.fail('provider must not be called'))
    def crash(**kw):raise OSError('disk full')
    with pytest.raises(OSError):
        orch.run_orchestration('测试', STAGES, before_dispatch=crash)


def test_result_sqlite_transport_model_usage_and_intents(temp_db,monkeypatch):
    monkeypatch.setattr(orch,'save_log',lambda _:None)
    monkeypatch.setattr(orch,'run_provider',lambda p,a,n,prompt,**kw:fake_result(p,prompt))
    tid='phase4-task-one'
    create_auto_task('用户原始问题',tid,stages=STAGES)
    flow=gw._auto_workflow(tid,STAGES)
    output=execute_auto_task(tid,'用户原始问题',flow)
    assert output['success'] and output['usage']['total_tokens']==21
    assert task_manager.get(tid)['status']=='done'
    calls=repository.get_provider_calls(tid)
    assert [(c['provider'],c['transport']) for c in calls]==[
        ('gemini','web'),('deepseek','api'),('chatgpt','web')]
    api_meta=json.loads(calls[1]['metadata_json'])
    assert api_meta['model']=='deepseek-flash'
    assert api_meta['usage']['prompt_tokens']==13
    assert len([e for e in repository.get_task_events(tid) if e['event_type']=='auto_dispatch_intent'])==3
    assert all('用户原始问题' not in e['event_data_json'] for e in repository.get_task_events(tid)
               if e['event_type']=='auto_dispatch_intent')
    assert read_auto_job(tid)['result']['usage']['total_tokens']==21
    assert json.loads(task_manager.get(tid)['metadata_json'])['stages']==STAGES


def test_input_rejection_before_dispatch(temp_db):
    with TestClient(gw.app) as client:
        bad=[
            {'prompt':'test','stages':STAGES[:2]},
            {'prompt':'test','stages':[STAGES[1],STAGES[0],STAGES[2]]},
            {'prompt':'test','stages':[{**STAGES[0],'model':'deepseek-flash'},*STAGES[1:]]},
            {'prompt':'test','stages':[{**STAGES[1],'provider':'invalid'},*STAGES[1:]]},
        ]
        for payload in bad:
            assert client.post('/api/orchestrate/start',json=payload).status_code==422
        assert repository.list_tasks(limit=30)==[]


def test_deepseek_not_configured_refuses_before_task(temp_db):
    with patch.object(gw,'is_configured',return_value=False):
        with TestClient(gw.app) as client:
            assert client.post('/api/orchestrate/start',json={'prompt':'no key','stages':STAGES}).status_code==503
        assert repository.list_tasks(limit=30)==[]


def test_async_custom_job_persists_and_does_not_create_chat_tasks(temp_db,monkeypatch):
    monkeypatch.setattr(orch,'save_log',lambda _: None)
    calls=[]
    def provider(p,a,n,prompt,**kw):
        calls.append(p)
        return fake_result(p,prompt)
    monkeypatch.setattr(orch,'run_provider',provider)
    with patch.object(gw,'is_configured',return_value=True):
        with TestClient(gw.app) as client:
            start=client.post('/api/orchestrate/start',json={'prompt':'三阶段测试','stages':STAGES})
            assert start.status_code==200, start.text
            job_id=start.json()['job_id']
            for _ in range(100):
                res=client.get('/api/orchestrate/status/'+job_id).json()
                if res['status'] in ('done','failed'):
                    break
                time.sleep(.02)
            assert res['status']=='done',res
    assert calls==['gemini','deepseek','chatgpt']
    with gw.ORCHESTRATION_JOBS_LOCK: gw.ORCHESTRATION_JOBS.clear()
    restored=gw.read_auto_job(job_id)
    assert restored['result']['usage']['total_tokens']==21
    assert [t['task_type'] for t in repository.list_tasks(limit=20)]==['auto']


def test_default_request_keeps_original_workflow(temp_db):
    expected={'success':True,'route_id':'test','stages':[],'final_answer':'ok','duration_ms':1}
    with patch.object(gw,'run_orchestration',return_value=expected) as run:
        with TestClient(gw.app) as client:
            result=client.post('/api/orchestrate',json={'prompt':'默认测试'})
        assert result.status_code==200
    run.assert_called_once_with('默认测试')
    assert result.json()['final_answer']=='ok'


def test_custom_ui_uses_expected_three_selectors():
    html=(Path(__file__).resolve().parents[1]/'templates/index.html').read_text()
    for key in ('autoAnalysis','autoReview','autoSynthesis','autoAnalysisModel','autoReviewModel','autoSynthesisModel'):
        assert f'id="{key}"' in html
    assert 'JSON.stringify(standard ? {prompt} : {prompt, stages})' in html
    assert 'DeepSeek Token 合计' in html
