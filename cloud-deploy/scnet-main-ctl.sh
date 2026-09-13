#!/usr/bin/env bash
# scnet-main 实例全栈一键运维：start / stop / restart / status / logs
#
# 用法：
#   bash cloud-deploy/scnet-main-ctl.sh start    [svc…]   # 冷启动全部（约 13 分钟，LA 加载是瓶颈）
#   bash cloud-deploy/scnet-main-ctl.sh stop     [svc…]   # 停服务（不给 svc = 全停，连 tmux 会话一起清）
#   bash cloud-deploy/scnet-main-ctl.sh restart  [svc…]   # 重启；单服务秒级（backend ~20s），全量=冷启动
#   bash cloud-deploy/scnet-main-ctl.sh status            # 各服务健康 + tmux 窗口 + GPU 探针
#   bash cloud-deploy/scnet-main-ctl.sh logs     <svc>    # 尾随该服务日志（tmux 窗口内 tee 落盘）
#
# svc ∈ ner | locateanything | backend | frontend | ocr
# 服务拓扑/venv/坑位详见 docs/scnet-main-环境清单.md（实例 /root/ENVIRONMENT.md 同步存放）。
#
# 安全约定：本脚本入库存放，**不含任何凭据**。平台代理等实例侧私有配置放
# /root/redaction/cloud-deploy/instance.env（可选，键：PROXY_URL，完整 http://user:pass@host:port），
# 缺省时按离线模式启动（模型均本地、HF_HUB_OFFLINE=1，常规起服务不需要代理）。
set -uo pipefail

# ── 实例布局（scnet-main 默认值，可在 instance.env 里覆盖） ──
UP=${UP:-/root/redaction/DataInfra-RedactionEverything}
BACKEND=$UP/backend
FRONTEND=$UP/frontend
DEPLOY=${DEPLOY:-/root/redaction/cloud-deploy}
LOG_DIR=$DEPLOY/logs; mkdir -p "$LOG_DIR"
SESSION=${SESSION:-redaction}
P=${P:-/root/private_data/redaction-persist}
PY_APP=${PY_APP:-/root/.venvs/app/bin/python}
PY_NL=${PY_NL:-/root/.venvs/nl/bin/python}
PY_OCR=${PY_OCR:-$P/dot-venvs/paddle-25041/bin/python}
NODE_BIN_DIR=${NODE_BIN_DIR:-/root/.local/node-v22/bin}
DTK_ENV=${DTK_ENV:-/opt/dtk/env.sh}

# optional instance env (credentials etc.)
[ -f "$DEPLOY/instance.env" ] && . "$DEPLOY/instance.env"

PROXY_EXPORT=""
if [ -n "${PROXY_URL:-}" ]; then
  export http_proxy="$PROXY_URL" https_proxy="$PROXY_URL"
  export no_proxy='localhost,127.0.0.1,0.0.0.0' NO_PROXY='localhost,127.0.0.1,0.0.0.0'
  PROXY_EXPORT="export http_proxy='$PROXY_URL' https_proxy='$PROXY_URL' no_proxy=localhost,127.0.0.1,0.0.0.0 NO_PROXY=localhost,127.0.0.1,0.0.0.0;"
fi
# /opt/dtk/env.sh 的 LD_LIBRARY_PATH 漏 /opt/dtk/hip/lib（libgalaxyhip.so.5 在此），必须补
DTK_EXPORT="set +u; source '$DTK_ENV'; set -u; export LD_LIBRARY_PATH=/opt/dtk/hip/lib:\$LD_LIBRARY_PATH; export MIOPEN_USER_CACHE_PATH=$P/miopen-cache;"

SERVICES=(ner locateanything backend frontend ocr)

step() { echo; echo "==> $*"; }
die() { echo "错误: $*" >&2; exit 1; }

valid_svc() { local s
  for s in "${SERVICES[@]}"; do [ "$s" = "$1" ] && return 0; done
  return 1
}

validate_targets() { local s
  for s in "$@"; do valid_svc "$s" || die "未知服务: $s（可选: ${SERVICES[*]}）"; done
}

wait_port_free() { local url=$1 name=$2 w=0
  # kill-window 只是 SIGHUP，大模型进程退出可能滞后；端口未释放就 spawn 会 bind 失败，
  # 而 wait_health 探到旧进程会误报 OK
  while curl -sf -m 2 "$url" >/dev/null 2>&1; do
    sleep 1; w=$((w+1))
    [ $w -ge 30 ] && { echo "  警告: $name 端口 30s 未释放，继续启动（若失败查日志）"; return 1; }
  done
  return 0
}

wait_health() { local url=$1 name=$2 timeout=${3:-600} w=0
  printf "    等 %s " "$name"
  until curl -sf -m 3 "$url" >/dev/null 2>&1; do sleep 3; w=$((w+3)); printf "."
    [ $w -ge $timeout ] && { echo; echo "超时: $name（看日志: $LOG_DIR/<svc>.log）"; return 1; }; done
  echo " OK (${w}s)"
}

svc_health() { case $1 in
  ner)            echo http://127.0.0.1:8080/health ;;
  locateanything) echo http://127.0.0.1:8090/health ;;
  backend)        echo http://127.0.0.1:8000/health ;;
  frontend)       echo http://127.0.0.1:10800/ ;;
  ocr)            echo http://127.0.0.1:8082/health ;;
esac; }

svc_log() { case $1 in
  ner)            echo "$LOG_DIR/ner.log" ;;
  locateanything) echo "$LOG_DIR/locateanything.log" ;;
  backend)        echo "$LOG_DIR/backend.log" ;;
  frontend)       echo "$LOG_DIR/frontend.log" ;;
  ocr)            echo "$LOG_DIR/ocr-gpu.log" ;;
esac; }

svc_cmd() { case $1 in
  ner) echo "$PROXY_EXPORT $DTK_EXPORT cd '$UP' && HF_HUB_OFFLINE=1 CUDA_VISIBLE_DEVICES=0 \
NER_MODEL_DIR='$BACKEND/models/has/HaS_Text_0209_0.6B' NER_DEVICE=cuda:0 \
'$PY_NL' '$DEPLOY/ner_transformers_server.py' \
--model '$BACKEND/models/has/HaS_Text_0209_0.6B' --host 0.0.0.0 --port 8080 --device cuda:0" ;;
  locateanything) echo "$PROXY_EXPORT $DTK_EXPORT cd '$UP' && CUDA_VISIBLE_DEVICES=0 HF_HUB_OFFLINE=1 \
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
LOCATE_ANYTHING_MODEL_NAME=LocateAnything-3B \
LOCATE_ANYTHING_MAX_IMAGE_SIDE=1280 LOCATE_ANYTHING_MAX_NEW_TOKENS=8192 \
LOCATE_ANYTHING_GENERATION_MODE=hybrid LOCATE_ANYTHING_FAST_FIRST=1 \
LOCATE_ANYTHING_TEMPERATURE=0.7 LOCATE_ANYTHING_VLLM_URL= \
LOCATE_ANYTHING_VLLM_MODEL=locate_qwen2_model \
PYTHONPATH='$BACKEND/scripts:$BACKEND' \
'$PY_NL' '$BACKEND/scripts/locate_anything_server.py' \
--model '$BACKEND/models/locateanything/LocateAnything-3B-HF' \
--backend hf --host 0.0.0.0 --port 8090 --dtype bfloat16" ;;
  backend) echo "$PROXY_EXPORT cd '$BACKEND' && \
DEBUG=false AUTH_ENABLED=true JOB_CONCURRENCY=2 \
VISION_DUAL_PIPELINE_PARALLEL=true \
HAS_NER_GLOBAL_MAX_INFLIGHT=2 HAS_NER_MAX_PARALLEL_REQUESTS=2 \
BATCH_RECOGNITION_PAGE_CONCURRENCY=1 BATCH_RECOGNITION_PAGE_TIMEOUT=300 \
HAS_NER_CACHE_TTL_SEC=0 \
CORS_ORIGINS='[\"http://localhost:3000\"]' \
'$PY_APP' -m uvicorn app.main:app --host 0.0.0.0 --port 8000" ;;
  frontend) echo "export PATH=$NODE_BIN_DIR:\$PATH no_proxy=localhost,127.0.0.1; \
cd '$FRONTEND' && npm run preview -- --host 0.0.0.0 --port 10800 --strictPort" ;;
  ocr) echo "$PROXY_EXPORT $DTK_EXPORT cd '$BACKEND' && \
HIP_VISIBLE_DEVICES=0 OCR_VL_ENABLED=0 OCR_REQUIRE_GPU=true OCR_DEVICE=dcu:0 \
OCR_STRUCTURE_ENABLED=1 OCR_STRUCTURE_PRIMARY=1 OCR_STRUCTURE_WARMUP=1 \
OCR_STRUCTURE_RELEASE_AFTER_REQUEST=0 OCR_MAX_IMAGE_SIDE=2048 OCR_MAX_NEW_TOKENS=2048 \
PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True PADDLE_PDX_DISABLE_DEV_MODEL_WL=true \
PADDLE_PDX_CACHE_HOME=$P/paddlex-cache \
PYTHONPATH='$BACKEND' \
'$PY_OCR' '$BACKEND/scripts/ocr_server.py'" ;;
esac; }

spawn_svc() { local win=$1
  tmux kill-window -t "$SESSION:$win" 2>/dev/null || true
  tmux new-window -d -t "$SESSION" -n "$win" "$(svc_cmd "$win") 2>&1 | tee -a $(svc_log "$win")"
}

ensure_session() { tmux has-session -t $SESSION 2>/dev/null || tmux new-session -d -s $SESSION -n init "sleep infinity"; }

ensure_backend_dotenv() {
  # 只在 .env 整体缺失时自举生成（避免重生成 JWT 令已登录会话静默失效）
  if [ ! -f "$UP/.env" ]; then
    step "生成 $UP/.env（backend/.env 软链指向它）"
    JWT=$(openssl rand -hex 32)
    cat > "$UP/.env" <<EOF
DEBUG=false
AUTH_ENABLED=true
JWT_SECRET_KEY=$JWT
JOB_CONCURRENCY=2
OCR_REQUIRE_GPU=false
OCR_BASE_URL=http://127.0.0.1:8082
HAS_BASE_URL=http://127.0.0.1:8080/v1
HAS_TEXT_MODEL_NAME=HaS_Text_0209_0.6B
VISUAL_FEATURES_BASE_URL=http://127.0.0.1:8090
CORS_ORIGINS=["http://localhost:3000"]
VENV_DIR=/root/.venvs/app
HAS_TEXT_HF_MODEL_PATH=$BACKEND/models/has/HaS_Text_0209_0.6B
EOF
  fi
  ln -sfn "$UP/.env" "$BACKEND/.env"
}

start_one() { local win=$1
  step "启动 $win"
  [ "$win" = backend ] && ensure_backend_dotenv
  [ "$win" = frontend ] && [ ! -f "$FRONTEND/dist/index.html" ] && build_frontend
  spawn_svc "$win"
  local timeout=600; [ "$win" = locateanything ] && timeout=900; [ "$win" = ocr ] && timeout=900
  wait_health "$(svc_health "$win")" "$win" "$timeout" || return 1
}

build_frontend() {
  step "构建前端 dist（缺失时才建）"
  (cd "$FRONTEND" && PATH=$NODE_BIN_DIR:$PATH no_proxy=localhost,127.0.0.1 \
    npm run build > "$LOG_DIR/frontend_build.log" 2>&1) || { echo "构建失败:"; tail -10 "$LOG_DIR/frontend_build.log"; return 1; }
}

cmd_start() {
  validate_targets "$@"
  step "预检"
  for f in "$PY_APP" "$PY_NL" "$PY_OCR" "$BACKEND/models/has/HaS_Text_0209_0.6B/config.json"; do
    [ -e "$f" ] || die "缺少 $f（先读 docs/scnet-main-环境清单.md 排障，别猜）"
  done
  ensure_session
  local targets=("$@"); [ ${#targets[@]} -eq 0 ] && targets=("${SERVICES[@]}")
  local failed=()
  for win in "${targets[@]}"; do
    start_one "$win" || failed+=("$win")
  done
  [ ${#failed[@]} -gt 0 ] && die "未就绪: ${failed[*]}"
  cmd_status
}

cmd_stop() {
  local targets=("$@")
  if [ ${#targets[@]} -eq 0 ]; then
    step "停止全部（含 tmux 会话 $SESSION）"
    tmux kill-session -t "$SESSION" 2>/dev/null || true
    echo "已全部停止"
    return
  fi
  for win in "${targets[@]}"; do
    step "停止 $win"
    tmux kill-window -t "$SESSION:$win" 2>/dev/null && echo "  已停" || echo "  （窗口不存在）"
  done
}

cmd_restart() {
  validate_targets "$@"
  local targets=("$@")
  if [ ${#targets[@]} -eq 0 ]; then
    cmd_stop; sleep 2; cmd_start
  else
    for win in "${targets[@]}"; do
      cmd_stop "$win"
    done
    for win in "${targets[@]}"; do wait_port_free "$(svc_health "$win")" "$win"; done
    local failed=()
    for win in "${targets[@]}"; do start_one "$win" || failed+=("$win"); done
    [ ${#failed[@]} -gt 0 ] && die "未就绪: ${failed[*]}"
    cmd_status
  fi
}

cmd_status() {
  step "tmux 窗口（$SESSION）"
  tmux list-windows -t "$SESSION" 2>/dev/null | sed 's/^/  /' || echo "  （会话不存在）"
  step "服务健康"
  for win in "${SERVICES[@]}"; do
    local url; url=$(svc_health "$win")
    if curl -sf -m 3 "$url" >/dev/null 2>&1; then
      echo "  $win: online"
    else
      echo "  $win: offline"
    fi
  done
  step "backend 服务总览"
  curl -s -m 5 http://127.0.0.1:8000/health/services | head -c 400; echo
  echo "  提示：前端页面「离线」徽章恢复需强刷（Ctrl+Shift+R）；GPU 探针用 hy_smi.py -i，不用 nvidia-smi"
}

cmd_logs() {
  local win=${1:-backend}
  local f; f=$(svc_log "$win")
  [ -f "$f" ] || die "日志不存在: $f"
  tail -f "$f"
}

case "${1:-}" in
  start)   shift; cmd_start "$@" ;;
  stop)    shift; cmd_stop "$@" ;;
  restart) shift; cmd_restart "$@" ;;
  status)  cmd_status ;;
  logs)    shift; cmd_logs "$@" ;;
  *) sed -n '2,15p' "$0"; exit 1 ;;
esac
