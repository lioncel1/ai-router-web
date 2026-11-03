#!/bin/bash

cd /home/ubuntu/ai-router || exit 1
source .venv/bin/activate

ACTION="${1:-}"

if [[ "$ACTION" != "new" && "$ACTION" != "ask" && "$ACTION" != "reset" ]]; then
    exec python gemini_chat.py "$@"
fi

CONVERSATION_NAME="${2:-}"
PROMPT="${*:3}"
LOCK_FILE="/tmp/ai-router-gemini.lock"
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
        --provider gemini \
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
    finish_lock_failure "[Router] Gemini provider lock unavailable: flock not found" 127
fi

if ! exec {LOCK_FD}>"$LOCK_FILE"; then
    finish_lock_failure "[Router] Gemini provider lock unavailable" 1
fi

if ! flock -n "$LOCK_FD"; then
    echo "[Router] Gemini当前正在处理另一个任务，等待可用..."
    if ! flock -w "$LOCK_WAIT_SECONDS" "$LOCK_FD"; then
        finish_lock_failure "[Router] Gemini provider lock timeout" 124
    fi
fi

OUTPUT_FILE=$(mktemp /tmp/ai-router-gemini.XXXXXX) || {
    python gemini_chat.py "$@"
    exit $?
}
trap 'rm -f -- "$OUTPUT_FILE"' EXIT

python -u gemini_chat.py "$@" 2>&1 | tee "$OUTPUT_FILE"
STATUS=${PIPESTATUS[0]}
END_NS=$(date +%s%N)
DURATION_MS=$(( (END_NS - START_NS) / 1000000 ))

python router_log.py \
    --provider gemini \
    --action "$ACTION" \
    --conversation-name "$CONVERSATION_NAME" \
    --exit-code "$STATUS" \
    --duration-ms "$DURATION_MS" \
    --output-file "$OUTPUT_FILE" \
    --prompt "$PROMPT" \
    >/dev/null 2>&1 || true

exit "$STATUS"
