#!/usr/bin/env bash
# 云端一次性环境安装。可重复执行（已装的部分自动跳过）。
# 前置：代码已解包到脚本所在仓库布局中（见 README_CLOUD.md 步骤②）
# 凭据脱敏: 平台代理账密不入库, 运行前必须 export SCNET_PROXY_URL='http://<user>:<pass>@<host>:<port>'
: "${SCNET_PROXY_URL:?请先 export SCNET_PROXY_URL(平台控制台获取)}"

# SCNet 实例适配：conda python3 进 PATH + 平台代理（pip/npm/hf 都需要）
export PATH=/opt/conda/bin:$PATH
export http_proxy="$SCNET_PROXY_URL"
export https_proxy="$SCNET_PROXY_URL"
export no_proxy='localhost,127.0.0.1,0.0.0.0'
export HF_ENDPOINT=https://hf-mirror.com

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPSTREAM="$DEPLOY_DIR/../DataInfra-RedactionEverything"
BACKEND="$UPSTREAM/backend"
FRONTEND="$UPSTREAM/frontend"
MODELS="$BACKEND/models"
LOG_DIR="$DEPLOY_DIR/logs"; mkdir -p "$LOG_DIR"

PIP_ARGS="-i https://pypi.tuna.tsinghua.edu.cn/simple"
HAS_MODEL_DIR="$MODELS/has/HaS_Text_0209_0.6B"
VENV_APP="$HOME/.venvs/app"
VENV_VLLM="$HOME/.venvs/vllm"
LA_DEPS="$HOME/.venvs/locateanything-hf-deps"

log() { echo; echo "=====> $* <====="; }

# ---------- 0. 基础检查 ----------
log "环境检查"
command -v python3 >/dev/null || { echo "缺少 python3"; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3,10), "需要 Python>=3.10"' || exit 1
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader || { echo "GPU 不可用"; exit 1; }
AVAIL_GB=$(df --output=avail -BG "$HOME" | tail -1 | tr -dc '0-9')
echo "磁盘可用: ${AVAIL_GB}GB (需约 25GB)"
[ "$AVAIL_GB" -lt 25 ] && echo "警告: 磁盘空间可能不足"

for pkg in tar curl tmux; do command -v $pkg >/dev/null || { echo "缺少 $pkg，请先安装"; exit 1; }; done

# ---------- 1. venv-app: backend + OCR ----------
log "venv-app（backend + PP-StructureV3 OCR, CPU paddle）"
# 印章式幂等: activate 存在≠装完(半途失败的安装会被永久跳过), 以装完末尾的印章为准
if [ ! -f "$VENV_APP/.install-complete" ]; then
    python3 -m venv "$VENV_APP"
    "$VENV_APP/bin/pip" install -q --upgrade pip
    # requirements.txt 里的 paddlepaddle-gpu 是 cu129 wheel，需要驱动>=575，
    # 云机驱动不满足；替换为 CPU 版 3.2.2（与已验证的 Docker OCR 容器同版本行为）
    [ -f "$BACKEND/requirements.txt" ] || { echo "缺少 requirements.txt — 未按 README 步骤② 解包代码"; exit 1; }
    grep -vE '^\s*(--extra-index-url|paddlepaddle-gpu)' "$BACKEND/requirements.txt" > /tmp/req-cloud.txt
    "$VENV_APP/bin/pip" install $PIP_ARGS paddlepaddle==3.2.2
    "$VENV_APP/bin/pip" install $PIP_ARGS -r /tmp/req-cloud.txt
    "$VENV_APP/bin/pip" install $PIP_ARGS -U "huggingface_hub"
    touch "$VENV_APP/.install-complete"
fi
"$VENV_APP/bin/python" -c "import paddle; print('paddle', paddle.__version__)" 2>/dev/null \
  || echo "警告: paddle 导入失败，检查 logs"

# ---------- 2. venv-vllm: vLLM + LocateAnything ----------
log "venv-vllm（vLLM + LocateAnything）"
if [ ! -f "$VENV_VLLM/.install-vllm" ]; then
    python3 -m venv "$VENV_VLLM"
    "$VENV_VLLM/bin/pip" install -q --upgrade pip
    # vLLM 0.8.5 对应 torch 2.7.0（cu126 wheel 兼容驱动 535/CUDA 11.8 实例）
    "$VENV_VLLM/bin/pip" install $PIP_ARGS vllm==0.8.5    # 自带匹配的 torch 栈（约2.5GB，耐心等）
    touch "$VENV_VLLM/.install-vllm"
fi
# LocateAnything 附加依赖装到独立 --target 目录（版本锁不污染 venv），独立判存可修复重装
if [ ! -d "$LA_DEPS/peft" ]; then
    "$VENV_VLLM/bin/pip" install $PIP_ARGS --target "$LA_DEPS" -r "$BACKEND/requirements-locateanything.txt"
    # 坑(L20 实测③): --target 会拖进 CUDA torch/numpy, PYTHONPATH 优先于 venv torch → 必删
    rm -rf "$LA_DEPS"/torch* "$LA_DEPS"/numpy*
fi

# ---------- 3. 模型权重 ----------
log "HaS_Text_0209_0.6B 权重 (1.2GB, hf-mirror)"
if [ ! -f "$HAS_MODEL_DIR/config.json" ]; then
    mkdir -p "$(dirname "$HAS_MODEL_DIR")"
    export HF_ENDPOINT=https://hf-mirror.com
    if ! "$VENV_APP/bin/hf" download xuanwulab/HaS_Text_0209_0.6B --local-dir "$HAS_MODEL_DIR" 2>&1 | tail -3; then
        "$VENV_APP/bin/huggingface-cli" download xuanwulab/HaS_Text_0209_0.6B --local-dir "$HAS_MODEL_DIR" 2>&1 | tail -3
    fi
fi
[ -f "$HAS_MODEL_DIR/config.json" ] && echo "HaS 模型 OK" || { echo "HaS 模型缺失"; exit 1; }
[ -d "$MODELS/locateanything/LocateAnything-3B-HF" ] && echo "LocateAnything 权重 OK" \
  || echo "警告: LocateAnything 权重尚未到位（可与 setup 并行上传，start 前到位即可）"

# ---------- 4. .env ----------
log "生成 .env"
if [ ! -f "$UPSTREAM/.env" ]; then
    JWT=$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p | tr -d '\n')
    cat > "$UPSTREAM/.env" <<EOF
DEBUG=false
AUTH_ENABLED=true
JWT_SECRET_KEY=$JWT
JOB_CONCURRENCY=2
OCR_REQUIRE_GPU=false

# --- 源码方式服务地址（本机回环） ---
OCR_BASE_URL=http://127.0.0.1:8082
HAS_TEXT_VLLM_BASE_URL=http://127.0.0.1:8080/v1
HAS_TEXT_MODEL_NAME=HaS_Text_0209_0.6B
VISUAL_FEATURES_BASE_URL=http://127.0.0.1:8090
CORS_ORIGINS=["http://localhost:3000"]

# --- 源码方式路径 ---
VENV_DIR=$VENV_APP
VLLM_VENV_DIR=$VENV_VLLM
LOCATE_ANYTHING_DEPS=$LA_DEPS
HAS_TEXT_HF_MODEL_PATH=$HAS_MODEL_DIR
EOF
    echo ".env 已生成（JWT 已随机生成）"
else
    echo ".env 已存在，跳过"
fi

# backend 只读 backend/.env (config.py BACKEND_DIR=parents[2]); 缺失时 VISUAL_FEATURES_BASE_URL
# 回落默认 9090(实际 8090) → LA 被静默判 offline。软链对齐, 不依赖 tmux 环境继承的侥幸
ln -sfn "$UPSTREAM/.env" "$BACKEND/.env"
echo "backend/.env -> $(readlink "$BACKEND/.env")"

# ---------- 5. Node + 前端构建 ----------
log "Node 22 + 前端构建"
NODE_DIR="$HOME/.local/node-v22"
if [ ! -x "$NODE_DIR/bin/node" ]; then
    ARCH=$(uname -m); [ "$ARCH" = "aarch64" ] && NARCH=arm64 || NARCH=x64
    mkdir -p "$(dirname "$NODE_DIR")"
    curl -fsSL "https://npmmirror.com/mirrors/node/v22.14.0/node-v22.14.0-linux-$NARCH.tar.xz" -o /tmp/node.tar.xz
    mkdir -p "$NODE_DIR" && tar xJf /tmp/node.tar.xz -C "$NODE_DIR" --strip-components=1 && rm /tmp/node.tar.xz
fi
export PATH="$NODE_DIR/bin:$PATH"
node --version
if [ ! -d "$FRONTEND/dist" ]; then
    (cd "$FRONTEND" && npm ci --registry=https://registry.npmmirror.com && npm run build)
fi
[ -d "$FRONTEND/dist" ] && echo "前端构建产物 OK"

log "setup 完成。运行 ./start_cloud.sh 启动全部服务"
