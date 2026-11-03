#!/bin/bash

cd /home/ubuntu/ai-router || exit 1
source .venv/bin/activate

ACTION="${1:-}"

if [[ "$ACTION" != "new" && "$ACTION" != "ask" && "$ACTION" != "reset" ]]; then
    exec python chatgpt_chat.py "$@"
fi

CONVERSATION_NAME="${2:-}"
PROMPT="${*:3}"
LOCK_FILE="/tmp/ai-router-chatgpt.lock"
LOCK_WAIT_SECONDS=300
START_NS=$(date +%s%N)

finish_lock_failure() {
    local message="$1"
    local exit_code="$2"
    local end_ns
    local duration_ms

    printf '%s\n' "$message"
    end_ns=$(date +%s%N)
    duration_ms=$(( (end_ns - START_NS) / 1000000 ))

    printf '%s\n' "$message" | python router_log.py \
        --provider chatgpt \
        --action "$ACTION" \
        --conversation-name "$CONVERSATION_NAME" \
        --exit-code "$exit_code" \
        --duration-ms "$duration_ms" \
        --output-file /dev/stdin \
        --prompt "$PROMPT" \
        >/dev/null 2>&1 || true

    exit "$exit_code"
}

if ! command -v flock >/dev/null 2>&1; then
    finish_lock_failure "[Router] ChatGPT provider lock unavailable: flock not found" 127
fi

if ! exec {LOCK_FD}>"$LOCK_FILE"; then
    finish_lock_failure "[Router] ChatGPT provider lock unavailable" 1
fi

if ! flock -n "$LOCK_FD"; then
    echo "[Router] ChatGPT当前正在处理另一个任务，等待可用..."
    if ! flock -w "$LOCK_WAIT_SECONDS" "$LOCK_FD"; then
        finish_lock_failure "[Router] ChatGPT provider lock timeout" 124
    fi
fi

OUTPUT_FILE=$(mktemp /tmp/ai-router-chatgpt.XXXXXX) || {
    python chatgpt_chat.py "$@"
    STATUS=$?

    if [ "$STATUS" -ne 0 ]; then
        echo
        echo "ChatGPT调用异常，尝试保存网页截图..."
        python capture_chatgpt_debug.py 2>/dev/null || true
    fi

    exit "$STATUS"
}
trap 'rm -f -- "$OUTPUT_FILE"' EXIT

python -u chatgpt_chat.py "$@" 2>&1 | tee "$OUTPUT_FILE"
STATUS=${PIPESTATUS[0]}
END_NS=$(date +%s%N)
DURATION_MS=$(( (END_NS - START_NS) / 1000000 ))

if [ "$STATUS" -ne 0 ]; then
    echo
    echo "ChatGPT调用异常，尝试保存网页截图..."
    python capture_chatgpt_debug.py 2>/dev/null || true
fi

python router_log.py \
    --provider chatgpt \
    --action "$ACTION" \
    --conversation-name "$CONVERSATION_NAME" \
    --exit-code "$STATUS" \
    --duration-ms "$DURATION_MS" \
    --output-file "$OUTPUT_FILE" \
    --prompt "$PROMPT" \
    >/dev/null 2>&1 || true

exit "$STATUS"
