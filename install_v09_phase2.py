#!/usr/bin/env python3
"""v0.9 Dialogue Task Gateway installer: strict anchors + backup + safe apply."""
from __future__ import annotations
import argparse
import os
import py_compile
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
TARGET = BASE / "gateway_app.py"
MARKER = "# AI_ROUTER_DIALOGUE_TASK_V09_PHASE2"
WORKER_NEW = (BASE / "phase2_assets" / "dialogue_worker.pyfrag").read_text(encoding="utf-8")
STOP_NEW = (BASE / "phase2_assets" / "dialogue_stop.pyfrag").read_text(encoding="utf-8")


def patched(source: str) -> str:
    if "# AI_ROUTER_DIALOGUE_RESUME_V11" in source:
        required = (
            MARKER, "import dialogue_task_service as _dts",
            "_dts.create_dialogue_task", "_dts.create_dialogue_continuation",
            "_dts.read_dialogue_job", "_dts.request_dialogue_stop",
            "_dts.reconcile_interrupted_dialogue_tasks",
        )
        if not all(item in source for item in required):
            raise RuntimeError("Incomplete Dialogue continuation detected")
        return source
    if MARKER in source:
        if not all(s in source for s in (
            "import dialogue_task_service as _dts", WORKER_NEW,
            "_dts.create_dialogue_task", "_dts.read_dialogue_job",
            "_dts.request_dialogue_stop", "_dts.reconcile_interrupted_dialogue_tasks",
        )):
            raise RuntimeError("Partial Dialogue patch detected; refusing to overwrite")
        return source

    if "# AI_ROUTER_AUTO_TASK_V09_PHASE1" not in source:
        raise RuntimeError("v0.9 Auto Phase 1 patch not found; refusing to modify")
    changes = [
        (
            "# dialogue mode v0.7\n",
            "# dialogue mode v0.7\n"
            "import dialogue_task_service as _dts\n"
            "# AI_ROUTER_DIALOGUE_TASK_V09_PHASE2\n"
            "\n@app.on_event(\"startup\")\n"
            "def _dialogue_task_startup_recovery():\n"
            "    import logging\n"
            "    try:\n"
            "        count = _dts.reconcile_interrupted_dialogue_tasks()\n"
            "        if count:\n"
            "            logging.getLogger(__name__).warning(\"Marked %d interrupted Dialogue tasks\", count)\n"
            "    except Exception:\n"
            "        logging.getLogger(__name__).exception(\"Dialogue startup recovery failed\")\n\n",
        ),
        (
            '    job_id = str(uuid.uuid4())\n    with DIALOGUE_JOBS_LOCK:\n',
            '    job_id = str(uuid.uuid4())\n'
            '    try:\n'
            '        await asyncio.to_thread(_dts.create_dialogue_task, job_id, req.first_provider,\n'
            '                                req.second_provider, req.prompt, req.max_messages)\n'
            '    except Exception:\n'
            '        raise HTTPException(status_code=503, detail="Dialogue task storage unavailable; request not sent")\n'
            '    with DIALOGUE_JOBS_LOCK:\n',
        ),
        (
            '        if job is None:\n'
            '            raise HTTPException(status_code=404, detail="Dialogue job not found")\n'
            '        data = dict(job)\n'
            '        data["messages"] = [dict(item) for item in job.get("messages", [])]\n'
            '    return {"success": data.get("status") != "failed", "job_id": job_id, **data}\n',
            '        if job is None:\n'
            '            data = None\n'
            '        else:\n'
            '            data = dict(job)\n'
            '            data["messages"] = [dict(item) for item in job.get("messages", [])]\n'
            '    if data is None:\n'
            '        data = await asyncio.to_thread(_dts.read_dialogue_job, job_id)\n'
            '        if data is None:\n'
            '            raise HTTPException(status_code=404, detail="Dialogue job not found")\n'
            '        return data\n'
            '    return {"success": data.get("status") not in {"failed", "stopped"}, "job_id": job_id, **data}\n',
        ),
    ]
    for old, new in changes:
        count = source.count(old)
        if count != 1:
            raise RuntimeError(f"Gateway anchor mismatch ({count}): {old[:75]!r}")
        source = source.replace(old, new, 1)
    a = source.find("def _run_dialogue_job(\n")
    b = source.find('@app.post("/api/dialogue/start")', a)
    if a == -1 or b == -1 or source.find("def _run_dialogue_job(\n", a + 1) != -1:
        raise RuntimeError("Dialogue worker anchor missing or duplicated")
    source = source[:a] + WORKER_NEW + "\n" + source[b:]
    a = source.find('@app.post("/api/dialogue/stop/{job_id}")')
    b = source.find('\n# AI_ROUTER_TASK_HISTORY_PHASE4', a)
    if a == -1 or b == -1:
        raise RuntimeError("Dialogue stop endpoint anchor mismatch")
    source = source[:a] + STOP_NEW + source[b:]
    return source


def main() -> None:
    parser = argparse.ArgumentParser()
    flags = parser.add_mutually_exclusive_group(required=True)
    flags.add_argument("--check", action="store_true")
    flags.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not (BASE / "dialogue_task_service.py").is_file():
        raise SystemExit("dialogue_task_service.py missing; extract full ZIP")
    current = TARGET.read_text(encoding="utf-8")
    updated = patched(current)
    if args.check:
        print("V09_DIALOGUE_PATCH_CHECK= already_installed" if updated == current else "V09_DIALOGUE_PATCH_CHECK= ready")
        return
    if updated == current:
        print("V09_DIALOGUE_PATCH= already_installed")
        return
    archive = Path.home() / "ai-router-archives" / "dialogue-task-v09-phase2"
    archive.mkdir(parents=True, exist_ok=True)
    backup = archive / ("gateway_app.py.before-dialogue-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    shutil.copy2(TARGET, backup)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=TARGET.parent,
                                         prefix=".gateway-dialogue-", delete=False) as out:
            out.write(updated)
            temp_path = Path(out.name)
        os.chmod(temp_path, TARGET.stat().st_mode & 0o777)
        py_compile.compile(str(temp_path), doraise=True)
        os.replace(temp_path, TARGET)
        temp_path = None
    except Exception:
        if temp_path: temp_path.unlink(missing_ok=True)
        shutil.copy2(backup, TARGET)
        raise
    print("V09_DIALOGUE_PATCH= applied")
    print("BACKUP=", backup)


if __name__ == "__main__":
    main()
