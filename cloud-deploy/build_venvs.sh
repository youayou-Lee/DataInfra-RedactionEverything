#!/usr/bin/env bash
# venv 从零重建 —— 无持久卷依赖的完整路径（生产镜像上约 15–25 分钟）
# 前提: 生产镜像（BW1000+DTK25.04.2, py3.10）。venv 只绑 Python 小版本, 不绑镜像。
# 全部坑位实测-backed, 详见 docs/dcu/海光DCU适配总览.md §9 坑清单:
#   ① nl 必须 --system-site-packages（torch 用镜像自带 DAS 构建, pip 装不了）
#   ② freeze 里的 torch/vllm/vision 带 10.16.4.1 内网直链, 运行实例不可达, 必须过滤
#   ③ paddle-dcu 依赖顺序: safetensors(bcebos) 先行 → DCU 源 → paddleocr
#   ④ pip/apt 全程走代理
#   ⑤ UTC 垫片必须写系统级 sitecustomize（venv 内的被 /usr/lib/python3.10 遮蔽）
# 用法: SCNET_PROXY_URL=http://... bash build_venvs.sh
set -uo pipefail
: "${SCNET_PROXY_URL:?先 export SCNET_PROXY_URL=<平台代理>}"
V=${VENV_ROOT:-/root/.venvs}
REQ="$(cd "$(dirname "$0")" && pwd)/venvs"
PX=(--proxy "$SCNET_PROXY_URL")
FILTER_DAS='^(torch|torchvision|torchaudio|torchdata|vllm|triton)([=<>@ ]|$)'
FILTER_DCU='^(paddlepaddle-dcu)([=<>@ ]|$)'
mkdir -p "$V"

echo "==> 0/5 系统依赖 + UTC 垫片"
http_proxy="$SCNET_PROXY_URL" https_proxy="$SCNET_PROXY_URL" \
  apt-get update -q && http_proxy="$SCNET_PROXY_URL" https_proxy="$SCNET_PROXY_URL" \
  apt-get install -y -q python3.10-venv tmux
python3 - <<'PYEOF'
import pathlib
f = pathlib.Path("/usr/lib/python3.10/sitecustomize.py")
if f.exists() and "DT_UTC_SHIM" in f.read_text():
    print("UTC shim 已存在"); raise SystemExit
with f.open("a") as fh:
    fh.write('\n# DT_UTC_SHIM: py3.11 datetime.UTC backport\n'
             'import datetime as _dt_shim\n'
             'if not hasattr(_dt_shim, "UTC"):\n'
             '    _dt_shim.UTC = _dt_shim.timezone.utc\n')
print("UTC shim 已注入(系统级)")
PYEOF

echo "==> 1/5 nl（NER, system-site-packages）"
python3.10 -m venv --system-site-packages "$V/nl"
grep -vE "$FILTER_DAS" "$REQ/requirements-nl.txt" > /tmp/req-nl.txt
"$V/nl/bin/pip" install -q "${PX[@]}" -r /tmp/req-nl.txt

echo "==> 2/5 app（backend）"
python3.10 -m venv "$V/app"
grep -vE "$FILTER_DAS" "$REQ/requirements-app.txt" > /tmp/req-app.txt
"$V/app/bin/pip" install -q "${PX[@]}" -r /tmp/req-app.txt

echo "==> 3/5 paddle-25041（GPU OCR）"
python3.10 -m venv "$V/paddle-25041"
"$V/paddle-25041/bin/pip" install -q "${PX[@]}" \
  https://paddle-whl.bj.bcebos.com/nightly/cu126/safetensors/safetensors-0.6.2.dev0-cp38-abi3-linux_x86_64.whl
grep -vE "$FILTER_DAS|$FILTER_DCU" "$REQ/requirements-paddle-25041.txt" > /tmp/req-p1.txt
"$V/paddle-25041/bin/pip" install -q "${PX[@]}" -r /tmp/req-p1.txt
"$V/paddle-25041/bin/pip" install -q "${PX[@]}" paddlepaddle-dcu==3.2.1 \
  -i https://www.paddlepaddle.org.cn/packages/stable/dcu/
"$V/paddle-25041/bin/pip" install -q "${PX[@]}" "paddleocr[doc-parser]"

echo "==> 4/5 paddle-cpu（兜底, PyPI 纯 CPU wheel）"
python3.10 -m venv "$V/paddle-cpu"
grep -vE "$FILTER_DAS" "$REQ/requirements-paddle-cpu.txt" > /tmp/req-p2.txt
"$V/paddle-cpu/bin/pip" install -q "${PX[@]}" -r /tmp/req-p2.txt

echo "==> 5/5 完成。验证: bash verify_dcu_adaptation.sh（L0 会逐个检查 venv）"
