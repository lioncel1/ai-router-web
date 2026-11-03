#!/usr/bin/env python3
"""Render call-log and conversation health sections for status.sh."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


BASE_DIR = Path(__file__).resolve().parent
LOG_PATH = BASE_DIR / "logs" / "router_calls.jsonl"
CONVERSATION_PATHS = {
    "gemini": BASE_DIR / "data" / "gemini_conversations.json",
    "chatgpt": BASE_DIR / "data" / "chatgpt_conversations.json",
}
PROVIDER_LABELS = {
    "gemini": "Gemini",
    "chatgpt": "ChatGPT",
}
LOCAL_TZ = datetime.now().astimezone().tzinfo
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def clean_text(value: Any, limit: int | None = None) -> str:
    """Convert a value to a single terminal-safe line."""
    text = ANSI_ESCAPE_RE.sub("", str(value or ""))
    text = " ".join(text.split())
    if limit is not None and len(text) > limit:
        return text[: max(0, limit - 1)] + "…"
    return text


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
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed


def format_timestamp(value: Any) -> str:
    parsed = parse_timestamp(value)
    if parsed is None:
        return "未知"
    local = parsed.astimezone(LOCAL_TZ)
    now = datetime.now().astimezone(LOCAL_TZ)
    if local.date() == now.date():
        return local.strftime("%H:%M")
    return local.strftime("%m-%d %H:%M")


def format_duration(value: Any) -> str:
    try:
        milliseconds = float(value)
    except (TypeError, ValueError):
        return "-"
    if milliseconds < 0:
        return "-"
    return f"{milliseconds / 1000:.1f}s"


def normalized_result(record: dict[str, Any]) -> str:
    result_type = clean_text(record.get("result_type")).lower()
    if result_type in {"success", "cached", "failed"}:
        return result_type
    return "success" if record.get("success") is True else "failed"


def load_call_logs() -> tuple[list[dict[str, Any]], int, str | None]:
    if not LOG_PATH.exists():
        return [], 0, None

    records: list[dict[str, Any]] = []
    invalid_lines = 0
    try:
        with LOG_PATH.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    invalid_lines += 1
                    continue
                if isinstance(item, dict):
                    records.append(item)
                else:
                    invalid_lines += 1
    except OSError as exc:
        return [], invalid_lines, clean_text(exc, 120)
    return records, invalid_lines, None


def print_recent_calls(
    records: list[dict[str, Any]],
    invalid_lines: int,
    read_error: str | None,
) -> None:
    print("========== 最近调用 ==========")
    if read_error:
        print(f"调用日志无法读取：{read_error}")
    elif not LOG_PATH.exists() or not records:
        print("暂无调用日志")
    else:
        print(f"{'时间':<12} {'模型':<9} {'动作':<7} {'会话':<25} {'结果':<9} {'耗时':>8}")
        for record in records[-5:]:
            provider = PROVIDER_LABELS.get(
                clean_text(record.get("provider")).lower(),
                clean_text(record.get("provider"), 8) or "未知",
            )
            print(
                f"{format_timestamp(record.get('timestamp')):<12} "
                f"{provider:<9} "
                f"{clean_text(record.get('action'), 6) or '-':<7} "
                f"{clean_text(record.get('conversation_name'), 24) or '-':<25} "
                f"{normalized_result(record):<9} "
                f"{format_duration(record.get('duration_ms')):>8}"
            )
    if invalid_lines:
        print(f"提示：调用日志有 {invalid_lines} 行无法解析，已跳过。")


def print_24_hour_stats(records: list[dict[str, Any]]) -> None:
    print()
    print("========== 最近24小时统计 ==========")
    now = datetime.now().astimezone()
    cutoff = now - timedelta(hours=24)

    for provider in ("gemini", "chatgpt"):
        recent: list[dict[str, Any]] = []
        for record in records:
            if clean_text(record.get("provider")).lower() != provider:
                continue
            timestamp = parse_timestamp(record.get("timestamp"))
            if timestamp is not None and cutoff <= timestamp.astimezone() <= now:
                recent.append(record)

        results = [normalized_result(record) for record in recent]
        durations: list[float] = []
        for record in recent:
            try:
                duration = float(record.get("duration_ms"))
            except (TypeError, ValueError):
                continue
            if duration >= 0:
                durations.append(duration)

        average = (
            f"{sum(durations) / len(durations) / 1000:.1f}s"
            if durations
            else "-"
        )
        longest = f"{max(durations) / 1000:.1f}s" if durations else "-"
        print(
            f"{PROVIDER_LABELS[provider]}："
            f"调用 {len(recent)}  "
            f"成功 {results.count('success')}  "
            f"缓存 {results.count('cached')}  "
            f"失败 {results.count('failed')}  "
            f"平均 {average}  "
            f"最长 {longest}"
        )


def load_conversations(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {}, "文件不存在"
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return {}, clean_text(exc, 120)
    if not isinstance(data, dict):
        return {}, "顶层 JSON 不是对象"
    return data, None


def valid_conversation_url(provider: str, value: Any) -> bool:
    url = clean_text(value)
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    if provider == "gemini":
        return (
            parsed.scheme == "https"
            and parsed.netloc == "gemini.google.com"
            and re.fullmatch(r"/app/[^/]+", parsed.path) is not None
        )
    if provider == "chatgpt":
        return (
            parsed.scheme == "https"
            and parsed.netloc == "chatgpt.com"
            and re.fullmatch(r"/c/[^/]+", parsed.path) is not None
            and not parsed.path.startswith("/c/WEB:")
        )
    return False


def is_root_conversation_url(provider: str, value: Any) -> bool:
    url = clean_text(value)
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if provider == "gemini":
        return (
            parsed.scheme == "https"
            and parsed.netloc == "gemini.google.com"
            and parsed.path.rstrip("/") == "/app"
        )
    if provider == "chatgpt":
        return (
            parsed.scheme == "https"
            and parsed.netloc == "chatgpt.com"
            and parsed.path.rstrip("/") == "/c"
        )
    return False


def conversation_anomaly_reasons(
    provider: str,
    value: dict[str, Any],
) -> list[str]:
    status = clean_text(value.get("status")).lower()
    answer = clean_text(value.get("last_answer"))
    url = (
        value.get("url")
        or value.get("conversation_url")
        or value.get("web_url")
        or ""
    )
    url_valid = valid_conversation_url(provider, url)
    url_is_root = is_root_conversation_url(provider, url)

    if status == "done":
        reasons = []
        if not answer:
            reasons.append("done但答案为空")
        if not url_valid:
            reasons.append("done但URL无效")
        return reasons

    reasons = ["状态为空" if not status else "未完成"]
    if url_is_root:
        reasons.insert(0, "根URL")
    elif not url:
        reasons.insert(0, "URL为空")
    elif not url_valid:
        reasons.insert(0, "URL无效")
    return reasons


def print_anomalous_conversations() -> None:
    print()
    print("========== pending / 异常会话 ==========")
    for provider, path in CONVERSATION_PATHS.items():
        label = PROVIDER_LABELS[provider]
        conversations, read_error = load_conversations(path)
        if read_error:
            print(f"{label}：无法读取会话数据（{read_error}）")
            continue

        anomalies: list[tuple[str, str, str, str, bool]] = []
        for name, value in conversations.items():
            if not isinstance(value, dict):
                continue
            status_value = value.get("status")
            if status_value is None:
                # Historical Gemini entries without status are legacy records.
                continue
            reasons = conversation_anomaly_reasons(provider, value)
            if not reasons:
                continue
            updated = (
                value.get("updated_at")
                or value.get("updated")
                or value.get("timestamp")
                or ""
            )
            url = (
                value.get("url")
                or value.get("conversation_url")
                or value.get("web_url")
                or ""
            )
            anomalies.append(
                (
                    clean_text(name, 30) or "-",
                    clean_text(status_value, 16) or "未知",
                    " / ".join(reasons),
                    clean_text(updated, 24) or "未知",
                    valid_conversation_url(provider, url),
                )
            )

        if not anomalies:
            print(f"{label}：无异常会话")
            continue

        anomalies.sort(key=lambda item: item[3], reverse=True)
        print(f"{label}：")
        print(
            f"  {'会话':<31} {'模型':<9} {'状态':<17} "
            f"{'原因':<31} {'更新时间':<25} URL"
        )
        for name, status, reason, updated, url_valid in anomalies:
            print(
                f"  {name:<31} {label:<9} {status:<17} "
                f"{reason:<31} {updated:<25} "
                f"{'有效' if url_valid else '无效'}"
            )


def print_recent_failures(records: list[dict[str, Any]]) -> None:
    print()
    print("========== 最近失败 ==========")
    failures = [record for record in records if record.get("success") is False]
    if not failures:
        print("最近失败：无")
        return

    print(f"{'时间':<12} {'模型':<9} {'会话':<25} 错误摘要")
    for record in reversed(failures[-3:]):
        provider = PROVIDER_LABELS.get(
            clean_text(record.get("provider")).lower(),
            clean_text(record.get("provider"), 8) or "未知",
        )
        summary = clean_text(record.get("error_summary"), 100)
        print(
            f"{format_timestamp(record.get('timestamp')):<12} "
            f"{provider:<9} "
            f"{clean_text(record.get('conversation_name'), 24) or '-':<25} "
            f"{summary or '未提供错误摘要'}"
        )


def main() -> int:
    records, invalid_lines, read_error = load_call_logs()
    print_recent_calls(records, invalid_lines, read_error)
    print_24_hour_stats(records)
    print_anomalous_conversations()
    print_recent_failures(records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
