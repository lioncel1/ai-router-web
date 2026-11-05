"""Apply DeepSeek Phase 1 to the existing v1.1 Gateway without overwriting Web code.

Usage: python install_v12_deepseek.py --check | --apply
"""
from __future__ import annotations

import argparse
import datetime
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "provider_runtime.py"
GATEWAY = ROOT / "gateway_app.py"
MARKER = "# AI_ROUTER_V12_DEEPSEEK_SINGLE_TURN"


def patch_runtime(source: str) -> str:
    if MARKER in source:
        if "import deepseek_api" not in source or "api_call=api_call" not in source:
            raise ValueError("partial DeepSeek runtime patch detected")
        return source
    head = "from provider_transport import dispatch_provider\n"
    if source.count(head) != 1:
        raise ValueError("provider_runtime.py import anchor changed")
    source = source.replace(head, head + "from provider_transport import TransportUnavailable\nimport deepseek_api\n", 1)
    old = "PROVIDERS = {'gemini', 'chatgpt'}"
    if source.count(old) != 1:
        raise ValueError("provider_runtime.py provider anchor changed")
    source = source.replace(old, "PROVIDERS = {'gemini', 'chatgpt', 'deepseek'}", 1)
    old = """def run_provider(provider, action, conversation_name, prompt, *,
                 role='answer', task=None, timeout=300, transport='web'):
    \"\"\"Preserve Web behavior; API fails closed until installed.\"\"\"
    if provider not in PROVIDERS or action not in {'new', 'ask'}:
        raise ValueError('unsupported provider or action')
    return dispatch_provider(
        provider, action, conversation_name, prompt,
        transport=transport, web_call=_run_web_provider,
        role=role, task=task, timeout=timeout,
    )
"""
    new = """# AI_ROUTER_V12_DEEPSEEK_SINGLE_TURN
def run_provider(provider, action, conversation_name, prompt, *,
                 role='answer', task=None, timeout=300, transport='web', model=None):
    \"\"\"Keep Web routing intact; enable one explicit DeepSeek API call.\"\"\"
    if provider not in PROVIDERS or action not in {'new', 'ask'}:
        raise ValueError('unsupported provider or action')
    api_call = None
    if provider == 'deepseek':
        if transport != 'api' or action != 'new':
            raise TransportUnavailable('DeepSeek supports single-turn API only; request not sent')
        selected_model = model or 'deepseek-flash'
        if selected_model not in deepseek_api.ALLOWED_MODELS:
            raise ValueError('unsupported DeepSeek model')
        def api_call(_provider, _action, _name, question, *, role, task, timeout):
            return deepseek_api.complete(question, model=selected_model, timeout=timeout)
    return dispatch_provider(
        provider, action, conversation_name, prompt,
        transport=transport, web_call=_run_web_provider, api_call=api_call,
        role=role, task=task, timeout=timeout,
    )
"""
    if source.count(old) != 1:
        raise ValueError("provider_runtime.py function anchor changed")
    return source.replace(old, new, 1)


def patch_gateway(source: str) -> str:
    if MARKER in source:
        if "/api/deepseek/chat" not in source or "execute_deepseek_chat" not in source:
            raise ValueError("partial DeepSeek Gateway patch detected")
        return source
    anchor = "from provider_runtime import run_provider\n"
    if source.count(anchor) != 1:
        raise ValueError("gateway_app.py import anchor changed")
    source = source.replace(anchor, anchor + "from deepseek_api import is_configured\nfrom deepseek_task_service import execute as execute_deepseek_chat\nfrom deepseek_task_service import reconcile_interrupted_deepseek_tasks\n", 1)
    anchor2 = "@app.post(\"/api/chat\")\n"
    block = '''# AI_ROUTER_V12_DEEPSEEK_SINGLE_TURN
class DeepSeekChatRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)
    model: Literal["deepseek-flash", "deepseek-v4-pro"] = "deepseek-flash"

    validate_prompt = field_validator("prompt")(ChatRequest.validate_prompt.__func__)


@app.get("/api/deepseek/status")
async def deepseek_status():
    return {"provider": "deepseek", "transport": "api", "configured": is_configured(),
            "models": ["deepseek-flash", "deepseek-v4-pro"],
            "capability": "single_turn_only"}


@app.on_event("startup")
async def reconcile_deepseek_on_startup():
    # Existing Web startup behavior is unchanged. Never issue a provider call.
    await asyncio.to_thread(reconcile_interrupted_deepseek_tasks)


@app.post("/api/deepseek/chat")
async def deepseek_chat(request: DeepSeekChatRequest):
    if not is_configured():
        raise HTTPException(status_code=503, detail="DeepSeek API key not configured; request not sent")
    try:
        return await asyncio.to_thread(execute_deepseek_chat, request.prompt, request.model)
    except Exception:
        # Never expose server environment/configuration or upstream HTTP headers.
        raise HTTPException(status_code=503, detail="DeepSeek request status uncertain; check Task Center before any retry")


'''
    if source.count(anchor2) != 1:
        raise ValueError("gateway_app.py route anchor changed")
    return source.replace(anchor2, block + anchor2, 1)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    original = {RUNTIME: RUNTIME.read_text(encoding="utf-8"),
                GATEWAY: GATEWAY.read_text(encoding="utf-8")}
    modified = {RUNTIME: patch_runtime(original[RUNTIME]),
                GATEWAY: patch_gateway(original[GATEWAY])}
    if args.check:
        print("V12_DEEPSEEK_CHECK=", "already_installed" if modified == original else "ready")
        return
    if modified == original:
        print("V12_DEEPSEEK_PATCH= already_installed")
        return
    # Preflight syntax and dependencies before touching any current source.
    if not (ROOT / "deepseek_api.py").exists() or not (ROOT / "deepseek_task_service.py").exists():
        raise SystemExit("Missing DeepSeek modules")
    for p, s in modified.items():
        compile(s, str(p), "exec")
    import time
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
    backup_dir = Path.home() / "ai-router-archives" / "v1.2-deepseek-phase1"
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_dir, 0o700)
    backups = []
    for p, old in original.items():
        backup = backup_dir / (p.name + ".before-v12-deepseek-" + stamp)
        backup.write_text(old, encoding="utf-8")
        os.chmod(backup, 0o600)
        backups.append(str(backup))
    try:
        for p, new in modified.items():
            tmp = p.with_name(p.name + ".v12-temp")
            tmp.write_text(new, encoding="utf-8")
            tmp.replace(p)
    except BaseException:
        for p, old in original.items():
            p.write_text(old, encoding="utf-8")
        raise
    print("V12_DEEPSEEK_PATCH= applied")
    print("BACKUPS=", *backups, sep="\n")


if __name__ == "__main__":
    main()
