#!/usr/bin/env bash
# OCR GPU 服务 —— DTK25.04.2 镜像专用（paddle-dcu 3.2.1 = GPU 唯一验证线）
# 2026-09-10 scnet-bw 实测: DTK25.04.2+gfx926 conv/pool PASS, PPStructureV3 GPU 可用
set -uo pipefail
P=/root/private_data/redaction-persist
BACKEND=/root/redaction/DataInfra-RedactionEverything/backend
LOG_DIR=/root/redaction/cloud-deploy/logs; mkdir -p "$LOG_DIR"
SESSION=redaction
: '${SCNET_PROXY_URL:?export SCNET_PROXY_URL=平台代理}'; export http_proxy="$SCNET_PROXY_URL"
export https_proxy="$http_proxy"
export no_proxy='localhost,127.0.0.1,0.0.0.0' NO_PROXY='localhost,127.0.0.1,0.0.0.0'
PROXY_EXPORT="export http_proxy='$http_proxy' https_proxy='$https_proxy' no_proxy=localhost,127.0.0.1,0.0.0.0;"
DTK_EXPORT="set +u; source /opt/dtk/env.sh; set -u;"

tmux has-session -t "$SESSION" 2>/dev/null || tmux new-session -d -s "$SESSION" -n init "sleep infinity"
tmux kill-window -t "$SESSION:ocr" 2>/dev/null || true
tmux new-window -d -t "$SESSION" -n ocr "$PROXY_EXPORT $DTK_EXPORT cd '$BACKEND' && \
  HIP_VISIBLE_DEVICES=0 OCR_VL_ENABLED=0 OCR_REQUIRE_GPU=true OCR_DEVICE=dcu:0 \
  OCR_STRUCTURE_ENABLED=1 OCR_STRUCTURE_PRIMARY=1 OCR_STRUCTURE_WARMUP=1 \
  OCR_STRUCTURE_RELEASE_AFTER_REQUEST=0 OCR_MAX_IMAGE_SIDE=2048 OCR_MAX_NEW_TOKENS=2048 \
  PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True PADDLE_PDX_DISABLE_DEV_MODEL_WL=true \
  PADDLE_PDX_CACHE_HOME=$P/paddlex-cache \
  PYTHONPATH='$BACKEND' \
  $P/dot-venvs/paddle-25041/bin/python '$BACKEND'/scripts/ocr_server.py 2>&1 | tee -a $LOG_DIR/ocr-gpu.log"

w=0; printf "等 OCR-GPU(paddle-dcu 3.2.1) "
until curl -sf -m 3 http://127.0.0.1:8082/health >/dev/null 2>&1; do
  sleep 5; w=$((w+5)); printf "."
  [ $w -ge 900 ] && { echo " 超时"; tail -30 "$LOG_DIR/ocr-gpu.log"; exit 1; }
done
echo " OK (${w}s)"
curl -s -m 5 http://127.0.0.1:8082/health; echo
