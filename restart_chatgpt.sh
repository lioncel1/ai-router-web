#!/bin/bash

PROFILE="/home/ubuntu/ai-router/profiles/chatgpt"

echo "正在停止 ChatGPT Chrome..."
pkill -f "chrome.*--user-data-dir=${PROFILE}" 2>/dev/null || true
sleep 3

echo "正在重新启动 ChatGPT..."
exec /home/ubuntu/ai-router/start_chatgpt.sh
