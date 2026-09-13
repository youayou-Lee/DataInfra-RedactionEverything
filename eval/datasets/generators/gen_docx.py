"""合成 docx 生成器（Issue #37 评测集）：语料行 → docx 文件。

docx 无真实分页概念，GT 聚合为单条「第 1 页」（run_eval 对 docx 做文档级实体对齐）。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_text  # noqa: E402

_FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)  # 固定 core.xml 时间戳，保证产物确定性


def build_docx(out_path: Path, *, pages: int, doc_type: str = "contract",
               density: str = "mid") -> list[dict]:
    title = gen_text.DOC_TITLES[doc_type]
    document = Document()
    document.core_properties.created = _FIXED_TIME
    document.core_properties.modified = _FIXED_TIME
    document.core_properties.last_modified_by = "eval-gen"
    entities_all: dict[str, list[str]] = {}
    for i in range(pages):
        data = gen_text.build_page(i, doc_type=doc_type, density=density)
        document.add_heading(f"{title}（第 {i + 1} 段 / 共 {pages} 段）", level=2)
        for line in data["lines"]:
            document.add_paragraph(line)
        for etype, values in data["entities"].items():
            entities_all.setdefault(etype, []).extend(values)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(out_path))
    merged = {k: sorted(set(v)) for k, v in entities_all.items() if v}
    return [{"page": 0, "entities": merged}]
