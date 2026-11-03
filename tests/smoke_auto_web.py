"""Explicit opt-in Auto smoke through the actual served UI, once per evidence file."""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from router_log import is_valid_conversation_url

PROMPT = 'RC6-WEB-20260919：小林有3盒铅笔，每盒4支，送出5支后还剩多少支？最终只用一句中文回答，并在句末保留标识 RC6-WEB-20260919。'
EVIDENCE = ROOT / 'debug/rc6-auto-web.json'


def verify(data):
    assert data['success'], data.get('error')
    assert 'output' not in data
    assert [s['role'] for s in data['stages']] == ['analysis', 'review', 'synthesis']
    assert '7' in data['final_answer'] or '七' in data['final_answer'], data['final_answer']
    assert 'RC6-WEB-20260919' in data['final_answer'], data['final_answer']
    previous = []
    for stage in data['stages']:
        assert stage['success'] and stage['quality_ok']
        selected = stage['attempts'][stage['selected_attempt'] - 1]
        record = json.loads((ROOT / 'data' / f'{stage["provider"]}_conversations.json').read_text())[selected['conversation_name']]
        assert record['status'] == 'done' and record['last_answer'] == stage['answer']
        assert is_valid_conversation_url(record['url'], stage['provider'])
        assert PROMPT in record['last_prompt']
        assert all(s['answer'] in record['last_prompt'] for s in previous)
        previous.append(stage)


if __name__ == '__main__':
    if EVIDENCE.exists():
        data = json.loads(EVIDENCE.read_text())
        verify(data)
        print('Existing successful evidence verified; no model calls repeated.')
        raise SystemExit(0)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/google-chrome', headless=True, args=['--disable-gpu'])
        page = browser.new_page(viewport={'width': 1100, 'height': 850})
        errors = []
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.goto('http://127.0.0.1:8765/')
        page.select_option('#provider', 'auto')
        expect(page.locator('#normalFields')).to_be_hidden()
        page.fill('#prompt', PROMPT)
        EVIDENCE.write_text(json.dumps({'success': False, 'error': 'Live smoke pending: inspect the existing route before any resend.'}))
        print('Submitting one real Web Auto request.', flush=True)
        with page.expect_response('**/api/orchestrate', timeout=590000) as response:
            page.click('#send')
        data = response.value.json()
        EVIDENCE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        verify(data)
        expect(page.locator('#send')).to_be_enabled()
        expect(page.locator('#result')).to_contain_text(data['final_answer'])
        expect(page.locator('#result')).to_contain_text(data['route_id'])
        expect(page.locator('details')).to_have_count(3)
        assert all(not page.locator('details').nth(i).evaluate('e=>e.open') for i in range(3))
        assert not errors, errors
        page.screenshot(path=str(ROOT / 'debug/rc6-auto-web.png'), full_page=True)
        browser.close()
    print(json.dumps({'success': data['success'], 'route_id': data['route_id'], 'duration_ms': data['duration_ms'],
                      'stages': [{k: s[k] for k in ['provider', 'role', 'success', 'retry_count', 'duration_ms']} for s in data['stages']],
                      'final_answer': data['final_answer'], 'browser_errors': errors}, ensure_ascii=False), flush=True)
