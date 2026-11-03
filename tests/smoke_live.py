"""Explicit opt-in live smoke; fixed chat names reuse successful cached replies."""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from router_log import extract_answer, is_valid_conversation_url


def chat(provider):
    prompt, expected = {'gemini': ('请只回答数字：6乘7等于多少？', '42'),
                        'chatgpt': ('请只回答两个汉字：春天之后是什么季节？', '夏天')}[provider]
    name = f'rc6-smoke-20260919-{provider}'
    payload = {'provider': provider, 'action': 'new', 'conversation_name': name, 'prompt': prompt}
    req = urllib.request.Request('http://127.0.0.1:8765/api/chat', json.dumps(payload).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=330) as response:
        result = json.load(response)
    (ROOT / 'debug' / f'rc6-{provider}-smoke.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert result['success'], result
    assert result['answer'].strip().rstrip('。.') == expected, result
    record = json.loads((ROOT / 'data' / f'{provider}_conversations.json').read_text())[name]
    assert record['status'] == 'done' and record['last_prompt'] == prompt
    assert record['last_answer'] == result['answer']
    assert is_valid_conversation_url(record['url'], provider)
    # Standalone wrapper smoke is a cache hit: verify wrapper without a second web send.
    cached = subprocess.run([str(ROOT / f'{provider}.sh'), 'new', name, prompt], cwd=ROOT, text=True, capture_output=True, timeout=15)
    assert cached.returncode == 0 and '不重复调用' in cached.stdout, cached.stdout
    assert extract_answer(cached.stdout, provider) == result['answer']
    print(json.dumps({'provider': provider, 'api_success': True, 'standalone_cached_smoke': True,
                      'answer': result['answer'], 'duration_ms': result['duration_ms'], 'conversation_status': record['status'],
                      'conversation_url': record['url']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != 'chat' or sys.argv[2] not in {'gemini', 'chatgpt'}:
        raise SystemExit('usage: smoke_live.py chat gemini|chatgpt')
    chat(sys.argv[2])
