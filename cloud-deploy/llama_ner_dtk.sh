#!/usr/bin/env bash
# Issue #23 轴B 主路线：llama.cpp HIP (DTK/DCU) 编译 + llama-server NER 服务。
# E1 实验脚本：编译未在 DTK 26.04 验证过（Issue #23 最大风险项），
# 失败时按设计退守 ner_transformers_server.py 攒批路线。
#
# 用法（实例上）：
#   bash llama_ner_dtk.sh build                 # 编译 llama-server + 量化工具（~5-10 分钟）
#   bash llama_ner_dtk.sh quantize_has <HF目录> <GGUF输出> [Q8_0]   # HaS HF -> GGUF 量化
#   bash llama_ner_dtk.sh serve <GGUF路径>      # 启动 llama-server (8080, 4 slots)
#   bash llama_ner_dtk.sh bench <GGUF路径>      # 单流 + 并发 tok/s 快测
set -euo pipefail

LLAMA_DIR="${LLAMA_DIR:-$HOME/llama.cpp}"          # 源码与构建目录
LLAMA_TAG="${LLAMA_TAG:-b6100}"                     # pinned 版本（实验后按实测固定）
HIP_TARGETS="${HIP_TARGETS:-gfx906}"                # Vega20/MI50 系；K100_AI 按实际 arch 传参覆盖
NPROC="${NPROC:-$(nproc)}"

proxy_env() {
    # 实例代理（deploy_fast.sh 同款），无代理环境 export 空串即可
    echo "http_proxy=${http_proxy:-} https_proxy=${https_proxy:-}"
}

cmd_build() {
    echo "==> clone llama.cpp @ ${LLAMA_TAG}"
    if [ ! -d "$LLAMA_DIR/.git" ]; then
        eval "$(proxy_env)" git clone --depth 1 --branch "$LLAMA_TAG" \
            https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
    fi
    cd "$LLAMA_DIR"

    echo "==> cmake HIP (AMDGPU_TARGETS=${HIP_TARGETS})"
    # DTK 用 hipcc 替代 nvcc：GGML_HIP=ON 走 HIP 后端；gfx906 无 flash attn，
    # 走通用 kernel。若 cmake 找不到 hipcc，先 source /opt/dtk/env.sh（DTK 26.04 路径见部署记录）。
    cmake -B build -S . -DGGML_HIP=ON -DAMDGPU_TARGETS="$HIP_TARGETS" -DCMAKE_BUILD_TYPE=Release
    cmake --build build --config Release -j"$NPROC" --target llama-server llama-quantize llama-cli

    echo "==> 冒烟：单 prompt 生成"
    local smoke; smoke="$($LLAMA_DIR/build/bin/llama-cli -m /dev/null 2>&1 || true)"
    echo "$smoke" | head -2 || true
    echo "OK: $LLAMA_DIR/build/bin/llama-server"
}

cmd_quantize_has() {
    # HaS_Text HF 目录 -> GGUF。convert 需要 transformers+gguf（paddle venv 或专用 venv），
    # 0.6B 按 Issue #23 量化档位只用 Q8_0（Q4 对 0.6B 是悬崖）。
    local hf_dir="$1" out="$2" qtype="${3:-Q8_0}"
    local venv_py="${QUANT_VENV_PY:-$HOME/.venvs/paddle/bin/python}"
    [ -x "$venv_py" ] || venv_py="$(command -v python3)"
    echo "==> convert_hf_to_gguf ($venv_py)"
    "$venv_py" "$LLAMA_DIR/convert_hf_to_gguf.py" "$hf_dir" --outtype f16 --outfile "${out%.gguf}.f16.gguf"
    echo "==> quantize ${qtype}"
    "$LLAMA_DIR/build/bin/llama-quantize" "${out%.gguf}.f16.gguf" "$out" "$qtype"
    echo "OK: $out"
}

serve_common() {
    local gguf="$1" np="${2:-4}" ctx="${3:-8192}" port="${4:-8080}"
    [ -x "$LLAMA_DIR/build/bin/llama-server" ] || { echo "先运行: llama_ner_dtk.sh build" >&2; exit 1; }
    [ -f "$gguf" ] || { echo "GGUF 不存在: $gguf" >&2; exit 1; }
    # -np = parallel slots（continuous batching）；ctx 是全部 slot 共享的总预算，
    # 每个 slot ≈ ctx/np。HaS/Qwen 系模板内嵌于 GGUF，无需显式 --chat-template。
    exec "$LLAMA_DIR/build/bin/llama-server" \
        -m "$gguf" --host 0.0.0.0 --port "$port" \
        -np "$np" -c "$ctx" --temp 0.0 --top-p 0.6 \
        --metrics --slots
}

cmd_serve() { serve_common "$1"; }

cmd_bench() {
    # 快速验证：单流延迟 + 4 并发下的每流 tok/s（E3 性能矩阵的 smoke 版）
    local gguf="$1"
    [ -x "$LLAMA_DIR/build/bin/llama-server" ] || { echo "先运行: llama_ner_dtk.sh build" >&2; exit 1; }
    serve_common "$gguf" 4 8192 18099 &
    local server_pid=$!
    sleep 3
    local prompt='Recognize the following entity types in the text.
Specified types:["姓名","身份证号","电话"]
Return strict JSON only. Include only entity types that have matches in the text.
Never output empty arrays. Do not return requested types with no matches. Do not explain.
If nothing matches, return {}.
<text>张三，身份证号 110101199001011234，电话 13800000000。</text>'
    for i in 1 2 3 4; do
        curl -s http://127.0.0.1:18099/v1/chat/completions -H 'Content-Type: application/json' \
            -d "{\"messages\":[{\"role\":\"user\",\"content\":$(python3 -c "import json,sys;print(json.dumps(sys.argv[1]))" "$prompt")}],\"max_tokens\":256}" \
            | python3 -c "import json,sys; d=json.load(sys.stdin); u=d['usage']; print(f'stream$i: completion_tokens={u[\"completion_tokens\"]}')" &
    done
    wait
    kill $server_pid 2>/dev/null || true
}

case "${1:-}" in
    build) cmd_build ;;
    quantize_has) shift; cmd_quantize_has "$@" ;;
    serve) shift; cmd_serve "$@" ;;
    bench) shift; cmd_bench "$@" ;;
    *) grep '^#' "$0" | sed 's/^# \{0,1\}//g' | head -14; exit 1 ;;
esac
