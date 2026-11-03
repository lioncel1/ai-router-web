#!/usr/bin/env python3
"""Write and query task-level routing history."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
LOG_PATH = BASE_DIR / "logs" / "routing_history.jsonl"
TASK_LEVELS = ("L0", "L1", "L2", "L3")
TASK_TYPES = ("summary", "writing", "analysis", "strategy", "coding", "review", "other")
PROVIDERS = ("gemini", "chatgpt")
FINAL_HANDLERS = ("codex", "gemini", "chatgpt")
SCHEMA_VERSION = 2
FEATURE_NAME_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


def warning(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "y", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError("必须是 true 或 false")


def parse_nullable_bool(value: str) -> bool | None:
    if value.strip().lower() in {"null", "none"}:
        return None
    return parse_bool(value)


def parse_providers(value: str) -> list[str]:
    providers = [item.strip().lower() for item in value.split(",") if item.strip()]
    if not providers or any(item not in PROVIDERS for item in providers):
        raise argparse.ArgumentTypeError("providers 只能包含 gemini 和 chatgpt")
    if len(set(providers)) != len(providers):
        raise argparse.ArgumentTypeError("providers 不能重复")
    return providers


def parse_features(value: str) -> list[str]:
    features: list[str] = []
    seen: set[str] = set()
    for item in value.split(","):
        feature = item.strip().lower()
        if not feature:
            continue
        if not FEATURE_NAME_RE.fullmatch(feature):
            raise argparse.ArgumentTypeError(
                "feature 必须为1至64位，只能包含小写字母、数字、下划线或连字符"
            )
        if feature not in seen:
            seen.add(feature)
            features.append(feature)
    return features


def record_features(record: dict[str, Any]) -> list[str]:
    value = record.get("task_features", [])
    if not isinstance(value, list):
        return []

    features: list[str] = []
    seen: set[str] = set()
    for item in value:
        feature = str(item).strip().lower()
        if FEATURE_NAME_RE.fullmatch(feature) and feature not in seen:
            seen.add(feature)
            features.append(feature)
    return features


def parse_stages(value: str) -> list[dict[str, str]]:
    stages: list[dict[str, str]] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        provider, separator, role = item.partition(":")
        if not separator or provider not in PROVIDERS or not role.strip():
            raise argparse.ArgumentTypeError(
                "stages 格式应为 provider:role,provider:role"
            )
        stages.append({"provider": provider, "role": role.strip()})
    if not stages:
        raise argparse.ArgumentTypeError("至少需要一个 stage")
    return stages


def prompt_hash(prompt: str | None, supplied_hash: str | None) -> str | None:
    if prompt is not None and supplied_hash is not None:
        raise ValueError("--prompt 和 --prompt-hash 不能同时使用")
    if supplied_hash is not None:
        value = supplied_hash.strip().lower()
        if len(value) != 12 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("--prompt-hash 必须是 SHA256 的前12位十六进制字符串")
        return value
    if prompt is not None:
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    return None


def append_record(record: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_records() -> list[dict[str, Any]]:
    if not LOG_PATH.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with LOG_PATH.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    warning(f"routing_history.jsonl 第 {line_number} 行损坏，已跳过：{exc.msg}")
                    continue
                if not isinstance(item, dict):
                    warning(f"routing_history.jsonl 第 {line_number} 行不是 JSON 对象，已跳过")
                    continue
                item.setdefault("schema_version", 1)
                item.setdefault("task_features", [])
                records.append(item)
    except OSError as exc:
        warning(f"无法读取 routing_history.jsonl：{exc}")
    return records


def add_command(args: argparse.Namespace) -> int:
    try:
        hash_value = prompt_hash(args.prompt, args.prompt_hash)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    route_id = str(uuid.uuid4())
    record = {
        "schema_version": SCHEMA_VERSION,
        "timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "route_id": route_id,
        "task_level": args.task_level,
        "task_type": args.task_type,
        "task_features": args.features,
        "providers_used": args.providers,
        "stages": args.stages,
        "final_handler": args.final_handler,
        "second_model_used": args.second_model_used,
        "second_model_changed_conclusion": args.second_model_changed,
        "external_call_success": args.success,
        "final_route": args.final_route,
        "duration_ms": args.duration_ms,
        "prompt_hash": hash_value,
    }
    append_record(record)
    print(route_id)
    return 0


def recent_command() -> int:
    records = load_records()
    print(
        f"{'时间':<19} {'等级':<5} {'类型':<10} {'调用模型':<18} "
        f"{'最终处理者':<12} {'多模型':<6} 任务特征"
    )
    for record in records[-10:][::-1]:
        providers = record.get("providers_used", [])
        if isinstance(providers, list):
            provider_text = ",".join(str(item) for item in providers) or "-"
        else:
            provider_text = str(providers or "-")
        feature_text = ",".join(record_features(record)) or "-"
        if len(feature_text) > 48:
            feature_text = feature_text[:47] + "…"
        timestamp = str(record.get("timestamp", "未知"))
        print(
            f"{timestamp[:19]:<19} "
            f"{str(record.get('task_level', '-')):<5} "
            f"{str(record.get('task_type', '-')):<10} "
            f"{provider_text:<18} "
            f"{str(record.get('final_handler', '-')):<12} "
            f"{'是' if len(providers) > 1 else '否':<6} "
            f"{feature_text}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="记录和查询任务路由历史")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="新增一条路由历史")
    add.add_argument("--task-level", choices=TASK_LEVELS, required=True)
    add.add_argument("--task-type", choices=TASK_TYPES, required=True)
    add.add_argument(
        "--features",
        type=parse_features,
        default=[],
        help="逗号分隔的任务特征；仅允许字母、数字、下划线和连字符",
    )
    add.add_argument("--providers", type=parse_providers, required=True)
    add.add_argument("--stages", type=parse_stages, required=True)
    add.add_argument("--final-handler", choices=FINAL_HANDLERS, required=True)
    add.add_argument("--second-model-used", type=parse_bool, required=True)
    add.add_argument("--second-model-changed", type=parse_nullable_bool, required=True)
    add.add_argument("--success", type=parse_bool, required=True)
    add.add_argument("--final-route", required=True)
    add.add_argument("--duration-ms", type=int, default=None)
    add.add_argument("--prompt", help="原始 prompt，仅用于计算哈希，不会写入日志")
    add.add_argument("--prompt-hash", help="已计算好的 SHA256 前12位")
    add.set_defaults(handler=add_command)

    recent = subparsers.add_parser("recent", help="显示最近10条路由历史")
    recent.set_defaults(handler=lambda _args: recent_command())
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
