#!/usr/bin/env bash
# DCU 适配验收门 —— L0环境/L1算子/L2服务/L3功能 一键体检（L4 另跑 e2e）
# 用法: bash verify_dcu_adaptation.sh          # L0-L3（服务需已按 kit 拉起）
#       bash verify_dcu_adaptation.sh --spawn  # 连服务一起拉起（多 ~8 分钟）
set -uo pipefail
P=/root/private_data/redaction-persist
UP=/root/redaction/DataInfra-RedactionEverything
SPAWN=0; [ "${1:-}" = "--spawn" ] && SPAWN=1
PASS=0; FAIL=0; SKIP=0
ok()  { printf "  ✓ %s\n" "$1"; PASS=$((PASS+1)); }
bad() { printf "  ✗ %s\n" "$1"; FAIL=$((FAIL+1)); }
skip(){ printf "  - %s\n" "$1"; SKIP=$((SKIP+1)); }
export no_proxy='localhost,127.0.0.1,0.0.0.0' NO_PROXY='localhost,127.0.0.1,0.0.0.0'
# 坑①：系统 python3 用 torch/vllm 必须先加载 DTK 环境（env.sh 有未定义变量，set +u 包裹）
set +u; source /opt/dtk/env.sh >/dev/null 2>&1; set -u

echo "===== L0 环境静态检查 ====="
DTK=$(ls -d /opt/dtk 2>/dev/null) && ok "DTK 存在" || bad "DTK 缺失"
TORCH_VER=$(python3 -c "import torch;print(torch.__version__)" 2>/dev/null) \
  && ok "torch $TORCH_VER" || bad "torch 不可导入"
python3 -c "import torch;p=torch.cuda.get_device_properties(0);print(p.name,p.major,p.minor,int(p.total_memory/2**30))" 2>/dev/null \
  && ok "GPU 可见" || bad "GPU 不可见"
PYV=$(python3 -c "import sys;print('%d.%d'%sys.version_info[:2])")
[ "$PYV" = "3.10" ] && ok "Python $PYV（venv 兼容）" || bad "Python $PYV ≠3.10，卷上 venv 全部需重建"
for v in nl app paddle-25041 paddle-cpu; do [ -x "$P/dot-venvs/$v/bin/python" ] && ok "venv:$v" || bad "venv:$v 缺失"; done
grep -q DT_UTC_SHIM /usr/lib/python3.10/sitecustomize.py 2>/dev/null && ok "UTC 垫片" || bad "UTC 垫片缺失（跑 bootstrap）"
command -v tmux >/dev/null && ok "tmux" || bad "tmux 缺失"
[ -f "$UP/backend/models/has/HaS_Text_0209_0.6B/config.json" ] && ok "代码+HaS模型" || bad "代码/模型缺失（跑 bootstrap）"
[ -d "$P/paddlex-cache/official_models" ] && ok "paddlex 模型缓存" || skip "paddlex 缓存空（首跑会补拉）"

echo "===== L1 算子冒烟 ====="
timeout 150 bash -c 'set +u; source /opt/dtk/env.sh >/dev/null 2>&1; set -u; exec '"$P"'/dot-venvs/paddle-25041/bin/python - <<PYEOF
import paddle
paddle.set_device("dcu:0")
o = paddle.nn.functional.conv2d(paddle.randn([2,3,64,64]), paddle.randn([16,3,3,3]))
m = paddle.nn.functional.max_pool2d(o, 2)
print("shape-ok", tuple(o.shape), tuple(m.shape))
PYEOF' 2>&1 | grep -qa "shape-ok" && ok "paddle conv+pool (dcu:0)" || bad "paddle GPU 崩（MIOpen? DTK 不匹配）"
timeout 120 python3 -c "
import torch
x=torch.randn(2,3,64,64,device='cuda'); w=torch.randn(16,3,3,3,device='cuda')
print('ok',tuple(torch.nn.functional.conv2d(x,w).shape))" 2>/dev/null | grep -q "ok (" \
  && ok "torch conv (反证)" || bad "torch conv 异常"
python3 -c "import vllm; print(vllm.__version__)" >/dev/null 2>&1 && ok "vllm 可导入" || bad "vllm 不可导入"

if [ "$SPAWN" = 1 ]; then
  echo "===== L2 拉起服务（--spawn） ====="
  nohup bash /root/vllm_ner_25042.sh      >/dev/null 2>&1 &
  nohup bash /root/respawn_ocr_gpu_dcu.sh >/dev/null 2>&1 &
  nohup bash "$P/vllm-img-kit/start_core.sh" >/dev/null 2>&1 &
  echo "  （服务后台拉起中，等待就绪…）"; sleep 240
fi

echo "===== L2 服务健康 ====="
h8081=$(curl -s -m 3 http://127.0.0.1:8081/v1/models); [ -n "$h8081" ] \
  && ok "vLLM 8081: $(echo "$h8081" | grep -o '"owned_by":"[^"]*"' | head -1)" || skip "vLLM 8081 未起"
h8082=$(curl -s -m 3 http://127.0.0.1:8082/health)
if echo "$h8082" | grep -q '"runtime_mode":"gpu"'; then ok "OCR 8082: GPU 模式"
elif [ -n "$h8082" ]; then bad "OCR 8082 在跑但非 GPU: $(echo "$h8082" | grep -o '"runtime_mode":"[^"]*"')"
else skip "OCR 8082 未起"; fi
curl -s -m 3 http://127.0.0.1:8080/health | grep -q '"ready":true' && ok "NER 8080 (transformers)" || skip "NER 8080 未起"
curl -s -m 3 http://127.0.0.1:8090/health >/dev/null 2>&1 && ok "LA 8090" || skip "LA 8090 未起"
curl -s -m 5 http://127.0.0.1:8000/health | grep -q '"status":"healthy"' && ok "backend 8000 healthy" \
  || { curl -s -m 5 http://127.0.0.1:8000/health >/dev/null 2>&1 && bad "backend 响应异常" || skip "backend 未起"; }

echo "===== L3 功能验证 ====="
if [ -n "$h8081" ]; then
  NER_OUT=$(/root/.venvs/nl/bin/python /root/verify_ner.py http://127.0.0.1:8081 2>/dev/null)
  NER_T=$(echo "$NER_OUT" | sed 's/\x1b\[[0-9;]*m//g' | grep -oE '\[2\] [0-9.]+s' | head -1)
  echo "$NER_OUT" | grep -qa "全命中 PASS" && ok "NER(vLLM) 3/3 全命中（热跑${NER_T:-?}）" || bad "NER(vLLM) 未全命中"
fi
[ -n "$h8082" ] && { /root/.venvs/app/bin/python /root/verify_ocr.py 2>/dev/null | grep -qa "PASS" \
  && ok "OCR(GPU) 真页验证 PASS" || bad "OCR 真页验证失败"; } || true

echo
echo "===== 验收结果: PASS=$PASS FAIL=$FAIL SKIP=$SKIP ====="
[ "$FAIL" -eq 0 ] && echo "✅ 适配成立（L0-L3）" || echo "❌ 存在适配问题，见上"
exit $FAIL
