"""合成 PDF 生成器（Issue #37 评测集）：语料行 → PDF 文件。

carrier：
  - text    文本层 PDF（insert_text，get_text 可提取）
  - scanned 扫描型（排版→整页 pixmap 渲染→插图，无文本层；迁自 perf-work/gen_test_pdf.py 模式）
  - hybrid  前半页文本层 + 后半页扫描图

确定性（内容级，设计文档 D8）：已固定元数据与 trailer /ID（含 rfind 防内容流伪 /ID
误替换）；修复后仍观察到一次字节级偶发漂移（机制未定位），故 PDF 一律以内容摘要
（页数+文本层+图像字节）作为可复现承诺，单测锁定——见 backend/tests。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_text  # noqa: E402

PAGE_W, PAGE_H = 595, 842
LINE_STEP = 20
FONT = "china-s"
DPI = 150
_FIXED_META = {
    "title": "synthetic eval document", "author": "eval-gen", "subject": "issue-37 benchmark",
    "creator": "gen_pdf.py", "producer": "PyMuPDF",
}
_FIXED_PDF_ID = b"/ID [<4f42373030316b73><4f42373030316b73>]"  # trailer /ID 固定（PyMuPDF 每次随机生成）
_PDF_ID_RE = re.compile(rb"/ID\s*\[<?[0-9A-Fa-f\s]+>?\s*<?[0-9A-Fa-f\s]+>?\]")


def _insert_lines(page: fitz.Page, lines: list[str], title: str, page_no: int, total: int) -> None:
    y = 72
    page.insert_text((72, y), f"{title}（第 {page_no + 1} 页 / 共 {total} 页）", fontsize=16, fontname=FONT)
    y += 36
    for line in lines:
        for seg_start in range(0, len(line), 38):
            page.insert_text((60, y), line[seg_start:seg_start + 38], fontsize=10.5, fontname=FONT)
            y += LINE_STEP
        y += 10
        if y > 760:
            break


def _rasterize_page(lines: list[str], title: str, page_no: int, total: int) -> fitz.Pixmap:
    """排版到临时单页文档并渲染为位图（gen_test_pdf.py 模式）。"""
    tmp = fitz.open()
    page = tmp.new_page(width=PAGE_W, height=PAGE_H)
    _insert_lines(page, lines, title, page_no, total)
    pix = page.get_pixmap(dpi=DPI)
    tmp.close()
    return pix


def build_pdf(out_path: Path, *, pages: int, carrier: str, doc_type: str = "contract",
              density: str = "mid", edge: bool = False) -> list[dict]:
    """生成 PDF 并返回 GT pages（[{page, entities}]，页号从 0 起）。

    edge=True 时：第 0 页空白、第 1 页纯表格、其余正常页（carrier 仍按指定执行）。
    """
    if carrier not in ("text", "scanned", "hybrid"):
        raise ValueError(f"carrier 非法: {carrier}")
    title = gen_text.DOC_TITLES[doc_type]
    doc = fitz.open()
    gt_pages: list[dict] = []
    split = pages // 2  # hybrid：前 split 页文本层
    for i in range(pages):
        blank = edge and i == 0
        table_only = edge and i == 1
        data = gen_text.build_page(i, doc_type=doc_type, density=density, blank=blank, table_only=table_only)
        gt_pages.append({"page": i, "entities": data["entities"]})
        if carrier == "text" or (carrier == "hybrid" and i < split):
            page = doc.new_page(width=PAGE_W, height=PAGE_H)
            if data["lines"]:
                _insert_lines(page, data["lines"], title, i, pages)
        else:
            if data["lines"]:
                pix = _rasterize_page(data["lines"], title, i, pages)
            else:  # 空白扫描页：渲染一张无内容位图，保持载体形态一致
                tmp = fitz.open()
                tmp.new_page(width=PAGE_W, height=PAGE_H)
                pix = tmp[0].get_pixmap(dpi=DPI)
                tmp.close()
            page = doc.new_page(width=PAGE_W, height=PAGE_H)
            page.insert_image(page.rect, pixmap=pix)
    doc.set_metadata(_FIXED_META)
    data = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    # 只替换 trailer 的 /ID（恒在文件尾部）。从头 sub 会偶发匹配到 deflate 内容流里的
    # 伪 /ID 字节序列，放过真 trailer /ID → 字节不确定（全量测试偶发失败根源）。
    id_idx = data.rfind(b"/ID")
    if id_idx != -1:
        data = data[:id_idx] + _PDF_ID_RE.sub(_FIXED_PDF_ID, data[id_idx:], count=1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    return gt_pages
