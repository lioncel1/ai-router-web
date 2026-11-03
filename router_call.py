#!/usr/bin/env python3
"""Unified entry point for the AI Router provider wrappers."""

from __future__ import annotations

import os
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

# Add future providers here; routing behavior remains in each provider wrapper.
PROVIDERS = {
    "gemini": BASE_DIR / "gemini.sh",
    "chatgpt": BASE_DIR / "chatgpt.sh",
}

ALLOWED_ACTIONS = ("new", "ask", "reset", "list")


def print_available_providers(file=sys.stdout) -> None:
    print("可用Provider：", file=file)
    for provider in PROVIDERS:
        print(f"- {provider}", file=file)


def print_allowed_actions(file=sys.stderr) -> None:
    print("可用action：", file=file)
    for action in ALLOWED_ACTIONS:
        print(f"- {action}", file=file)


def print_usage() -> None:
    print(
        "用法：router_call.py <provider> <action> [参数...]",
        file=sys.stderr,
    )
    print("       router_call.py providers", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if not args:
        print_usage()
        print_available_providers(file=sys.stderr)
        return 2

    if args[0] == "providers":
        if len(args) != 1:
            print("providers 命令不接受额外参数", file=sys.stderr)
            return 2
        for provider in PROVIDERS:
            print(provider)
        return 0

    provider = args[0]
    wrapper = PROVIDERS.get(provider)
    if wrapper is None:
        print(f"未知Provider：{provider}", file=sys.stderr)
        print_available_providers(file=sys.stderr)
        return 2

    if len(args) < 2:
        print(f"缺少action：{provider}", file=sys.stderr)
        print_allowed_actions()
        return 2

    action = args[1]
    if action not in ALLOWED_ACTIONS:
        print(f"未知action：{action}", file=sys.stderr)
        print_allowed_actions()
        return 2

    if not wrapper.is_file():
        print(f"Provider wrapper不存在：{wrapper}", file=sys.stderr)
        return 1
    if not os.access(wrapper, os.X_OK):
        print(f"Provider wrapper不可执行：{wrapper}", file=sys.stderr)
        return 1

    wrapper_args = [str(wrapper), action, *args[2:]]
    os.execv(str(wrapper), wrapper_args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
