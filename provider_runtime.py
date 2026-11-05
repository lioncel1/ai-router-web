"""Provider execution through the existing locked wrappers, with DB evidence."""
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from quality import check_quality
from provider_transport import dispatch_provider
from provider_transport import TransportUnavailable
import deepseek_api
from router_log import ANSI_ESCAPE_RE, extract_answer, extract_conversation_url, is_valid_conversation_url

BASE_DIR = Path(__file__).resolve().parent
PROVIDERS = {'gemini', 'chatgpt', 'deepseek'}


# AI_ROUTER_V010_PHASE1_PROVIDER_DISPATCH
# AI_ROUTER_V12_DEEPSEEK_SINGLE_TURN
def run_provider(provider, action, conversation_name, prompt, *,
                 role='answer', task=None, timeout=300, transport='web', model=None, messages=None):
    """Keep Web routing intact; enable one explicit DeepSeek API call."""
    if provider not in PROVIDERS or action not in {'new', 'ask'}:
        raise ValueError('unsupported provider or action')
    api_call = None
    if provider == 'deepseek':
        # AI_ROUTER_V12_DEEPSEEK_SESSIONS
        if transport != 'api' or (action == 'ask' and messages is None):
            raise TransportUnavailable('DeepSeek single-turn API request only unless verified history; request not sent')
        selected_model = model or 'deepseek-flash'
        if selected_model not in deepseek_api.ALLOWED_MODELS:
            raise ValueError('unsupported DeepSeek model')
        def api_call(_provider, _action, _name, question, *, role, task, timeout):
            if messages is None:
                return deepseek_api.complete(question, model=selected_model, timeout=timeout)
            return deepseek_api.complete(question, model=selected_model, timeout=timeout, messages=messages)
    return dispatch_provider(
        provider, action, conversation_name, prompt,
        transport=transport, web_call=_run_web_provider, api_call=api_call,
        role=role, task=task, timeout=timeout,
    )


def _run_web_provider(provider, action, conversation_name, prompt, *, role='answer', task=None, timeout=300):
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
