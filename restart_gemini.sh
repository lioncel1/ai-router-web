#!/bin/bash

PROFILE="/home/ubuntu/ai-router/profiles/gemini"

echo "正在停止 Gemini Chrome..."

pkill -f "chrome.*--user-data-dir=${PROFILE}" 2>/dev/null || true

sleep 3

echo "正在重新启动 Gemini..."
exec /home/ubuntu/ai-router/start_gemini.sh
