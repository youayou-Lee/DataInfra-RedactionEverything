#!/usr/bin/env bash
# vLLM 0.9.2(DTK25.04.2) 服务 HaS Qwen3-0.6B —— NER 高速路线
# 2026-09-10 scnet-bw 实测: 3/3 全命中 @0.53s (与 25.04.1 同速), cudagraph 正常无需 eager
set -uo pipefail
MODEL=/root/redaction/DataInfra-RedactionEverything/backend/models/has/HaS_Text_0209_0.6B
LOG_DIR=/root/redaction/cloud-deploy/logs; mkdir -p "$LOG_DIR"
SESSION=redaction
: '${SCNET_PROXY_URL:?export SCNET_PROXY_URL=平台代理}'; export http_proxy="$SCNET_PROXY_URL"
export https_proxy="$http_proxy"
export no_proxy='localhost,127.0.0.1,0.0.0.0' NO_PROXY='localhost,127.0.0.1,0.0.0.0'
PROXY_EXPORT="export http_proxy='$http_proxy' https_proxy='$https_proxy' no_proxy=localhost,127.0.0.1,0.0.0.0;"
DTK_EXPORT="set +u; source /opt/dtk/env.sh; set -u;"

tmux has-session -t "$SESSION" 2>/dev/null || tmux new-session -d -s "$SESSION" -n init "sleep infinity"
tmux kill-window -t "$SESSION:vllm-ner" 2>/dev/null || true
tmux new-window -d -t "$SESSION" -n vllm-ner "$PROXY_EXPORT $DTK_EXPORT cd /root && \
  CUDA_VISIBLE_DEVICES=0 HF_HUB_OFFLINE=1 VLLM_USE_V1=0 \
  python3 -m vllm.entrypoints.openai.api_server \
  --model '$MODEL' --served-model-name HaS_Text_0209_0.6B \
  --trust-remote-code --dtype bfloat16 --max-model-len 8192 \
  --gpu-memory-utilization 0.25 --host 127.0.0.1 --port 8081 \
  2>&1 | tee -a $LOG_DIR/vllm-ner.log"

w=0; printf "等 vLLM-NER(8081) "
until curl -sf -m 3 http://127.0.0.1:8081/v1/models >/dev/null 2>&1; do
  sleep 5; w=$((w+5)); printf "."
  [ $w -ge 900 ] && { echo " 超时"; tail -30 "$LOG_DIR/vllm-ner.log"; exit 1; }
done
echo " OK (${w}s)"
curl -s -m 5 http://127.0.0.1:8081/v1/models | head -c 200; echo
