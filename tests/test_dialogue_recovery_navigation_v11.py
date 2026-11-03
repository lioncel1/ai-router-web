"""Read-only recovery navigation and UI tests; no model calls or real provider pages."""
import uuid
from unittest.mock import patch
from fastapi.testclient import TestClient
from database import connection, repository
import gateway_app as gw
import dialogue_task_service as svc


def test_recovered_failure_links_to_latest_continuation_without_sending():
    connection.init_db()
    source = 'failed-ui-source'
    svc.create_dialogue_task(source, 'gemini', 'chatgpt', 'Q', 2)
    svc.start_dialogue_task(source)
    svc.fail_dialogue_task(source, code='provider_failed', reason='timeout')
    with TestClient(gw.app) as client, patch.object(gw, 'run_provider') as caller:
        notyet = client.get(f'/api/dialogue/recovery-status/{source}')
        assert notyet.status_code == 200 and notyet.json()['already_recovered'] is False
        checkpoint_id = str(uuid.uuid5(svc._RESUME_NS, source + ':confirmed-web-recovery'))
        repository.create_dialogue_recovery_checkpoint(task_id=checkpoint_id, parent_job_id=source,
            root_job_id=source, first='gemini',second='chatgpt', provider='gemini',
            index=1, answer='Gemini recovered', conversation_name='gemini-session',
            conversation_url='https://gemini.google.com/app/demo',answer_hash='a'*64)
        check = client.get(f'/api/dialogue/recovery-status/{source}')
        assert check.status_code == 200
        assert check.json()['already_recovered'] is True
        assert check.json()['latest_job_id'] == checkpoint_id
        assert check.json()['can_continue'] is True
        with patch.object(svc, '_verify_existing_conversation'):
            new = svc.create_dialogue_continuation(checkpoint_id, 'edited', 1)['job_id']
        check = client.get(f'/api/dialogue/recovery-status/{source}')
        assert check.status_code == 200
        assert check.json()['latest_job_id'] == new
        assert check.json()['latest_status'] == 'queued'
        assert check.json()['can_continue'] is False
        svc.start_dialogue_task(new)
        svc.record_dialogue_step(new,index=2,provider='chatgpt',action='new',
            conversation_name='chatgpt-session',prompt='edited',
            result={'success':True,'answer':'ChatGPT answer', 'conversation_url':'https://chatgpt.com/c/demo', 'duration_ms':10})
        svc.complete_dialogue_task(new)
        final = client.get(f'/api/dialogue/recovery-status/{source}').json()
        assert final['latest_job_id'] == new
        assert final['latest_status'] == 'done' and final['can_continue'] is True
        caller.assert_not_called()


def test_failed_task_with_recovery_offers_latest_not_duplicate_button():
    from pathlib import Path
    import shutil
    from playwright.sync_api import sync_playwright
    chrome=shutil.which('chromium') or shutil.which('google-chrome')
    if not chrome:
        import pytest
        pytest.skip('Chrome unavailable')
    html = (Path(__file__).resolve().parents[1]/'templates/index.html').read_text()
    stub = '''<script>
window.sentToProvider=0;
window.fetch=async (url,opts={})=>{
let u=String(url), value;
if(u.startsWith('/api/tasks?'))value={items:[{task_id:'failure1',task_type:'dialogue',status:'failed',created_at:'now'}],total:1};
else if(u.startsWith('/api/tasks/failure1'))value={task:{task_id:'failure1',task_type:'dialogue',status:'failed',original_input:'Q'},messages:[],events:[],provider_calls:[]};
else if(u.startsWith('/api/tasks/child1'))value={task:{task_id:'child1',task_type:'dialogue',status:'stopped',original_input:'Q'},messages:[],events:[],provider_calls:[]};
else if(u.startsWith('/api/dialogue/recovery-status/failure1'))value={already_recovered:true,checkpoint_job_id:'checkpoint1',latest_job_id:'child1',latest_status:'stopped',can_continue:true};
else if(u.startsWith('/api/dialogue/prepare/child1'))value={source_job_id:'child1',next_provider:'gemini',next_action:'ask',completed_messages:6,default_prompt:'The last ChatGPT answer'};
else if(u.startsWith('/api/dialogue/recover/failure1')){window.sentToProvider++;value={};}
else value={conversations:[]};
return new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}})
}
</script>'''
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=chrome,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page()
            page.set_content(html.replace('<body>','<body>\n'+stub,1), wait_until='load')
            page.locator('#taskRows button').first.click()
            page.get_by_text('恢复检查点已创建',exact=False).wait_for()
            assert page.get_by_role('button',name='核对网页回答并恢复（不重新发送）').count()==0
            page.get_by_role('button',name='从最新后续对话继续').click()
            page.wait_for_function("document.getElementById('dialogueResumePrompt').value === 'The last ChatGPT answer'")
            assert page.evaluate('window.sentToProvider')==0
        finally:
            browser.close()
