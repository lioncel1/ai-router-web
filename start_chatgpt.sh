#!/bin/bash

PORT=9223
PROFILE="/home/ubuntu/ai-router/profiles/chatgpt"
URL="https://chatgpt.com/"
LOG="/tmp/chatgpt-chrome.log"

echo "======================================"
echo " ChatGPT Web 节点启动检查"
echo "======================================"

if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
    echo "[正常] ChatGPT 已在线，端口：${PORT}"
    exit 0
fi

echo "[提示] ChatGPT 当前未在线。"

if pgrep -af "chrome.*--user-data-dir=${PROFILE}" >/dev/null 2>&1; then
    echo "[处理] 检测到 ChatGPT Chrome 残留进程，正在清理..."
    pkill -f "chrome.*--user-data-dir=${PROFILE}" 2>/dev/null || true
    sleep 2
fi

DISPLAY_NUM=$(ps -u "$USER" -o args= | sed -n 's/.*Xorg \(:[0-9][0-9]*\).*/\1/p' | head -n 1)

if [ -z "$DISPLAY_NUM" ]; then
    echo "[失败] 没有检测到 XFCE/XRDP 图形桌面。"
    echo "请先用 mstsc 登录 Ubuntu 桌面。"
    exit 2
fi

echo "[正常] 图形桌面：${DISPLAY_NUM}"

export DISPLAY="$DISPLAY_NUM"
export XAUTHORITY="/home/ubuntu/.Xauthority"

echo "[启动] 正在启动 ChatGPT Chrome..."

nohup /usr/bin/google-chrome --remote-debugging-port="${PORT}" --user-data-dir="${PROFILE}" --no-first-run --disable-dev-shm-usage --new-window "${URL}" >"${LOG}" 2>&1 &

for i in $(seq 1 20); do
    if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
        echo "[成功] ChatGPT Web 节点已经启动。"
        echo "[端口] ${PORT}"
        echo "[Profile] ${PROFILE}"
        exit 0
    fi
    sleep 1
done

echo "[失败] ChatGPT Chrome 启动失败。"
echo "最近日志："
tail -30 "${LOG}"
exit 1
