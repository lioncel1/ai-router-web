#!/usr/bin/env python3

import argparse
import fcntl
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from quality import check_quality


BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "logs" / "router_calls.jsonl"
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
CACHE_RE = re.compile(r"检测到相同任务已(?:经)?执行过，不重复调用")


def extract_conversation_url(output, provider):
    if provider == "gemini":
        pattern = r"https://gemini\.google\.com/app(?:/[A-Za-z0-9_-]+)?"
    else:
        pattern = r"https://chatgpt\.com/(?:[^/\s]+/)*c/[^\s]+"

    matches = re.findall(pattern, output)
    if not matches:
        return ""

    return matches[-1].rstrip(".,;:!?，。；：！？)]}>'\"")


def is_valid_conversation_url(url, provider):
    if provider == "gemini":
        return (
            url.startswith("https://gemini.google.com/app/")
            and url.rstrip("/") != "https://gemini.google.com/app"
        )

    return (
        url.startswith("https://chatgpt.com/")
        and "/c/WEB:" not in url
        and "/c/" in url and len(url.rstrip("/").split("/c/", 1)[-1]) > 0
    )


def extract_answer(output, provider):
    header = "Gemini回复：" if provider == "gemini" else "ChatGPT回复："
    if header not in output:
        return ""

    answer = output.rsplit(header, 1)[1]
    if "会话地址：" in answer:
        answer = answer.rsplit("会话地址：", 1)[0]

    lines = [
        line
        for line in answer.splitlines()
        if not line.strip() or set(line.strip()) != {"="}
    ]
    return "\n".join(lines).strip()


def summarize_error(output, exit_code, valid_result):
    if exit_code == 0 and not valid_result:
        return "process exited without a valid model response"

    # Never persist arbitrary stderr: tracebacks may contain the user's prompt.
    if "timeout" in output.lower() or exit_code == 124:
        return "provider_timeout"
    return f"provider_exit_{exit_code}"


def build_record(args, output):
    clean_output = ANSI_ESCAPE_RE.sub("", output)
    conversation_url = extract_conversation_url(
        clean_output,
        args.provider,
    )
    answer = extract_answer(clean_output, args.provider)
    cached = bool(CACHE_RE.search(clean_output))
    valid_result = bool(answer) and is_valid_conversation_url(
        conversation_url,
        args.provider,
    )

    try:
        db = json.loads((BASE_DIR / "data" / f"{args.provider}_conversations.json").read_text())
        record = db.get(args.conversation_name, {})
    except (OSError, ValueError):
        record = {}
    valid_result = (valid_result and record.get("status") == "done"
                    and record.get("last_prompt") == args.prompt
                    and record.get("last_answer", "").strip() == answer.strip()
                    and record.get("url") == conversation_url)
    quality_ok, quality_reason = check_quality(
        answer, task=args.prompt, transport_ok=args.exit_code == 0,
        wrapper_ok=valid_result, evidence=record.get("response_evidence"),
    )
    valid_result = valid_result and quality_ok

    if args.exit_code != 0 or not valid_result:
        result_type = "failed"
        success = False
    elif cached:
        result_type = "cached"
        success = True
    else:
        result_type = "success"
        success = True

    return {
        "timestamp": datetime.now().astimezone().isoformat(
            timespec="milliseconds"
        ),
        "provider": args.provider,
        "action": args.action,
        "conversation_name": args.conversation_name,
        "success": success,
        "exit_code": args.exit_code,
        "duration_ms": max(args.duration_ms, 0),
        "result_type": result_type,
        "quality_ok": quality_ok,
        "quality_reason": quality_reason,
        "output_length": len(clean_output),
        "conversation_url": conversation_url,
        "error_summary": (
            ""
            if success
            else summarize_error(
                clean_output,
                args.exit_code,
                valid_result,
            )
        ),
        "prompt_length": len(args.prompt),
        "prompt_hash": hashlib.sha256(
            args.prompt.encode("utf-8")
        ).hexdigest()[:12],
    }


def append_record(record):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))

    with LOG_FILE.open("a", encoding="utf-8") as log_file:
        fcntl.flock(log_file.fileno(), fcntl.LOCK_EX)
        try:
            log_file.write(line + "\n")
            log_file.flush()
        finally:
            fcntl.flock(log_file.fileno(), fcntl.LOCK_UN)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--provider",
        choices=("gemini", "chatgpt"),
        required=True,
    )
    parser.add_argument(
        "--action",
        choices=("new", "ask", "reset"),
        required=True,
    )
    parser.add_argument("--conversation-name", required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--duration-ms", type=int, required=True)
    parser.add_argument("--output-file", type=Path, required=True)
    parser.add_argument("--prompt", default="")
    return parser.parse_args()


def main():
    args = parse_args()
    output = args.output_file.read_text(
        encoding="utf-8",
        errors="replace",
    )
    append_record(build_record(args, output))


if __name__ == "__main__":
    main()
