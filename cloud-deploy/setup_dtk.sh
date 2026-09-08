#!/usr/bin/env bash
# DTK(海光DCU) 环境一次性安装。前置: 平台镜像已带 torch 2.9(DTK) + python 3.11。
# 实测实例: SCNet K100_AI 单卡 64GB (HIP 6.3), DTK 26.04 在 /opt/dtk。
# 可重复执行。
# 凭据脱敏: 平台代理账密不入库, 运行前必须 export SCNET_PROXY_URL='http://<user>:<pass>@<host>:<port>'
: "${SCNET_PROXY_URL:?请先 export SCNET_PROXY_URL(平台控制台获取)}"

# SCNet 平台代理: 容器访问 pypi/hf-mirror/modelscope/npmmirror 的唯一出网通道(直连全不通)
# 非交互 ssh 执行本脚本时不加载 .bashrc, 这里必须显式 export
export http_proxy="$SCNET_PROXY_URL"
export https_proxy="$SCNET_PROXY_URL"
export no_proxy='localhost,127.0.0.1,0.0.0.0'
export HF_ENDPOINT=https://hf-mirror.com

# DTK 运行时: profile.d 只对登录 shell 生效, 非 interactive 环境 LD_LIBRARY_PATH 为空,
# torch 直接报 ImportError: libgalaxyhip.so.5 → 必须显式 source
# (env.sh 按 interactive 环境写, 追加引用 CMAKE_PREFIX_PATH/LD_LIBRARY_PATH 等未定义变量,
#  set -u 下直接炸 → source 前后临时关/开 -u)
set +u
source /opt/dtk/env.sh
set -u

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPSTREAM="$DEPLOY_DIR/../DataInfra-RedactionEverything"
BACKEND="$UPSTREAM/backend"
FRONTEND="$UPSTREAM/frontend"
MODELS="$BACKEND/models"
LOG_DIR="$DEPLOY_DIR/logs"; mkdir -p "$LOG_DIR"

HAS_MODEL_DIR="$MODELS/has/HaS_Text_0209_0.6B"
LA_MODEL_DIR="$MODELS/locateanything/LocateAnything-3B-HF"
VENV_APP="$HOME/.venvs/app"       # backend + OCR（CPU paddle，无 torch）
VENV_NL="$HOME/.venvs/nl"         # NER transformers 服务 + LocateAnything（复用系统 DTK torch）
LA_DEPS="$HOME/.venvs/locateanything-hf-deps"

log() { echo; echo "=====> $* <====="; }

# ---------- 0. 检查 ----------
log "环境检查"
python3 -c 'import torch; n=torch.cuda.device_count(); assert torch.cuda.is_available() and n>0, "torch.cuda 不可用"; print("torch", torch.__version__, "| DCU 数:", n, "|", torch.cuda.get_device_name(0))'
for pkg in tar curl tmux; do
    command -v $pkg >/dev/null || {
        echo "缺少 $pkg → apt 自动安装(走代理)"
        # 平台 apt 模板源不通且可能版本错配, 换 aliyun 源(L20 同款修复)
        sed -i -e "s|security.ubuntu.com/ubuntu|mirrors.aliyun.com/ubuntu|g" \
               -e "s|archive.ubuntu.com/ubuntu|mirrors.aliyun.com/ubuntu|g" \
               -e "s/noble/jammy/g" /etc/apt/sources.list 2>/dev/null || true
        apt-get update -qq >/dev/null 2>&1 || true
        apt-get install -y -qq "$pkg" >/dev/null 2>&1 || { echo "$pkg 自动安装失败, 请手动装后重跑"; exit 1; }
    }
done
AVAIL_GB=$(df --output=avail -BG "$HOME" | tail -1 | tr -dc '0-9')
echo "磁盘可用: ${AVAIL_GB}GB (需约 30GB: 模型8.5 + venv/pip ~8 + node_modules ~1 + 系统)"

# ---------- 1. venv-app: backend + OCR (CPU paddle) ----------
log "venv-app (backend + OCR CPU paddle)"
if [ ! -f "$VENV_APP/bin/activate" ]; then
    python3 -m venv "$VENV_APP"
    "$VENV_APP/bin/pip" install -q --upgrade pip
    grep -vE '^\s*(--extra-index-url|paddlepaddle-gpu)' "$BACKEND/requirements.txt" > /tmp/req-dtk.txt
    "$VENV_APP/bin/pip" install paddlepaddle==3.2.2 -r /tmp/req-dtk.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
fi
"$VENV_APP/bin/python" -c "import paddle; print('paddle', paddle.__version__)" 2>/dev/null || echo "警告: paddle 导入失败"

# ---------- 2. venv-nl: NER/视觉服务 venv（--system-site-packages 复用 DTK torch）----------
# 注意: 绝不能在此 venv 里 pip install torch（会覆盖为 CUDA 版）。
# transformers 等只装 CPU 侧依赖，GPU 计算走系统 DTK torch。
log "venv-nl (transformers 服务，复用系统 DTK torch)"
if [ ! -f "$VENV_NL/bin/activate" ]; then
    python3 -m venv --system-site-packages "$VENV_NL"
    "$VENV_NL/bin/pip" install -q --upgrade pip
    "$VENV_NL/bin/pip" install "transformers==4.57.1" fastapi "uvicorn[standard]" pydantic httpx accelerate safetensors pillow \
        -i https://pypi.tuna.tsinghua.edu.cn/simple
fi
# LocateAnything 附加依赖装到 --target 目录（其版本锁不污染 venv），独立判存保证可修复重装
# 坑(实测两连): ①不带 --no-deps → peft 拖下 torch 2.14 + CUDA13 全家桶 ~2G(装完即删的纯浪费);
# ②对整份 requirements 用 --no-deps → 最新版 pydantic/fastapi 本体进 target, 其编译后端
# (pydantic-core 等)还在 venv → 版本对撞 SystemError。正解: venv-nl(--system-site-packages)
# 已含 transformers 4.57.1(正是 LA 钉的版本)/accelerate/safetensors/pillow/fastapi/pydantic/httpx,
# target 只补 venv 真没有的 4 个本体包(全部无编译后端耦合, 可安全 --no-deps):
if [ ! -d "$LA_DEPS/peft" ]; then
    "$VENV_NL/bin/pip" install --target "$LA_DEPS" --no-deps \
        "peft>=0.13.0" "opencv-python-headless==4.11.0.86" "decord==0.6.0" "lmdb==1.7.5" \
        -i https://pypi.tuna.tsinghua.edu.cn/simple
    # 坑(实测): LA 模型代码(trust_remote_code)硬依赖 torchvision, DTK 镜像只有 torch。
    # PyPI 的 torchvision 是 CUDA 构建且会拖 nvidia 全家桶 → 必须绕开。
    # 首选: 光合社区官方 DTK 构建(sourcefind z-file, 与镜像 torch 2.9.0+das.opt1.dtk2604
    # 同源同 ABI; 直链规则 /file/<CategoryID>/<path>, 文件名 + 须编码 %2B; 实测换装后 LA
    # 78s 重启即用)。兜底: aliyun CPU 版 0.24.0(实测对 DTK torch ABI 兼容, LA 仅用图像
    # 预处理面 TF.resize/InterpolationMode, io.read_video 仅视频输入才走)
    "$VENV_NL/bin/pip" install -q --target "$LA_DEPS" --no-deps \
        "https://download.sourcefind.cn:65024/file/4/vision/DAS1.8/torchvision-0.24.0%2Bdas.opt1.dtk2604.torch290-cp311-cp311-manylinux_2_28_x86_64.whl" \
        || "$VENV_NL/bin/pip" install -q --target "$LA_DEPS" --no-deps "torchvision==0.24.0" \
        -f https://mirrors.aliyun.com/pytorch-wheels/cpu/
fi
# 坑(L20 实测③): pip --target 会把 CUDA 版 torch/numpy 一并装进 target，PYTHONPATH 优先
# 于系统 DTK torch → 混载崩溃。target 内绝不能有 torch/numpy，统一用系统的
rm -rf "$LA_DEPS"/torch* "$LA_DEPS"/numpy*
"$VENV_NL/bin/python" -c "import torch; print('venv-nl torch ->', torch.__version__, torch.cuda.is_available())"
PYTHONPATH="$LA_DEPS" "$VENV_NL/bin/python" -c \
    "import torch,torchvision,transformers,peft,accelerate,cv2,decord; from transformers import AutoProcessor; print('LA deps OK, torch', torch.__version__, '| torchvision', torchvision.__version__)"

# ---------- 3. 模型下载（全部容器内直下 hf-mirror，无需本地上传）----------
export HF_ENDPOINT=https://hf-mirror.com
log "HaS_Text_0209_0.6B (1.2GB)"
if [ ! -f "$HAS_MODEL_DIR/config.json" ]; then
    mkdir -p "$(dirname "$HAS_MODEL_DIR")"
    "$VENV_APP/bin/pip" install -q -U huggingface_hub
    "$VENV_APP/bin/hf" download xuanwulab/HaS_Text_0209_0.6B --local-dir "$HAS_MODEL_DIR" 2>&1 | tail -2
fi
[ -f "$HAS_MODEL_DIR/config.json" ] && echo "HaS OK" || { echo "HaS 模型下载失败"; exit 1; }

log "LocateAnything-3B (7.3GB, ModelScope nv-community 直下 = L20 实测渠道 ~19MB/s)"
if [ ! -f "$LA_MODEL_DIR/model.safetensors.index.json" ]; then
    mkdir -p "$LA_MODEL_DIR"
    "$VENV_APP/bin/pip" install -q modelscope
    "$VENV_APP/bin/modelscope" download --model nv-community/LocateAnything-3B \
        --local_dir "$LA_MODEL_DIR" 2>&1 | tail -2
fi
[ -f "$LA_MODEL_DIR/model.safetensors.index.json" ] && echo "LocateAnything OK" \
  || { echo "LocateAnything 下载失败。兜底: LOCATE_ANYTHING_MODEL=$LA_MODEL_DIR $VENV_NL/bin/python $BACKEND/scripts/download_locateanything_hf.py (需 HF 可达)"; exit 1; }

# ---------- 4. .env ----------
log "生成 .env"
# 判存看关键键而非文件存在: 本地开发的 .env 可能被代码包带上(只有基础键, 缺 VENV_DIR 等)
if [ ! -f "$UPSTREAM/.env" ] || ! grep -q "^VENV_DIR=" "$UPSTREAM/.env"; then
    JWT=$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p | tr -d '\n')
    cat > "$UPSTREAM/.env" <<EOF
DEBUG=false
AUTH_ENABLED=true
JWT_SECRET_KEY=$JWT
JOB_CONCURRENCY=2
OCR_REQUIRE_GPU=false

# --- 源码方式服务地址（本机回环） ---
OCR_BASE_URL=http://127.0.0.1:8082
HAS_BASE_URL=http://127.0.0.1:8080/v1
HAS_TEXT_MODEL_NAME=HaS_Text_0209_0.6B
VISUAL_FEATURES_BASE_URL=http://127.0.0.1:8090
CORS_ORIGINS=["http://localhost:3000"]

# --- 路径 ---
VENV_DIR=$VENV_APP
HAS_TEXT_HF_MODEL_PATH=$HAS_MODEL_DIR
EOF
    echo ".env 已生成"
else
    echo ".env 已存在，跳过"
fi
# 坑(实测): 应用 Settings 读的是 backend/.env (BACKEND_DIR=parents[2]=$UP/backend),
# 不是仓库根 .env! 读不到则 VISUAL_FEATURES_BASE_URL 回落默认 9090(实际 8090) →
# LA 判 offline。软链对齐(L20 当时靠 tmux 服务器恰好继承 .env export 才侥幸通过)
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
if [ ! -d "$FRONTEND/dist" ]; then
    (cd "$FRONTEND" && npm ci --registry=https://registry.npmmirror.com && npm run build)
fi
[ -d "$FRONTEND/dist" ] && echo "前端构建产物 OK"

log "setup 完成。运行 ./start_dtk.sh 启动全部服务"
