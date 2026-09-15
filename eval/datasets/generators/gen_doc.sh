#!/usr/bin/env bash
# Issue #46 格式矩阵：docx → doc（LibreOffice headless 一次性转换）。
# 用法：gen_doc.sh <src.docx> <out_dir>   产物：<out_dir>/<同名>.doc
# .doc 为二进制 OLE 复合文档，转换带时间戳，不做逐字节确定性承诺（单测只验魔数）。
set -euo pipefail

src="${1:?用法: gen_doc.sh <src.docx> <out_dir>}"
out_dir="${2:?用法: gen_doc.sh <src.docx> <out_dir>}"

converter=""
for c in /usr/bin/soffice /usr/bin/libreoffice /snap/bin/libreoffice soffice libreoffice; do
  if command -v "$c" >/dev/null 2>&1; then
    converter="$c"
    break
  fi
done
if [ -z "$converter" ]; then
  echo "错误：未找到 LibreOffice（soffice/libreoffice），无法生成 .doc 样张" >&2
  exit 1
fi

mkdir -p "$out_dir"
"$converter" --headless --convert-to doc --outdir "$out_dir" "$src"
echo "OK: $out_dir/$(basename "${src%.*}").doc"
