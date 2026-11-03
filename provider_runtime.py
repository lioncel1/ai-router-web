"""Provider execution through the existing locked wrappers, with DB evidence."""
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from quality import check_quality
from router_log import ANSI_ESCAPE_RE, extract_answer, extract_conversation_url, is_valid_conversation_url

BASE_DIR = Path(__file__).resolve().parent
PROVIDERS = {'gemini', 'chatgpt'}


def run_provider(provider, action, conversation_name, prompt, *, role='answer', task=None, timeout=300):
    if provider not in PROVIDERS or action not in {'new', 'ask'}:
        raise ValueError('unsupported provider or action')
    started = time.monotonic()
    stdout = ''
    exit_code = 1
    error = ''
    try:
        # A separate group lets a timeout stop ONLY this invocation and its wrapper
        # children. The pre-existing Chrome/CDP processes are never signalled.
        proc = subprocess.Popen(
            [str(BASE_DIR / 'router_call.sh'), provider, action, conversation_name, prompt],
            cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', start_new_session=True,
        )
        try:
            stdout, _ = proc.communicate(timeout=max(0.01, timeout))
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                stdout, _ = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                stdout, _ = proc.communicate()
            exit_code, error = 124, 'provider_timeout'
    except OSError:
        error = 'provider_launch_failed'

    clean = ANSI_ESCAPE_RE.sub('', stdout)
    answer = extract_answer(clean, provider)
    url = extract_conversation_url(clean, provider)
    transport_ok = exit_code == 0
    try:
        db = json.loads((BASE_DIR / 'data' / f'{provider}_conversations.json').read_text())
        record = db.get(conversation_name, {})
    except (OSError, ValueError):
        record = {}
    wrapper_ok = (
        record.get('status') == 'done' and record.get('last_prompt') == prompt
        and bool(answer) and record.get('last_answer', '').strip() == answer.strip()
        and record.get('url') == url and is_valid_conversation_url(url, provider)
    )
    quality_ok, reason = check_quality(
        answer, task=prompt if task is None else task, role=role,
        transport_ok=transport_ok, wrapper_ok=wrapper_ok,
        evidence=record.get('response_evidence'),
    )
    return {
        'success': quality_ok, 'transport_success': transport_ok,
        'wrapper_success': wrapper_ok, 'quality_ok': quality_ok,
        'quality_reason': reason, 'exit_code': exit_code,
        'answer': answer if quality_ok else '', 'answer_length': len(answer),
        'conversation_url': url if is_valid_conversation_url(url, provider) else '',
        'conversation_status': record.get('status', 'missing'),
        'error': '' if quality_ok else (error or reason),
        'duration_ms': round((time.monotonic() - started) * 1000),
    }
