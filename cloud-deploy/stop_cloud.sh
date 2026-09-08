#!/usr/bin/env bash
# 云端停止：结束 tmux 会话 + 进程兜底清理。
set -uo pipefail

SESSION=redaction

echo "==> 结束 tmux 会话 $SESSION"
tmux kill-session -t "$SESSION" 2>/dev/null && echo "    OK" || echo "    会话不存在"

echo "==> 进程兜底清理"
for pat in "vllm.entrypoints.openai.api_server" "locate_anything_server.py" \
           "scripts/ocr_server.py" "uvicorn app.main:app" "vite.*--port 3000"; do
    pkill -f "$pat" 2>/dev/null && echo "    killed: $pat" || true
done

sleep 1
echo
echo "已停止。./start_cloud.sh 可再次启动。"
