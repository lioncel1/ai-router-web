#!/usr/bin/env python3
"""Summarize routing history and provider call latency."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
CALL_LOG_PATH = BASE_DIR / "logs" / "router_calls.jsonl"
ROUTE_LOG_PATH = BASE_DIR / "logs" / "routing_history.jsonl"
LEVELS = ("L0", "L1", "L2", "L3")
PROVIDER_NAMES = {
    "gemini": "Gemini",
    "chatgpt": "ChatGPT",
}
FEATURE_NAME_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


def warning(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    warning(f"{path.name} 第 {line_number} 行损坏，已跳过：{exc.msg}")
                    continue
                if isinstance(item, dict):
                    records.append(item)
                else:
                    warning(f"{path.name} 第 {line_number} 行不是 JSON 对象，已跳过")
    except OSError as exc:
        warning(f"无法读取 {path.name}：{exc}")
    return records


def load_route_records() -> list[dict[str, Any]]:
    records = load_jsonl(ROUTE_LOG_PATH)
    for record in records:
        record.setdefault("schema_version", 1)
        record.setdefault("task_features", [])
    return records


def recent(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now().astimezone()
    cutoff = now - timedelta(hours=24)
    return [
        record
        for record in records
        if (timestamp := parse_timestamp(record.get("timestamp"))) is not None
        and cutoff <= timestamp <= now
    ]


def providers(record: dict[str, Any]) -> list[str]:
    value = record.get("providers_used", [])
    if not isinstance(value, list):
        return []
    return [str(item).lower() for item in value if str(item).lower() in {"gemini", "chatgpt"}]


def task_features(record: dict[str, Any]) -> list[str]:
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


def summary(_args: argparse.Namespace) -> int:
    route_records = recent(load_route_records())
    call_records = recent(load_jsonl(CALL_LOG_PATH))
    level_counts = {level: sum(record.get("task_level") == level for record in route_records) for level in LEVELS}
    single_model = sum(len(providers(record)) == 1 for record in route_records)
    dual_model = sum(len(providers(record)) >= 2 for record in route_records)
    gemini_count = sum("gemini" in providers(record) for record in route_records)
    chatgpt_count = sum("chatgpt" in providers(record) for record in route_records)
    failed_external = sum(record.get("external_call_success") is False for record in route_records)
    feature_counts = Counter(
        feature
        for record in route_records
        for feature in task_features(record)
    )
    provider_feature_counts = {
        provider: Counter()
        for provider in PROVIDER_NAMES
    }
    for record in route_records:
        features = task_features(record)
        for provider in providers(record):
            provider_feature_counts[provider].update(features)

    durations = [
        float(record["duration_ms"])
        for record in call_records
        if isinstance(record.get("duration_ms"), (int, float)) and record["duration_ms"] >= 0
    ]
    average_duration = sum(durations) / len(durations) if durations else None

    print("最近24小时路由统计")
    print(f"1. 任务数量：{len(route_records)}")
    print(
        "2. L0/L1/L2/L3分布："
        + ", ".join(f"{level}={level_counts[level]}" for level in LEVELS)
    )
    print(f"3. 单模型任务数量：{single_model}")
    print(f"4. 双模型任务数量：{dual_model}")
    print(f"5. Gemini参与次数：{gemini_count}")
    print(f"6. ChatGPT参与次数：{chatgpt_count}")
    print(f"7. 外部调用失败数量：{failed_external}")
    print(
        "8. 平均Provider耗时："
        + (f"{average_duration:.1f} ms（样本数：{len(durations)}）" if average_duration is not None else "暂无数据（样本数：0）")
    )
    if len(route_records) < 10:
        print(f"提示：路由历史样本数量为 {len(route_records)}，样本较少，不据此评价模型优劣。")

    print()
    print("任务特征分布（最近24小时）")
    if feature_counts:
        for feature, count in sorted(
            feature_counts.items(),
            key=lambda item: (-item[1], item[0]),
        ):
            print(f"{feature}: {count}")
    else:
        print("暂无任务特征数据")

    print()
    print("各Provider参与的任务特征（最近24小时）")
    for provider, label in PROVIDER_NAMES.items():
        print(f"{label}:")
        counts = provider_feature_counts[provider]
        if not counts:
            print("  暂无任务特征数据")
            continue
        for feature, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        ):
            print(f"  {feature}: {count}")
    print("说明：以上仅统计Provider参与次数，不代表模型能力或质量评价。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="统计 AI Router 路由历史")
    subparsers = parser.add_subparsers(dest="command", required=True)
    summary_parser = subparsers.add_parser("summary", help="显示最近24小时统计")
    summary_parser.set_defaults(handler=summary)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
