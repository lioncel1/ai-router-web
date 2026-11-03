#!/bin/bash

cd /home/ubuntu/ai-router || {
    echo "无法进入 /home/ubuntu/ai-router"
    exit 1
}

find_chrome_pid() {
    local port="$1"
    if command -v pgrep >/dev/null 2>&1; then
        pgrep -f "chrome.*--remote-debugging-port=$port" 2>/dev/null | head -n 1
    fi
}

print_node_status() {
    local label="$1"
    local port="$2"
    local node_state
    local chrome_pid

    if curl -fsS --max-time 2 "http://127.0.0.1:$port/json/version" >/dev/null 2>&1; then
        node_state="ONLINE"
    else
        node_state="OFFLINE"
    fi

    chrome_pid=$(find_chrome_pid "$port")
    echo "$label："
    echo "  状态：$node_state"
    echo "  端口：$port"
    if [ -n "$chrome_pid" ]; then
        echo "  Chrome PID：$chrome_pid"
    else
        echo "  Chrome PID：未找到"
    fi
}

echo "======================================"
echo " AI Router 状态"
echo "======================================"
echo

print_node_status "Gemini Web" "9222"
echo
print_node_status "ChatGPT Web" "9223"

echo
echo "========== 图形桌面 =========="

DISPLAY_NUM=$(ps -u "$USER" -o args= 2>/dev/null | sed -n 's/.*Xorg \(:[0-9][0-9]*\).*/\1/p' | head -n 1)

if [ -n "$DISPLAY_NUM" ]; then
    echo "XFCE/XRDP：ONLINE ($DISPLAY_NUM)"
else
    echo "XFCE/XRDP：OFFLINE"
fi

echo
echo "========== Profile =========="

echo -n "Gemini："
du -sh profiles/gemini 2>/dev/null || echo "不存在"

echo -n "ChatGPT："
du -sh profiles/chatgpt 2>/dev/null || echo "不存在"

if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
    PYTHON_BIN="python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
    echo
    echo "提示：未找到项目虚拟环境，使用系统 python3。"
else
    PYTHON_BIN=""
    echo
    echo "提示：未找到可用的 Python，跳过 JSON 状态分析和会话列表。"
fi

echo
if [ -n "$PYTHON_BIN" ]; then
    "$PYTHON_BIN" router_status.py || echo "状态数据分析失败，继续检查其他项目。"
else
    echo "========== 最近调用 / 统计 / 异常会话 =========="
    echo "无法运行：Python 不可用"
fi

echo
echo "========== Gemini会话 =========="

if [ -n "$PYTHON_BIN" ]; then
    "$PYTHON_BIN" gemini_chat.py list || echo "Gemini 会话列表读取失败"
else
    echo "无法读取：Python 不可用"
fi

echo
echo "========== ChatGPT会话 =========="

if [ -n "$PYTHON_BIN" ]; then
    "$PYTHON_BIN" chatgpt_chat.py list || echo "ChatGPT 会话列表读取失败"
else
    echo "无法读取：Python 不可用"
fi

echo
echo "========== 内存 / Swap =========="

free -h 2>/dev/null || echo "内存信息读取失败"

echo
echo "========== 磁盘 =========="

df -h / 2>/dev/null || echo "磁盘信息读取失败"

exit 0
