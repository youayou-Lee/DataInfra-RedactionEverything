#!/usr/bin/env bash
# paddle conv 判别器：DTK25.04.2 + 当前卡 上测 volume 里两个 DCU wheel venv
set -uo pipefail
set +u; source /opt/dtk/env.sh >/dev/null 2>&1; set -u
P=/root/private_data/redaction-persist

cat > /tmp/conv_probe.py <<'PYEOF'
import paddle
print("paddle:", paddle.__version__)
paddle.set_device("dcu:0")
x = paddle.randn([2, 3, 64, 64])
w = paddle.randn([16, 3, 3, 3])
out = paddle.nn.functional.conv2d(x, w)
print("conv2d PASS", tuple(out.shape))
# 顺便测 pool + matmul（OCR 管线常用算子）
y = paddle.nn.functional.max_pool2d(out, kernel_size=2)
print("maxpool PASS", tuple(y.shape))
PYEOF

for V in paddle-25041 paddle; do
  echo "===== venv: $V ====="
  PY=$P/dot-venvs/$V/bin/python
  [ -x "$PY" ] || { echo "venv 不存在"; continue; }
  timeout 150 "$PY" /tmp/conv_probe.py 2>&1 | grep -aE "paddle:|PASS|bad_alloc|Error|error|abort|Segmentation" | head -5
done
echo "PROBE_DONE"
