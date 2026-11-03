"""Offline browser checks for recovery controls and safe answer rendering."""
from pathlib import Path
import shutil
import pytest
from playwright.sync_api import sync_playwright

@pytest.mark.skipif(not (shutil.which("chromium") or shutil.which("google-chrome")), reason="Chrome unavailable")
def test_failed_task_recovery_requires_confirmation_and_does_not_autosend():
    source = (Path(__file__).resolve().parents[1] / "templates/index.html").read_text()
    stub = '''<script>
window.confirmedRecovery = null;
window.sentContinuation = null;
window.fetch = async (url, opts={}) => {
 const u=String(url);let value;
 if(u.startsWith('/api/tasks?')) value={items:[{task_id:'fail-id', task_type:'dialogue',title:'Dialogue',status:'failed',created_at:'today'}],total:1};
 else if(u.startsWith('/api/tasks/')) value={task:{task_id:'fail-id',task_type:'dialogue',status:'failed',created_at:'today',original_input:'Q'},messages:[],events:[],provider_calls:[]};
 else if(u.startsWith('/api/dialogue/recovery-status/')) value={already_recovered:false};
 else if(u.startsWith('/api/dialogue/recover/') && opts.method==='POST') {window.confirmedRecovery=JSON.parse(opts.body);value={recovered_job_id:'checkpoint-id',status:'stopped'};}
 else if(u.startsWith('/api/dialogue/recover/')) value={provider:'gemini',index:3, original_prompt:'Q',answer:'<img src=x onerror=window.xss=1>',answer_hash:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',conversation_url:'https://gemini.google.com/app/ok',warning:'人工确认'};
 else if(u.startsWith('/api/dialogue/prepare/')) value={source_job_id:'checkpoint-id',next_provider:'chatgpt',next_action:'ask',completed_messages:3,default_prompt:'Recovered'};
 else if(u.startsWith('/api/dialogue/continue/')) {window.sentContinuation=JSON.parse(opts.body);value={job_id:'next-id',status:'queued'};}
 else if(u.startsWith('/api/dialogue/status/')) value={status:'done',messages:[],max_messages:4,completed_messages:4};
 else value={conversations:[]};
 return new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
};
</script>'''
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=shutil.which('chromium') or shutil.which('google-chrome'), headless=True, args=['--no-sandbox'])
        try:
            page=browser.new_page()
            page.set_content(source.replace('<body>','<body>\n'+stub,1),wait_until='load')
            page.locator('#taskRows button').click()
            page.get_by_role('button',name='核对网页回答并恢复（不重新发送）').click()
            page.get_by_text('从原网页读取的候选回答（请仔细核对）').wait_for()
            assert page.evaluate('window.xss') is None
            button=page.get_by_role('button',name='确认保存恢复检查点')
            assert button.is_disabled()
            page.get_by_text('我已核对：这确实是本轮问题的回复，且没有被转发给下一模型').click()
            assert button.is_enabled()
            button.click()
            page.wait_for_function('window.confirmedRecovery !== null')
            assert page.evaluate('window.confirmedRecovery') == {'answer_hash':'a'*64,'confirmed':True}
            assert page.evaluate('window.sentContinuation') is None
            page.get_by_role('button',name='从已恢复回答继续对话').click()
            page.locator('#dialogueResumePrompt').fill('Edited follow-up')
            page.locator('#dialogueResumeCount').fill('1')
            page.locator('#confirmDialogueResume').click()
            page.wait_for_function('window.sentContinuation !== null')
            assert page.evaluate('window.sentContinuation')['prompt'] == 'Edited follow-up'
        finally:
            browser.close()

@pytest.mark.skipif(not (shutil.which("chromium") or shutil.which("google-chrome")), reason="Chrome unavailable")
def test_gemini_rich_card_without_copy_is_read_as_completed_model_body():
    from web_response import snapshot
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=shutil.which('chromium') or shutil.which('google-chrome'),headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page()
            page.set_content('<model-response><message-content><div class="markdown">洛杉矶天气：局部多云，15°C</div></message-content></model-response>')
            assert snapshot(page, 'gemini') == (1, '洛杉矶天气：局部多云，15°C', True)
            page.set_content('<button data-testid="stop-button">Stop</button><model-response><message-content><div class="markdown">尚在生成</div></message-content></model-response>')
            assert snapshot(page,'gemini')[2] is False
        finally:
            browser.close()
