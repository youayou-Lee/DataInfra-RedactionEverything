#!/usr/bin/env bash
# NER 调优一键评测 — DCU 实例入口（source DTK 环境 + 本机默认路径，全部可用环境变量覆盖）。
#
#   bash bench_ner_onekey.sh                       # 全矩阵 base,axisA,axisB,axisAB
#   bash bench_ner_onekey.sh --matrix base,axisAB  # 速查模式
#   bash bench_ner_onekey.sh --reference-base http://127.0.0.1:8081/v1 \
#        --reference-model HaS_Text_0209_0.6B      # 附 vLLM 参考行
#
# 输出目录默认 /root/issue23-eval/run-<时间戳>/SUMMARY.md
# 不用 -u：/opt/dtk/env.sh 会引用未定义的 LD_LIBRARY_PATH（nounset 下报错）
set -eo pipefail

REPO=${REPO:-/root/redaction/DataInfra-RedactionEverything}
MODEL=${MODEL:-$REPO/backend/models/has/HaS_Text_0209_0.6B}
SERVER=${SERVER:-/root/redaction/cloud-deploy/ner_transformers_server.py}
PY=${PY:-/root/.venvs/nl/bin/python}
OUT=${OUT:-/root/issue23-eval/run-$(date +%Y%m%d-%H%M%S)}

export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
if [ -f /opt/dtk/env.sh ]; then source /opt/dtk/env.sh; fi
export MIOPEN_USER_CACHE_PATH=${MIOPEN_USER_CACHE_PATH:-/root/private_data/redaction-persist/miopen-cache}
export HF_HUB_OFFLINE=1

exec "$PY" "$REPO/backend/scripts/eval/bench_ner_onekey.py" \
  --model "$MODEL" \
  --server-script "$SERVER" \
  --python "$PY" \
  --miopen-cache "$MIOPEN_USER_CACHE_PATH" \
  --out "$OUT" \
  "$@"
