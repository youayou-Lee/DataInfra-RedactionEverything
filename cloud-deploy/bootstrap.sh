#!/bin/bash
# RedactionEverything 开机引导脚本（幂等，可反复跑）
# 位置: /root/private_data/redaction-persist/bootstrap.sh （持久卷，换实例不丢）
# 用途: 任意新实例（镜像为本项目保存过的环境）开机后跑一遍 → 环境/软链/启动脚本全部就位
# 用法:
#   bash $P/bootstrap.sh                # 只做环境校验+修复（软链、run_backend.sh、验证）
#   bash $P/bootstrap.sh --services     # 修复后拉起 CPU 三件套（OCR/backend/frontend，无卡模式用）
#   有卡模式起全量5服务: cd /root/redaction/cloud-deploy && bash start_cloud.sh
set -euo pipefail
# 平台代理账密不入库: 运行前必须 export SCNET_PROXY_URL(平台控制台获取)
: "${SCNET_PROXY_URL:?请先 export SCNET_PROXY_URL='http://<user>:<pass>@<代理地址:端口>'}"
P=/root/private_data/redaction-persist
UP=/root/redaction/DataInfra-RedactionEverything
LOG=/root/redaction/cloud-deploy/logs
PROXY_URL="$SCNET_PROXY_URL"
step(){ echo; echo "==> $*"; }

[ -d "$P/dot-venvs" ] || { echo "致命: 持久卷 $P 不存在（未挂载或数据丢失）"; exit 1; }
[ -d "$UP/backend" ] || { echo "致命: 代码 $UP 缺失——镜像未保存环境。本地 cloud-deploy 重传: ./pack_upload.sh <host> 后解包"; exit 1; }

step "0/5 PATH 与代理写入 .bashrc（交互 shell 生效）"
grep -q "/opt/conda/bin" ~/.bashrc 2>/dev/null || echo 'export PATH=/opt/conda/bin:$PATH' >> ~/.bashrc
if ! grep -q "# 平台代理" ~/.bashrc 2>/dev/null; then
  cat >> ~/.bashrc <<EOF

# 平台代理
export http_proxy='$PROXY_URL'
export https_proxy='$PROXY_URL'
export ftp_proxy='$PROXY_URL'
EOF
  echo "已写入代理配置"
else
  echo "OK 已配置"
fi
export PATH=/opt/conda/bin:$PATH
export no_proxy=localhost,127.0.0.1,0.0.0.0

step "1/5 系统工具（新镜像可能缺 tmux/curl）"
MISSING=""
for t in tmux curl; do command -v $t >/dev/null || MISSING="$MISSING $t"; done
if [ -n "$MISSING" ]; then
  export http_proxy="$PROXY_URL" https_proxy="$PROXY_URL"
  sed -i -e "s|security.ubuntu.com/ubuntu|mirrors.aliyun.com/ubuntu|g" -e "s/noble/jammy/g" /etc/apt/sources.list 2>/dev/null || true
  apt-get update -qq >/dev/null 2>&1 || true
  apt-get install -y -qq $MISSING >/dev/null 2>&1 && echo "已安装:$MISSING" || echo "警告:$MISSING 安装失败，请手动装"
else
  echo "OK 齐全"
fi

step "2/5 软链自检自修（幂等，目标名错误自动纠正）"
link(){ # <持久卷目标> <容器内路径>
  local t=$1 l=$2
  mkdir -p "$(dirname "$l")"
  if [ -L "$l" ]; then
    if [ "$(readlink "$l")" = "$t" ]; then echo "OK  $l"; else ln -sfn "$t" "$l"; echo "修  $l -> $t"; fi
  elif [ -e "$l" ]; then echo "跳过 $l（实体目录存在，如需接管请手动处理后重跑）"
  else ln -s "$t" "$l"; echo "建  $l -> $t"; fi
}
link "$P/dot-venvs"              /root/.venvs
link "$P/backend-models"         "$UP/backend/models"
link "$P/dot-cache"              /root/.cache
link "$P/yoloe-service"          /root/redaction/yoloe-service
# 注意: node_modules 绝不能软链! Node 按 realpath 向上找 node_modules, 软链会断链
# (报 ERR_MODULE_NOT_FOUND), 保持实体目录, 缺了在 step 4 自愈重装
# .local 特例: 平台 Jupyter 开机会重建 share/jupyter，保留实体、只软链 node-v22
if [ -L /root/.local ]; then
  echo "OK  /root/.local 已是软链"
else
  mkdir -p /root/.local
  if [ "$(readlink /root/.local/node-v22 2>/dev/null)" = "$P/dot-local/node-v22" ]; then echo "OK  node-v22 软链"
  else ln -sfn "$P/dot-local/node-v22" /root/.local/node-v22; echo "建  /root/.local/node-v22"; fi
fi

step "3/5 run_backend.sh 重建（引号防剥离写法）"
cat > /root/run_backend.sh <<'RB'
#!/bin/bash
cd /root/redaction/DataInfra-RedactionEverything/backend
export no_proxy=localhost,127.0.0.1
exec env DEBUG=false AUTH_ENABLED=true JOB_CONCURRENCY=2 OCR_REQUIRE_GPU=false CORS_ORIGINS='["http://localhost:3000"]' /root/.venvs/app/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
RB
chmod +x /root/run_backend.sh && echo "OK /root/run_backend.sh"

step "4/5 环境验证"
/root/.venvs/app/bin/python --version
/root/.local/node-v22/bin/node --version
[ -f "$UP/backend/models/has/HaS_Text_0209_0.6B/config.json" ] && echo "HaS 模型 OK" || echo "警告: HaS 模型缺失"
[ -f "$UP/backend/models/locateanything/LocateAnything-3B-HF/config.json" ] && echo "LocateAnything 模型 OK" || echo "警告: LA 模型缺失"
if [ -x "$UP/frontend/node_modules/.bin/vite" ]; then
  echo "vite OK"
else
  echo "vite 缺失 → 自动 npm ci（npmmirror，约3分钟）"
  export http_proxy="$PROXY_URL" https_proxy="$PROXY_URL" PATH="/root/.local/node-v22/bin:$PATH"
  (cd "$UP/frontend" && /root/.local/node-v22/bin/npm ci --registry=https://registry.npmmirror.com >/dev/null 2>&1 \
    && echo "node_modules 重装完成" || echo "警告: npm ci 失败，手动跑: cd $UP/frontend && npm ci")
fi

step "5/5 服务"
if [ "${1:-}" = "--services" ]; then
  mkdir -p "$LOG"
  tmux kill-session -t redaction 2>/dev/null || true
  tmux new-session -d -s redaction -n init "sleep infinity"
  PR="export http_proxy='$PROXY_URL' https_proxy='$PROXY_URL' no_proxy=localhost,127.0.0.1,0.0.0.0;"
  tmux new-window -d -t redaction -n ocr "cd $UP/backend && $PR CUDA_VISIBLE_DEVICES= OCR_VL_ENABLED=0 OCR_REQUIRE_GPU=false OCR_STRUCTURE_ENABLED=1 OCR_STRUCTURE_PRIMARY=1 OCR_STRUCTURE_WARMUP=1 OCR_STRUCTURE_RELEASE_AFTER_REQUEST=0 OCR_MAX_IMAGE_SIDE=2048 OCR_MAX_NEW_TOKENS=2048 PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True PYTHONPATH=$UP/backend /root/.venvs/app/bin/python $UP/backend/scripts/ocr_server.py 2>&1 | tee -a $LOG/ocr.log"
  tmux new-window -d -t redaction -n backend "bash /root/run_backend.sh 2>&1 | tee -a $LOG/backend.log"
  tmux new-window -d -t redaction -n frontend "export PATH=/root/.local/node-v22/bin:\$PATH no_proxy=localhost,127.0.0.1; cd $UP/frontend && npm run preview -- --host 0.0.0.0 --port 3000 --strictPort 2>&1 | tee -a $LOG/frontend.log"
  echo "已拉起 OCR/backend/frontend，等待 15s 后自检..."
  sleep 15
  echo "ocr(8082):      $(curl -s -o /dev/null -m 3 -w '%{http_code}' http://127.0.0.1:8082/health)"
  echo "backend(8000):  $(curl -s -o /dev/null -m 3 -w '%{http_code}' http://127.0.0.1:8000/health)"
  echo "frontend(3000): $(curl -s -o /dev/null -m 3 -w '%{http_code}' http://127.0.0.1:3000/)"
  echo "（无卡模式 NER/视觉服务不启动，属正常；有卡后跑 start_cloud.sh）"
else
  echo "环境就绪。启动服务:"
  echo "  无卡模式: bash $0 --services   （OCR/backend/frontend）"
  echo "  有卡模式: cd /root/redaction/cloud-deploy && bash start_cloud.sh   （全部5服务）"
fi
