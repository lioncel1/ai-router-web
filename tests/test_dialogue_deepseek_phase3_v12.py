"""Phase 3: mixed Web + DeepSeek API Dialogue; all external calls are mocked.

DeepSeek session locking/persistence is REAL SQLite, but provider network never runs.
"""
import json
import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import gateway_app as gw
import dialogue_task_service as dialogue
import deepseek_session_service as deepseek
from database import connection, repository

GEMINI_URL = "https://gemini.google.com/app/test-phase3"
CHATGPT_URL = "https://chatgpt.com/c/test-phase3"


def web_result(provider, answer=None):
    return {"success": True, "answer": answer or f"{provider}-answer", "quality_ok": True,
            "quality_reason": "ok", "answer_length": 10, "duration_ms": 12,
            "conversation_url": GEMINI_URL if provider == "gemini" else CHATGPT_URL}


def api_result(answer="DeepSeek answer"):
    return {"success": True, "answer": answer, "quality_ok": True, "quality_reason": "ok",
            "answer_length": len(answer), "duration_ms": 8, "transport_success": True,
            "wrapper_success": True, "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6},
            "model": "deepseek-flash"}


def await_done(client, jid):
    for _ in range(200):
        data = client.get("/api/dialogue/status/" + jid).json()
        if data["status"] in {"done", "stopped", "failed"}:
            return data
        time.sleep(.02)
    pytest.fail("Dialogue job did not finish")


def request(first="gemini", second="deepseek", count=2, model="deepseek-flash"):
    return {"first_provider": first, "second_provider": second, "prompt": "START",
            "max_messages": count, "deepseek_model": model}


def test_gemini_then_deepseek_and_resume_api_history():
    seen_web, api_context = [], []
    def fake_web(provider, action, name, prompt):
        seen_web.append((provider, action, name, prompt))
        return web_result(provider)
    def fake_api(provider, action, name, prompt, **kwargs):
        api_context.append((action, prompt, kwargs["messages"]))
        return api_result("DeepSeek says yes" if len(api_context) == 1 else "DeepSeek second")

    with patch.object(gw, "is_configured", return_value=True), \
         patch.object(deepseek, "require_config"), \
         patch.object(gw, "run_provider", side_effect=fake_web), \
         patch.object(deepseek, "run_provider", side_effect=fake_api), \
         patch.object(dialogue, "load_conversations", return_value={} ) as loader, \
         TestClient(gw.app) as client:
        root = client.post("/api/dialogue/start", json=request())
        assert root.status_code == 200, root.text
        jid = root.json()["job_id"]
        result = await_done(client, jid)
        assert result["status"] == "done", result
        assert [t["provider"] for t in result["messages"]] == ["gemini", "deepseek"]
        assert len(repository.get_provider_calls(jid)) == 2
        assert [p["transport"] for p in repository.get_provider_calls(jid)] == ["web", "api"]
        assert [m["transport"] for m in repository.get_messages(jid) if m["role"] == "assistant"] == ["web", "api"]
        assert api_context[0][0] == "new" and api_context[0][1] == "gemini-answer"
        assert api_context[0][2] == [{"role":"user", "content":"gemini-answer"}]
        name = next(t["conversation_name"] for t in result["messages"] if t["provider"] == "deepseek")
        assert deepseek.read_session(name)["session"]["status"] == "ready"
        assert deepseek.read_session(name)["session"]["model"] == "deepseek-flash"
        # Web cache evidence is checked on continuation; simulate the existing matching cache.
        loader.return_value = {seen_web[0][2]: {"url": GEMINI_URL}}
        prep = client.get("/api/dialogue/prepare/" + jid)
        assert prep.status_code == 200, prep.text
        assert prep.json()["next_provider"] == "gemini"
        assert prep.json()["default_prompt"] == "DeepSeek says yes"
        child = client.post("/api/dialogue/continue/" + jid, json={"prompt":"EDIT", "additional_messages":2})
        assert child.status_code == 200, child.text
        child_id = child.json()["job_id"]
        continued = await_done(client, child_id)
        assert continued["status"] == "done", continued
        assert [t["provider"] for t in continued["messages"]] == ["gemini", "deepseek", "gemini", "deepseek"]
        assert seen_web[-1][1] == "ask" and seen_web[-1][-1] == "EDIT"
        assert api_context[-1][0] == "ask"
        assert api_context[-1][1] == "gemini-answer"
        assert api_context[-1][2] == [
            {"role":"user","content":"gemini-answer"},
            {"role":"assistant","content":"DeepSeek says yes"},
            {"role":"user","content":"gemini-answer"},
        ]
        assert [p["transport"] for p in repository.get_provider_calls(child_id)] == ["web", "api"]
        assert client.post("/api/dialogue/continue/" + jid, json={"prompt":"EDIT", "additional_messages":2}).status_code == 409
        assert len(api_context) == 2


def test_deepseek_first_then_chatgpt_and_model_persisted():
    seen = []
    def fake_web(provider, action, name, prompt):
        seen.append((provider, prompt))
        return web_result(provider)
    with patch.object(gw,"is_configured",return_value=True), patch.object(deepseek,"require_config"), \
         patch.object(deepseek,"run_provider",return_value=api_result()), \
         patch.object(gw,"run_provider",side_effect=fake_web), TestClient(gw.app) as client:
        response=client.post('/api/dialogue/start',json=request(first='deepseek', second='chatgpt',model='deepseek-v4-pro'))
        assert response.status_code==200, response.text
        jid=response.json()['job_id']
        data=await_done(client,jid)
        assert data['status']=='done',data
        assert [t['provider'] for t in data['messages']]==['deepseek','chatgpt']
        assert seen==[('chatgpt','DeepSeek answer')]
        assert client.get('/api/dialogue/prepare/'+jid).json()['deepseek_model']=='deepseek-v4-pro'
        assert [r['transport'] for r in repository.get_provider_calls(jid)]==['api','web']


def test_uncertain_api_result_quarantines_and_never_calls_other_model():
    seen=[]
    with patch.object(gw,'is_configured',return_value=True),patch.object(deepseek,'require_config'),\
         patch.object(deepseek,'run_provider', return_value={"success":False,"error":"api_outcome_unknown","answer":""}),\
         patch.object(gw,'run_provider',side_effect=lambda *args: seen.append(args)):\
        # TestClient startup reconciliation will not replay anything.
        with TestClient(gw.app) as client:
            jid=client.post('/api/dialogue/start',json=request(first='deepseek',second='gemini')).json()['job_id']
            data=await_done(client,jid)
            assert data['status']=='failed',data
            assert data['completed_messages']==0 and not seen
            name=f'dialogue-{jid[:8]}-deepseek'
            assert deepseek.read_session(name)['session']['status']=='uncertain'
            assert client.get('/api/dialogue/prepare/'+jid).status_code==409
            assert [r['status'] for r in repository.get_provider_calls(jid)]==['failed']


def test_ready_api_history_becoming_uncertain_blocks_resume_before_send():
    with patch.object(gw,'is_configured',return_value=True),patch.object(deepseek,'require_config'),\
         patch.object(deepseek,'run_provider',return_value=api_result()),TestClient(gw.app) as client:
        jid=client.post('/api/dialogue/start',json=request(first='deepseek',second='gemini',count=1)).json()['job_id']
        assert await_done(client,jid)['status']=='done'
        name=f'dialogue-{jid[:8]}-deepseek'
        with connection.get_connection() as db:
            db.execute('UPDATE deepseek_sessions SET status=? WHERE name=?',('uncertain',name))
            db.commit()
        with patch.object(gw,'run_provider') as web, patch.object(deepseek,'run_provider') as api:
            r=client.post('/api/dialogue/continue/'+jid,json={'prompt':'NEXT','additional_messages':2})
            assert r.status_code==409,r.text
            web.assert_not_called(); api.assert_not_called()
        assert repository.get_task(str(dialogue.uuid.uuid5(dialogue._RESUME_NS,jid))) is None


def test_stop_during_deepseek_call_stops_before_next_web():
    started=threading.Event(); release=threading.Event(); web=[]
    def blocking(*args,**kwargs):
        started.set()
        assert release.wait(3)
        return api_result()
    with patch.object(gw,'is_configured',return_value=True),patch.object(deepseek,'require_config'),\
         patch.object(deepseek,'run_provider',side_effect=blocking),\
         patch.object(gw,'run_provider',side_effect=lambda *args:web.append(args)):
        with TestClient(gw.app) as client:
            jid=client.post('/api/dialogue/start',json=request(first='deepseek',second='gemini')).json()['job_id']
            assert started.wait(2)
            stopped=client.post('/api/dialogue/stop/'+jid)
            assert stopped.status_code==200 and stopped.json()['stop_requested']
            release.set()
            state=await_done(client,jid)
            assert state['status']=='stopped' and state['completed_messages']==1 and web==[]


def test_no_key_or_duplicate_providers_are_rejected_before_job_created():
    with patch.object(gw,'is_configured',return_value=False),patch.object(gw,'run_provider') as web,\
         patch.object(gw,'execute_session') as api,TestClient(gw.app) as client:
        assert client.post('/api/dialogue/start',json=request()).status_code==503
        assert client.post('/api/dialogue/start',json=request(first='deepseek',second='deepseek')).status_code==400
        assert client.post('/api/dialogue/start',json=request(first='gemini',second='fake')).status_code==422
        web.assert_not_called(); api.assert_not_called()


def test_web_recovery_of_mixed_job_remains_available_without_api_resend():
    import dialogue_recovery as recovery
    connection.init_db()
    jid='mixed-web-fail'
    dialogue.create_dialogue_task(jid,'gemini','deepseek','Q',2)
    dialogue.start_dialogue_task(jid)
    dialogue.record_dialogue_dispatch_intent(jid,index=1,provider='gemini',action='new',conversation_name='c1',prompt='Q')
    dialogue.record_dialogue_step(jid,index=1,provider='gemini',action='new',prompt='Q',conversation_name='c1',
        result={'success':False,'answer':'','error':'transport_failed'})
    dialogue.fail_dialogue_task(jid,code='provider_failed',reason='transport_failed')
    with patch.object(recovery,'load_conversations',return_value={'c1':{'url':GEMINI_URL,'status':'pending','last_prompt':'Q','baseline_count':0}}):
        candidate=recovery._candidate(jid)
    assert candidate['provider']=='gemini' and candidate['next_provider']=='deepseek'


def test_mixed_dialogue_records_usage_without_spurious_chat_tasks():
    with patch.object(gw,'is_configured',return_value=True),patch.object(deepseek,'require_config'), \
         patch.object(deepseek,'run_provider',return_value=api_result()), \
         patch.object(gw,'run_provider',return_value=web_result('gemini')), TestClient(gw.app) as client:
        jid=client.post('/api/dialogue/start',json=request()).json()['job_id']
        assert await_done(client,jid)['status']=='done'
        task=repository.get_task(jid)
        assert task['source']=='mixed'
        records=repository.list_tasks(limit=100)
        assert len([x for x in records if x['task_type']=='chat'])==0
        calls=repository.get_provider_calls(jid)
        meta=json.loads(calls[-1]['metadata_json'])
        assert meta['usage']['total_tokens']==6
        intents=[json.loads(x['event_data_json']) for x in repository.get_task_events(jid)
                 if x['event_type']=='dialogue_dispatch_intent']
        assert [x['transport'] for x in intents]==['web','api']


def test_restart_quarantines_api_inflight_without_sending():
    connection.init_db()
    jid='mixed-inflight'
    dialogue.create_dialogue_task(jid,'deepseek','gemini','hi',3)
    dialogue.start_dialogue_task(jid)
    dialogue.record_dialogue_dispatch_intent(jid,index=1,provider='deepseek',action='new',
                                            conversation_name='dialogue-inflight-deepseek',prompt='hi')
    deepseek._reserve(task_id=jid,name='dialogue-inflight-deepseek',model='deepseek-flash',
                      action='new',prompt='hi')
    assert deepseek.reconcile_interrupted_sessions()==1
    assert dialogue.reconcile_interrupted_dialogue_tasks()==1
    assert repository.get_task(jid)['status']=='failed'
    assert deepseek.read_session('dialogue-inflight-deepseek')['session']['status']=='uncertain'
    with patch.object(deepseek,'run_provider') as net:
        with pytest.raises(deepseek.SessionConflict):
            deepseek._reserve(task_id='another',name='dialogue-inflight-deepseek',model='deepseek-flash',
                              action='ask',prompt='repeat?')
        net.assert_not_called()


def test_dialogue_ui_has_deepseek_combo_and_model_payload():
    from pathlib import Path
    html=(Path(__file__).resolve().parents[1]/'templates/index.html').read_text(encoding='utf-8')
    assert html.count('<option value="deepseek">DeepSeek API</option>') == 2
    assert 'id="dialogueDeepseekModel"' in html
    assert "deepseek_model: $('dialogueDeepseekModel').value" in html
