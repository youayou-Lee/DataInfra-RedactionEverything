#!/usr/bin/env bash
# 云端启动：tmux 会话 redaction，5 个服务按依赖顺序拉起并等待健康。
# 服务参数与启动目录 = 已验证的 Docker compose / dev.mjs 同款。
# 凭据脱敏: 平台代理账密不入库, 运行前必须 export SCNET_PROXY_URL='http://<user>:<pass>@<host>:<port>'
: "${SCNET_PROXY_URL:?请先 export SCNET_PROXY_URL(平台控制台获取)}"

# 健康检查 curl 127.0.0.1 不能走代理；conda python 进 PATH
export no_proxy='localhost,127.0.0.1,0.0.0.0'
export NO_PROXY='localhost,127.0.0.1,0.0.0.0'
# 平台代理（PaddleX 模型下载等需要出网的窗口；tmux 服务器环境不继承时显式传）
export http_proxy="$SCNET_PROXY_URL"
export https_proxy="$SCNET_PROXY_URL"

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPSTREAM="$(cd "$DEPLOY_DIR/../DataInfra-RedactionEverything" && pwd)"
BACKEND="$UPSTREAM/backend"
FRONTEND="$UPSTREAM/frontend"
LOG_DIR="$DEPLOY_DIR/logs"; mkdir -p "$LOG_DIR"
SESSION=redaction

# 加载 .env（VENV 路径、模型路径、服务地址、JWT 等）
set -a; source "$UPSTREAM/.env"; set +a
PY_APP="$VENV_DIR/bin/python"
PY_VLLM="$VLLM_VENV_DIR/bin/python"

for f in "$PY_APP" "$PY_VLLM" "$HAS_TEXT_HF_MODEL_PATH/config.json"; do
    [ -e "$f" ] || { echo "缺少 $f，先跑 setup_cloud.sh"; exit 1; }
done

wait_health() { # url 描述 超时秒
    local url="$1" name="$2" timeout="${3:-600}" waited=0
    printf "    等待 %s (%s) " "$name" "$url"
    while ! curl -sf -m 3 "$url" >/dev/null 2>&1; do
        sleep 3; waited=$((waited+3)); printf "."
        if [ "$waited" -ge "$timeout" ]; then
            echo; echo "错误: $name 在 ${timeout}s 内未就绪，查看 $LOG_DIR/"; exit 1
        fi
    done
    echo " OK (${waited}s)"
}

spawn() { # 窗口名 完整shell命令(单字符串)
    local win="$1" cmd="$2"
    tmux kill-window -t "$SESSION:$win" 2>/dev/null || true
    tmux new-window -d -t "$SESSION" -n "$win" "$cmd 2>&1 | tee -a '$LOG_DIR/$win.log'"
}

tmux has-session -t "$SESSION" 2>/dev/null || tmux new-session -d -s "$SESSION" -n init "sleep infinity"

echo "==> 1/5 vLLM serve HaS_Text (8080)"
spawn vllm-ner "cd '$UPSTREAM' && CUDA_VISIBLE_DEVICES=0 '$PY_VLLM' -m vllm.entrypoints.openai.api_server \
    --model '$HAS_TEXT_HF_MODEL_PATH' \
    --served-model-name HaS_Text_0209_0.6B \
    --host 0.0.0.0 --port 8080 \
    --trust-remote-code --dtype bfloat16 \
    --max-model-len 4096 --max-num-batched-tokens 4096 \
    --gpu-memory-utilization 0.18 \
    --no-enable-prefix-caching --enforce-eager"
wait_health http://127.0.0.1:8080/v1/models "vLLM HaS" 900

echo "==> 2/5 LocateAnything 视觉服务 (8090, HF 模式 = Docker visual-features 同款)"
spawn locateanything "cd '$UPSTREAM' && CUDA_VISIBLE_DEVICES=0 HF_HUB_OFFLINE=1 \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    LOCATE_ANYTHING_MODEL_NAME=LocateAnything-3B \
    LOCATE_ANYTHING_MAX_IMAGE_SIDE=1280 LOCATE_ANYTHING_MAX_NEW_TOKENS=8192 \
    LOCATE_ANYTHING_GENERATION_MODE=hybrid LOCATE_ANYTHING_FAST_FIRST=1 \
    LOCATE_ANYTHING_TEMPERATURE=0.7 LOCATE_ANYTHING_VLLM_URL= \
    LOCATE_ANYTHING_VLLM_MODEL=locate_qwen2_model \
    PYTHONPATH='$LOCATE_ANYTHING_DEPS:$BACKEND/scripts:$BACKEND' \
    '$PY_VLLM' '$BACKEND/scripts/locate_anything_server.py' \
    --model '$BACKEND/models/locateanything/LocateAnything-3B-HF' \
    --backend hf --host 0.0.0.0 --port 8090 --dtype bfloat16"
wait_health http://127.0.0.1:8090/health "LocateAnything" 900

echo "==> 3/5 OCR PP-StructureV3 (8082, CPU 模式 = Docker ocr 同款)"
spawn ocr "cd '$BACKEND' && CUDA_VISIBLE_DEVICES= OCR_VL_ENABLED=0 OCR_REQUIRE_GPU=false \
    OCR_STRUCTURE_ENABLED=1 OCR_STRUCTURE_PRIMARY=1 OCR_STRUCTURE_WARMUP=1 \
    OCR_STRUCTURE_RELEASE_AFTER_REQUEST=0 OCR_MAX_IMAGE_SIDE=2048 \
    OCR_MAX_NEW_TOKENS=2048 PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
    PYTHONPATH='$BACKEND' \
    '$PY_APP' '$BACKEND/scripts/ocr_server.py'"
wait_health http://127.0.0.1:8082/health "OCR" 900

echo "==> 4/5 FastAPI 后端 (8000)"
spawn backend "cd '$BACKEND' && DEBUG=false AUTH_ENABLED=true JOB_CONCURRENCY=2 OCR_REQUIRE_GPU=false \
    CORS_ORIGINS='[\"http://localhost:3000\"]' \
    '$PY_APP' -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
wait_health http://127.0.0.1:8000/health "backend" 120

echo "==> 5/5 前端 (3000, vite preview + /api 代理)"
export PATH="$HOME/.local/node-v22/bin:$PATH"
spawn frontend "cd '$FRONTEND' && npm run preview -- --host 0.0.0.0 --port 3000 --strictPort"
wait_health http://127.0.0.1:3000 "frontend" 60

echo
echo "全部就绪:"
curl -s -m 5 http://127.0.0.1:8000/health/services | head -c 300; echo
echo "  tmux attach -t $SESSION   # 查看各服务（Ctrl+B 数字切窗口）"
echo "  本地: ssh -N -L 3000:localhost:3000 <user@host>  → 浏览器 http://localhost:3000"
