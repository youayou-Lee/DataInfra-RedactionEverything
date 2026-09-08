#!/usr/bin/env bash
# 本地打包 + 上传到云实例。用法: ./pack_upload.sh user@host [远程目录]
# 产物: 代码包(~10MB) + LocateAnything 权重(7.3GB) + yoloe 权重(880MB)
# 注意: samples/ 下的真实案卷 PDF 不会被打包（敏感数据不上临时云实例）
set -euo pipefail

HOST="${1:?用法: $0 user@host [远程目录]}"
REMOTE_DIR="${2:-~/redaction}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

echo "==> 打包代码（排除 node_modules / venv / 模型 / .git / 案卷样本）..."
tar czf "$OUT/redaction-code.tar.gz" \
    --exclude='node_modules' \
    --exclude='*.venv*' \
    --exclude='.git' \
    --exclude='DataInfra-RedactionEverything/.env' \
    --exclude='DataInfra-RedactionEverything/backend/models' \
    --exclude='DataInfra-RedactionEverything/frontend/dist' \
    --exclude='DataInfra-RedactionEverything/logs' \
    --exclude='yoloe-service/output' \
    --exclude='yoloe-service/samples' \
    --exclude='yoloe-service/*.pt' \
    --exclude='yoloe-service/*.ts' \
    --exclude='yoloe-service/CLIP/notebooks' \
    -C "$ROOT" DataInfra-RedactionEverything yoloe-service cloud-deploy

echo "==> 打包 yoloe 权重 (880MB, 可选; LocateAnything 已改为云上从 hf-mirror 直下, 无需打包)..."
tar czf "$OUT/yoloe-weights.tar.gz" \
    -C "$ROOT/yoloe-service" yoloe-11l-seg.pt mobileclip_blt.ts mobileclip2_b.ts

echo "==> 上传到 $HOST:$REMOTE_DIR ..."
ssh "$HOST" "mkdir -p $REMOTE_DIR"
scp "$OUT"/redaction-code.tar.gz "$OUT"/yoloe-weights.tar.gz "$HOST:$REMOTE_DIR/"

echo
echo "完成。接下来 SSH 登录云机执行："
echo "  ssh $HOST"
echo "  cd $REMOTE_DIR"
echo "  tar xzf redaction-code.tar.gz && mkdir -p yoloe-service && tar xzf yoloe-weights.tar.gz -C yoloe-service"
echo "  ./setup_dtk.sh"
