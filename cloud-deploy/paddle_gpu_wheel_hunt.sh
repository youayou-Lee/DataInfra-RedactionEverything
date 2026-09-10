#!/usr/bin/env bash
# GPU OCR wheel 猎测：DTK25.04.1+CC9.2 上轮询 paddlepaddle-dcu 版本，conv2d 为判别器
# 已死(勿重试): 3.2.1, 3.3.1。依次试: 3.1.1 → 3.0.0 → 3.2.2 → 3.3.0 → 3.2.0
set -uo pipefail
PX="--proxy ${SCNET_PROXY_URL:?export SCNET_PROXY_URL=平台代理}"
SRC="https://www.paddlepaddle.org.cn/packages/stable/dcu/"
VENV=/root/venv-gpu-hunt

python3 -m venv "$VENV" || { echo "venv 创建失败"; exit 1; }
PIP="$VENV/bin/pip"
$PIP install -q $PX https://paddle-whl.bj.bcebos.com/nightly/cu126/safetensors/safetensors-0.6.2.dev0-cp38-abi3-linux_x86_64.whl 2>&1 | tail -1

cat > /tmp/conv_probe.py <<'PYEOF'
import sys
import paddle
print("ver:", paddle.__version__)
paddle.set_device("dcu:0")
x = paddle.randn([2, 3, 64, 64])
w = paddle.randn([16, 3, 3, 3])
out = paddle.nn.functional.conv2d(x, w)
print("conv2d PASS", tuple(out.shape))
PYEOF

for VER in 3.1.1 3.0.0 3.2.2 3.3.0 3.2.0; do
  echo "===== paddlepaddle-dcu $VER ====="
  $PIP install -q $PX "paddlepaddle-dcu==$VER" -i "$SRC" 2>&1 | grep -iE "error|success" | head -2
  timeout 120 bash -c 'set +u; source /opt/dtk/env.sh >/dev/null 2>&1; set -u; '"$VENV"'/bin/python /tmp/conv_probe.py' 2>&1 \
    | grep -aE "ver:|PASS|bad_alloc|Error|error|abort" | head -4
done
echo "HUNT_DONE"
