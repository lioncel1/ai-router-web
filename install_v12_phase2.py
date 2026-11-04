"""v1.2 Phase 2 install: DeepSeek durable multi-turn API sessions and Web Chat UI.

Use --check before --apply. Never touches user data or browser profiles. Source
backups are stored in ~/ai-router-archives outside the project Git checkout.
"""
from __future__ import annotations

import argparse
import datetime
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MARKER = "# AI_ROUTER_V12_DEEPSEEK_SESSIONS"


def _replace(text: str, old: str, new: str, filename: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"{filename}: expected one anchor, got {text.count(old)}")
    return text.replace(old, new, 1)


def patch_api(text: str) -> str:
    if MARKER in text:
        assert "messages: list" in text and '"messages": payload_messages' in text
        return text
    text = _replace(text, 'def complete(prompt: str, *, model: str = "deepseek-flash", timeout: float = 120, client=None) -> dict[str, Any]:',
        'def complete(prompt: str, *, model: str = "deepseek-flash", timeout: float = 120, client=None, messages: list[dict[str, str]] | None = None) -> dict[str, Any]:', 'deepseek_api.py')
    text = _replace(text, '    key = require_config()  # fail before dispatch', '''    # AI_ROUTER_V12_DEEPSEEK_SESSIONS
    payload_messages = messages if messages is not None else [{"role": "user", "content": prompt}]
    if not isinstance(payload_messages, list) or not payload_messages or len(payload_messages) > 500:
        raise ValueError("invalid API messages")
    if any(not isinstance(m, dict) or m.get("role") not in {"user", "assistant"}
           or not isinstance(m.get("content"), str) or not m["content"].strip()
           for m in payload_messages):
        raise ValueError("invalid API message content")
    if payload_messages[-1] != {"role": "user", "content": prompt}:
        raise ValueError("last API message must equal current prompt")
    if sum(len(m["content"]) for m in payload_messages) > 100_000:
        raise ValueError("API context exceeds safety limit; no message sent")
    key = require_config()  # fail before dispatch''', 'deepseek_api.py')
    text = _replace(text,'"messages": [{"role": "user", "content": prompt}],','"messages": payload_messages,','deepseek_api.py')
    return text


def patch_runtime(text: str) -> str:
    if MARKER in text:
        assert 'messages=messages' in text
        return text
    text = _replace(text,"role='answer', task=None, timeout=300, transport='web', model=None):",
        "role='answer', task=None, timeout=300, transport='web', model=None, messages=None):",'provider_runtime.py')
    text = _replace(text,"        if transport != 'api' or action != 'new':\n            raise TransportUnavailable('DeepSeek supports single-turn API only; request not sent')",
        "        # AI_ROUTER_V12_DEEPSEEK_SESSIONS\n        if transport != 'api' or (action == 'ask' and messages is None):\n            raise TransportUnavailable('DeepSeek single-turn API request only unless verified history; request not sent')",'provider_runtime.py')
    text = _replace(text,"            return deepseek_api.complete(question, model=selected_model, timeout=timeout)",
        "            if messages is None:\n                return deepseek_api.complete(question, model=selected_model, timeout=timeout)\n            return deepseek_api.complete(question, model=selected_model, timeout=timeout, messages=messages)",'provider_runtime.py')
    return text


def patch_gateway(text: str) -> str:
    if MARKER in text:
        assert '/api/deepseek/session/chat' in text and 'reconcile_interrupted_sessions' in text
        return text
    text = _replace(text,'from deepseek_task_service import reconcile_interrupted_deepseek_tasks\n',
        'from deepseek_task_service import reconcile_interrupted_deepseek_tasks\n'
        'from deepseek_session_service import (execute_session, list_sessions, read_session,\n'
        '    reconcile_interrupted_sessions, SessionConflict, SessionMissing)\n', 'gateway_app.py')
    anchor = '@app.post("/api/chat")\n'
    block = '''# AI_ROUTER_V12_DEEPSEEK_SESSIONS
class DeepSeekSessionChatRequest(BaseModel):
    action: Literal["new", "ask"] = "new"
    conversation_name: str = Field(default="", max_length=MAX_CONVERSATION_NAME_LENGTH)
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)
    model: Literal["deepseek-flash", "deepseek-v4-pro"] = "deepseek-flash"

    validate_conversation_name = field_validator("conversation_name")(ChatRequest.validate_conversation_name.__func__)
    validate_prompt = field_validator("prompt")(ChatRequest.validate_prompt.__func__)


@app.on_event("startup")
async def reconcile_api_sessions_on_startup():
    # Does not call any model. Old pending requests are quarantined on restart.
    await asyncio.to_thread(reconcile_interrupted_sessions)


@app.get("/api/deepseek/sessions")
async def deepseek_sessions():
    return {"sessions": await asyncio.to_thread(list_sessions)}


@app.get("/api/deepseek/sessions/{session_name}")
async def deepseek_session_detail(session_name: str):
    try:
        return await asyncio.to_thread(read_session, session_name)
    except SessionMissing:
        raise HTTPException(status_code=404, detail="DeepSeek session not found")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid DeepSeek session name")


@app.post("/api/deepseek/session/chat")
async def deepseek_session_chat(request: DeepSeekSessionChatRequest):
    if not is_configured():
        raise HTTPException(status_code=503, detail="DeepSeek API key not configured; request not sent")
    try:
        return await asyncio.to_thread(execute_session, request.prompt, request.action,
                                       request.conversation_name, request.model)
    except (SessionConflict, SessionMissing) as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid DeepSeek session request; not sent")
    except Exception:
        raise HTTPException(status_code=503, detail="Session request status uncertain; inspect Task Center before retry")


'''
    text = _replace(text, anchor, block + anchor, 'gateway_app.py')
    return text


def patch_html(text: str) -> str:
    if "AI_ROUTER_V12_DEEPSEEK_SESSIONS" in text:
        assert "deepseekModel" in text and "/api/deepseek/session/chat" in text
        return text
    text = _replace(text,'    <option value="chatgpt">ChatGPT</option>\n    <option value="auto">',
        '    <option value="chatgpt">ChatGPT</option>\n    <option value="deepseek">DeepSeek（API）</option>\n    <option value="auto">','templates/index.html')
    text = _replace(text, '  <div id="dialogueFields" class="hidden">', '''  <!-- AI_ROUTER_V12_DEEPSEEK_SESSIONS -->
  <div id="deepseekFields" class="hidden">
    <label for="deepseekModel">DeepSeek API Model（模型）</label>
    <select id="deepseekModel">
      <option value="deepseek-flash">deepseek-flash</option>
      <option value="deepseek-v4-pro">deepseek-v4-pro</option>
    </select>
    <p style="font-size:.9em;opacity:.75">API 会话保存在本机 SQLite，不关联官方网页；结果不明的会话禁止自动继续。</p>
  </div>

  <div id="dialogueFields" class="hidden">''', 'templates/index.html')
    text = _replace(text,"  $('dialogueFields').classList.toggle('hidden', mode !== 'dialogue');",
        "  $('dialogueFields').classList.toggle('hidden', mode !== 'dialogue');\n  $('deepseekFields').classList.toggle('hidden', mode !== 'deepseek');",'templates/index.html')
    text = _replace(text,"    } else {\n      const payload = {\n        provider,",'''    } else if (provider === 'deepseek') {
      const data = await readResponse(await fetch('/api/deepseek/session/chat', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({action: $('action').value, conversation_name: $('conversation').value,
                              prompt, model: $('deepseekModel').value})
      }));
      result.innerHTML = `<div class="meta ${data.success ? 'ok' : 'bad'}">` +
        `<strong>Provider：</strong>DeepSeek API<br>` +
        `<strong>会话：</strong>${esc(data.conversation_name)}<br>` +
        `<strong>模型：</strong>${esc(data.model)}<br>` +
        `<strong>状态：</strong>${data.success ? '完成' : '失败/结果不确定'}<br>` +
        `<strong>耗时：</strong>${esc(data.duration_ms)} ms<br>` +
        `<strong>Token：</strong>${esc(data.usage?.total_tokens ?? '—')}` +
        `${data.error ? `<br><strong>错误：</strong>${esc(data.error)}` : ''}` +
        `</div><hr><strong>模型回复：</strong><br>${esc(data.answer || '无有效回答')}`;
      if (data.success) {
        $('conversation').value = data.conversation_name;
        $('action').value = 'ask';
        updateConversationUi();
      }
      await loadConversations();
    } else {
      const payload = {
        provider,''', 'templates/index.html')
    text = _replace(text,"    const response = await fetch(`/api/conversations?provider=${encodeURIComponent(provider)}`);",
        "    const response = await fetch(provider === 'deepseek' ? '/api/deepseek/sessions' : `/api/conversations?provider=${encodeURIComponent(provider)}`);",'templates/index.html')
    text = _replace(text,"    for (const item of data.conversations || []) {",
        "    for (const item of (provider === 'deepseek' ? data.sessions : data.conversations) || []) {",'templates/index.html')
    text = _replace(text,"      const label = item.status === 'pending'\n        ? `${item.name} [pending，不可继续]`",
        "      const blocked = provider === 'deepseek' ? item.status !== 'ready' : item.status === 'pending';\n      const label = blocked\n        ? `${item.name} [${item.status}，不可继续]`",'templates/index.html')
    text = _replace(text,"      if (item.status === 'pending') option.disabled = true;",
        "      if (blocked) option.disabled = true;\n      if (provider === 'deepseek') option.dataset.model = item.model;",'templates/index.html')
    text = _replace(text,"      if (item.status !== 'pending') {",
        "      if (!blocked) {",'templates/index.html')
    text = _replace(text,"  $('conversationList').style.display = action === 'ask' ? '' : 'none';",
        "  $('importConversation').style.display = provider === 'deepseek' ? 'none' : '';\n  $('conversationList').style.display = action === 'ask' ? '' : 'none';",'templates/index.html')
    text = _replace(text,"    hint.textContent = '继续会话：优先从下拉列表选择。pending 会话不会允许直接继续。';",
        "    hint.textContent = provider === 'deepseek' ? 'API 多轮：选择 ready 会话；pending/uncertain 不会自动重发。' : '继续会话：优先从下拉列表选择。pending 会话不会允许直接继续。';",'templates/index.html')
    text = _replace(text,"    hint.textContent = '新建会话：同一 Provider 内名称不可重复；Gemini 与 ChatGPT 可使用相同名称。';",
        "    hint.textContent = provider === 'deepseek' ? 'API 新会话：名称可留空，自动生成；仅保存已确认的上下文。' : '新建会话：同一 Provider 内名称不可重复；Gemini 与 ChatGPT 可使用相同名称。';",'templates/index.html')
    text = _replace(text,"  if ($('conversationList').value) $('conversation').value = $('conversationList').value;",
        "  if ($('conversationList').value) {\n    $('conversation').value = $('conversationList').value;\n    if ($('provider').value === 'deepseek') {\n      const item = $('conversationList').selectedOptions[0];\n      if (item?.dataset?.model) $('deepseekModel').value = item.dataset.model;\n    }\n  }",'templates/index.html')
    text = _replace(text,"  if (provider === 'auto' || provider === 'dialogue') return;\n\n  const name = window.prompt",
        "  if (provider === 'auto' || provider === 'dialogue' || provider === 'deepseek') return;\n\n  const name = window.prompt",'templates/index.html')
    # Returning to DeepSeek should never leak an old Web conversation name.
    text = _replace(text,"$('provider').addEventListener('change', async () => {\n  updateConversationUi();",
        "$('provider').addEventListener('change', async () => {\n  $('conversation').value = '';\n  updateConversationUi();",'templates/index.html')
    return text


FILES = {
    ROOT/'deepseek_api.py': patch_api,
    ROOT/'provider_runtime.py': patch_runtime,
    ROOT/'gateway_app.py': patch_gateway,
    ROOT/'templates/index.html': patch_html,
}


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    src = {p:p.read_text(encoding='utf-8') for p in FILES}
    patched = {p:fn(src[p]) for p,fn in FILES.items()}
    if args.check:
        print('V12_PHASE2_CHECK=', 'already_installed' if src == patched else 'ready')
        return
    if src == patched:
        print('V12_PHASE2_PATCH= already_installed')
        return
    for p in (ROOT/'deepseek_session_service.py', ROOT/'database/migrations/002_deepseek_sessions.sql'):
        if not p.exists():
            raise SystemExit('Missing Phase 2 file: '+str(p))
    try:
        import httpx  # noqa: F401
    except ImportError:
        raise SystemExit('Missing httpx: .venv/bin/python -m pip install "httpx>=0.27,<1"')
    for p, data in patched.items():
        if p.suffix == '.py': compile(data, str(p), 'exec')
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())
    dest = Path.home()/'ai-router-archives'/'v1.2-phase2-deepseek'
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(dest, 0o700)
    for p, old in src.items():
        backup = dest/(p.name+'.before-phase2-'+stamp)
        backup.write_text(old, encoding='utf-8')
        os.chmod(backup, 0o600)
    completed = []
    try:
        for p, data in patched.items():
            temp=p.with_name(p.name+'.phase2-tmp')
            temp.write_text(data, encoding='utf-8')
            os.replace(temp,p)
            completed.append(p)
    except BaseException:
        for p in completed:
            p.write_text(src[p], encoding='utf-8')
        raise
    print('V12_PHASE2_PATCH= applied')
    print('BACKUP_DIR=', dest)

if __name__=='__main__':main()
