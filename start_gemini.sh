#!/bin/bash

PORT=9222
PROFILE="/home/ubuntu/ai-router/profiles/gemini"
URL="https://gemini.google.com/app"
LOG="/tmp/gemini-chrome.log"

echo "======================================"
echo " Gemini Web 节点启动检查"
echo "======================================"

# 1. 如果节点已经在线，不重复启动
if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
    echo "[正常] Gemini 已在线，端口：${PORT}"
    exit 0
fi

echo "[提示] Gemini 当前未在线。"

# 2. 清理使用 Gemini Profile 的残留 Chrome
if pgrep -af "chrome.*--user-data-dir=${PROFILE}" >/dev/null 2>&1; then
    echo "[处理] 检测到 Gemini Chrome 残留进程，正在清理..."
    pkill -f "chrome.*--user-data-dir=${PROFILE}" 2>/dev/null
    sleep 2
fi

# 3. 自动寻找当前 XFCE/XRDP 图形桌面
DISPLAY_NUM=$(ps -u "$USER" -o args= | sed -n 's/.*Xorg \(:[0-9][0-9]*\).*/\1/p' | head -n 1)

if [ -z "$DISPLAY_NUM" ]; then
    echo
    echo "[失败] 没有检测到正在运行的 XFCE/XRDP 图形桌面。"
    echo
    echo "请先："
    echo "1. 在 Windows 双击远程桌面隧道 BAT"
    echo "2. 使用 mstsc 登录 Ubuntu 桌面"
    echo "3. 登录成功后再次运行 ./start_gemini.sh"
    exit 2
fi

echo "[正常] 检测到图形桌面：${DISPLAY_NUM}"

export DISPLAY="$DISPLAY_NUM"
export XAUTHORITY="/home/ubuntu/.Xauthority"

# 4. 启动 Gemini 专用 Chrome
echo "[启动] 正在启动 Gemini Chrome..."

nohup /usr/bin/google-chrome \
    --remote-debugging-port=${PORT} \
    --user-data-dir="${PROFILE}" \
    --no-first-run \
    --disable-dev-shm-usage \
    --disable-gpu \
    --new-window "${URL}" \
    >"${LOG}" 2>&1 &

# 5. 等待远程调试端口上线
for i in $(seq 1 20); do
    if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
        echo
        echo "[成功] Gemini Web 节点已经启动。"
        echo "[地址] ${URL}"
        echo "[端口] ${PORT}"
        echo "[Profile] ${PROFILE}"
        exit 0
    fi

    sleep 1
done

echo
echo "[失败] Chrome 已尝试启动，但 9222 端口没有上线。"
echo "最近的 Chrome 日志："
echo "--------------------------------------"
tail -30 "${LOG}"
exit 1
