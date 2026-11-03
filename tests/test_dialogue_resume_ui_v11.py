"""Dialogue continuation UI test (mock fetch, no provider/CDP/network)."""
import json
import shutil
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


@pytest.mark.skipif(not (shutil.which("chromium") or shutil.which("google-chrome")),
                    reason="Browser unavailable")
def test_task_center_can_edit_and_resume_dialogue():
    source = (Path(__file__).resolve().parents[1] / "templates" / "index.html").read_text(encoding="utf-8")
    stub = '''<script>
window.sentResume = null;
window.fetch = async (url, opts={}) => {
  const p = String(url);
  let data;
  if (p.startsWith('/api/tasks?')) data = {items:[{task_id:'parent-id',task_type:'dialogue',title:'Dialogue',status:'stopped',created_at:'today'}],total:1};
  else if (p.startsWith('/api/tasks/')) data = {task:{task_id:'parent-id',task_type:'dialogue',status:'stopped',created_at:'today',original_input:'hello'},messages:[],provider_calls:[],events:[]};
  else if (p.startsWith('/api/dialogue/prepare/')) data = {success:true,source_job_id:'parent-id',next_provider:'gemini',next_action:'ask',completed_messages:2,default_prompt:'<img src=x onerror=window.xss=1>'};
  else if (p.startsWith('/api/dialogue/continue/')) {window.sentResume=JSON.parse(opts.body);data={success:true,job_id:'child-id',status:'queued'};}
  else if (p.startsWith('/api/dialogue/status/')) data = {success:true,status:'done',max_messages:3,completed_messages:3,messages:[{index:3,provider:'gemini',answer:'ok'}]};
  else data = {conversations:[]};
  return new Response(JSON.stringify(data), {status:200,headers:{'Content-Type':'application/json'}});
};
</script>'''
    html = source.replace('<body>', '<body>\n'+stub,1)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=shutil.which("chromium") or shutil.which("google-chrome"),
                                    headless=True, args=['--disable-gpu','--no-sandbox'])
        try:
            page = browser.new_page()
            page.set_content(html,wait_until='load')
            page.locator('#taskRows button').click()
            page.get_by_role('button',name='从此任务继续对话').click()
            page.wait_for_function("document.querySelector('#dialogueResumePrompt').value === '<img src=x onerror=window.xss=1>'")
            assert page.evaluate('window.xss') is None
            page.locator('#dialogueResumePrompt').fill('我修改后的消息')
            page.locator('#dialogueResumeCount').fill('1')
            page.locator('#confirmDialogueResume').click()
            page.wait_for_function('window.sentResume !== null')
            assert page.evaluate('window.sentResume') == {'prompt':'我修改后的消息','additional_messages':1}
        finally:
            browser.close()
