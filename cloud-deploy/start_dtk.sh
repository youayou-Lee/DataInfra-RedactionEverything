#!/usr/bin/env bash
# DTK 环境启动：5 个服务按依赖顺序拉起并等待健康。
# NER 用 transformers 自包 OpenAI 兼容服务(ner_transformers_server.py)——DTK 上 pip 装 vLLM
# 会拖 CUDA 版 torch 覆盖 DTK 栈，不可行；本服务与 backend 的 HAS_BASE_URL 契约完全一致。
# 显存布局: 实测实例 K100_AI 单卡 64G → NER ~2G + LocateAnything ~10G 共卡0；双卡时 LA 自适应上卡1。
# OCR/backend/frontend 在 CPU。
# 凭据脱敏: 平台代理账密不入库, 运行前必须 export SCNET_PROXY_URL='http://<user>:<pass>@<host>:<port>'
: "${SCNET_PROXY_URL:?请先 export SCNET_PROXY_URL(平台控制台获取)}"

# 坑(L20 实测⑤⑥): tmux 服务器环境在首次启动时定死，.bashrc/.env 的变量传不进窗口
# → 脚本级 export(首启 tmux server 继承本进程环境) + 每个 spawn 显式前缀双保险
export no_proxy='localhost,127.0.0.1,0.0.0.0'
export NO_PROXY='localhost,127.0.0.1,0.0.0.0'
export http_proxy="$SCNET_PROXY_URL"
export https_proxy="$SCNET_PROXY_URL"
PROXY_EXPORT="export http_proxy='$http_proxy' https_proxy='$https_proxy' no_proxy=localhost,127.0.0.1,0.0.0.0;"

# 坑(实测): 非 interactive shell 不加载 profile.d → torch 报 libgalaxyhip.so.5 缺失
# (env.sh 按 interactive 环境写, 引用未定义变量, set -u 下直接炸 → 临时关/开 -u)
set +u
source /opt/dtk/env.sh
set -u
# MIOpen 算子内核缓存落持久卷: 首次 JIT(~60s/新形状)的结果重启/换实例不再重算
# 哨兵检查: 持久卷未挂载时 mkdir 会在容器临时层"成功", 缓存持久化的意义悄然落空
if [ ! -d /root/private_data/redaction-persist/dot-venvs ] && [ ! -d /root/private_data/redaction-persist/backend-models ]; then
    echo "警告: 平台持久卷疑似未挂载(缺 dot-venvs/backend-models 哨兵), miopen/paddlex 缓存将落临时层"
fi
mkdir -p /root/private_data/redaction-persist/miopen-cache 2>/dev/null || mkdir -p /root/.cache/miopen
export MIOPEN_USER_CACHE_PATH=/root/private_data/redaction-persist/miopen-cache
DTK_EXPORT="source /opt/dtk/env.sh; export MIOPEN_USER_CACHE_PATH=/root/private_data/redaction-persist/miopen-cache;"

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPSTREAM="$(cd "$DEPLOY_DIR/../DataInfra-RedactionEverything" && pwd)"
BACKEND="$UPSTREAM/backend"
FRONTEND="$UPSTREAM/frontend"
LOG_DIR="$DEPLOY_DIR/logs"; mkdir -p "$LOG_DIR"
SESSION=redaction

set -a; source "$UPSTREAM/.env"; set +a
PY_APP="$VENV_DIR/bin/python"
PY_NL="$HOME/.venvs/nl/bin/python"

for f in "$PY_APP" "$PY_NL" "$HAS_TEXT_HF_MODEL_PATH/config.json"; do
    [ -e "$f" ] || { echo "缺少 $f，先跑 setup_dtk.sh"; exit 1; }
done

# 双卡分配: 卡0 = NER(+YOLOE), 卡1 = LocateAnything(10G 大头)；单卡则共卡0
NGPU=$("$PY_NL" -c 'import torch; print(torch.cuda.device_count())' 2>/dev/null || echo 1)
LA_DEV=$(( NGPU >= 2 ? 1 : 0 ))
LA_DTYPE="${LA_DTYPE:-bfloat16}"   # DCU 若 bf16 算子缺失导致 LA 起不来: LA_DTYPE=float16 ./start_dtk.sh
echo "DCU 数: $NGPU → LocateAnything 用卡 $LA_DEV (dtype=$LA_DTYPE)"

wait_health() {
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

spawn() {
    local win="$1" cmd="$2"
    tmux kill-window -t "$SESSION:$win" 2>/dev/null || true
    tmux new-window -d -t "$SESSION" -n "$win" "$cmd 2>&1 | tee -a '$LOG_DIR/$win.log'"
}

tmux has-session -t "$SESSION" 2>/dev/null || tmux new-session -d -s "$SESSION" -n init "sleep infinity"

echo "==> 1/5 NER 服务 (8080, transformers 自包 OpenAI 兼容, DCU0)"
spawn ner "$PROXY_EXPORT $DTK_EXPORT cd '$UPSTREAM' && HF_HUB_OFFLINE=1 CUDA_VISIBLE_DEVICES=0 \
    '$PY_NL' '$DEPLOY_DIR/ner_transformers_server.py' \
    --model '$HAS_TEXT_HF_MODEL_PATH' --host 0.0.0.0 --port 8080 --device cuda:0"
wait_health http://127.0.0.1:8080/v1/models "NER transformers" 600

echo "==> 2/5 LocateAnything 视觉服务 (8090, HF 模式, DCU$LA_DEV)"
spawn locateanything "$PROXY_EXPORT $DTK_EXPORT cd '$UPSTREAM' && CUDA_VISIBLE_DEVICES=$LA_DEV HF_HUB_OFFLINE=1 \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    LOCATE_ANYTHING_MODEL_NAME=LocateAnything-3B \
    LOCATE_ANYTHING_MAX_IMAGE_SIDE=1280 LOCATE_ANYTHING_MAX_NEW_TOKENS=8192 \
    LOCATE_ANYTHING_GENERATION_MODE=hybrid LOCATE_ANYTHING_FAST_FIRST=1 \
    LOCATE_ANYTHING_TEMPERATURE=0.7 LOCATE_ANYTHING_VLLM_URL= \
    LOCATE_ANYTHING_VLLM_MODEL=locate_qwen2_model \
    PYTHONPATH='$HOME/.venvs/locateanything-hf-deps:$BACKEND/scripts:$BACKEND' \
    '$PY_NL' '$BACKEND/scripts/locate_anything_server.py' \
    --model '$BACKEND/models/locateanything/LocateAnything-3B-HF' \
    --backend hf --host 0.0.0.0 --port 8090 --dtype $LA_DTYPE"
wait_health http://127.0.0.1:8090/health "LocateAnything" 900

echo "==> 3/5 OCR PP-StructureV3 (8082, CPU 模式, 首启经代理下载 PaddleX 模型)"
# 坑(L20 实测⑤复发): OCR_REQUIRE_GPU 必须显式内联——tmux 服务器不继承脚本 source 的 .env,
# 漏传则 CPU 版 paddle 撞 GPU 硬检查 FATAL。PADDLE_PDX_CACHE_HOME 把模型缓存(~/.paddlex
# 默认, 不在持久卷)指到持久卷, 关机保存/换实例免重下 ~500MB
spawn ocr "$PROXY_EXPORT cd '$BACKEND' && CUDA_VISIBLE_DEVICES= OCR_VL_ENABLED=0 OCR_REQUIRE_GPU=false \
    OCR_STRUCTURE_ENABLED=1 OCR_STRUCTURE_PRIMARY=1 OCR_STRUCTURE_WARMUP=1 \
    OCR_STRUCTURE_RELEASE_AFTER_REQUEST=0 OCR_MAX_IMAGE_SIDE=2048 \
    OCR_MAX_NEW_TOKENS=2048 PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
    PADDLE_PDX_CACHE_HOME=/root/private_data/redaction-persist/paddlex-cache \
    PYTHONPATH='$BACKEND' \
    '$PY_APP' '$BACKEND/scripts/ocr_server.py'"
wait_health http://127.0.0.1:8082/health "OCR" 900

echo "==> 4/5 FastAPI 后端 (8000)"
# CORS_ORIGINS 显式传: source .env 会被 bash 吃掉 JSON 双引号(L20 实测坑⑥)
spawn backend "$PROXY_EXPORT cd '$BACKEND' && DEBUG=false AUTH_ENABLED=true JOB_CONCURRENCY=2 OCR_REQUIRE_GPU=false \
    CORS_ORIGINS='[\"http://localhost:3000\"]' \
    '$PY_APP' -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
wait_health http://127.0.0.1:8000/health "backend" 120

echo "==> 5/5 前端 (3000, vite preview + /api 代理)"
export PATH="$HOME/.local/node-v22/bin:$PATH"
spawn frontend "export PATH=$HOME/.local/node-v22/bin:\$PATH no_proxy=localhost,127.0.0.1,0.0.0.0; \
    cd '$FRONTEND' && npm run preview -- --host 0.0.0.0 --port 3000 --strictPort"
wait_health http://127.0.0.1:3000 "frontend" 60

echo
echo "全部就绪:"
curl -s -m 5 http://127.0.0.1:8000/health/services | head -c 400; echo
echo "  tmux attach -t $SESSION   # 查看服务（Ctrl+B 数字切窗口）"
