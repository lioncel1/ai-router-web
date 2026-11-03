"""Fail-closed, reversible Phase 3 gateway edit. Usage: --check / --apply."""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
GATEWAY = BASE / "gateway_app.py"
IMPORT_OLD = "from orchestrator import run_orchestration\n"
IMPORT_NEW = IMPORT_OLD + "from chat_task_service import execute_chat_with_task\n"
CALL_OLD = '''    result = await asyncio.to_thread(
        _run_provider,
        request.provider,
        request.action,
        conversation_name,
        request.prompt,
    )
'''
CALL_NEW = CALL_OLD.replace("        _run_provider,\n", "        execute_chat_with_task,\n        _run_provider,\n")
RESPONSE_OLD = '''    return {
        "success": result["success"],
        "provider": request.provider,
'''
RESPONSE_NEW = '''    return {
        "task_id": result["task_id"],
        "history_warning": result.get("history_warning", ""),
        "success": result["success"],
        "provider": request.provider,
'''


def patched(original: str) -> str:
    if IMPORT_NEW in original:
        if CALL_NEW in original and RESPONSE_NEW in original:
            return original
        raise RuntimeError("Gateway has partial Phase 3 edits; refusing to overwrite")
    for label, needle in (("import", IMPORT_OLD), ("provider call", CALL_OLD),
                          ("response", RESPONSE_OLD)):
        if original.count(needle) != 1:
            raise RuntimeError(f"Gateway {label} anchor mismatch; no changes made")
    return (original.replace(IMPORT_OLD, IMPORT_NEW, 1)
                    .replace(CALL_OLD, CALL_NEW, 1)
                    .replace(RESPONSE_OLD, RESPONSE_NEW, 1))


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not GATEWAY.is_file():
        raise SystemExit("ERROR: gateway_app.py missing; run from project root")
    for name in ("chat_task_service.py", "task_manager.py", "database/repository.py"):
        if not (BASE / name).is_file():
            raise SystemExit(f"ERROR: required file missing: {name}")
    original = GATEWAY.read_text(encoding="utf-8")
    result = patched(original)
    if args.check:
        print("GATEWAY_PATCH_CHECK=", "already applied" if result == original else "ready")
        return
    if result == original:
        print("GATEWAY_PATCH= already applied")
        return
    archive = BASE.parent / "ai-router-archives" / "chat-task-phase3"
    archive.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = archive / f"gateway_app.py.pre-phase3-{stamp}"
    shutil.copy2(GATEWAY, backup)
    # Validate new file before changing the active source.
    compile(result, str(GATEWAY), "exec")
    GATEWAY.write_text(result, encoding="utf-8")
    print("GATEWAY_PATCH= applied")
    print("BACKUP=", backup)


if __name__ == "__main__":
    main()
