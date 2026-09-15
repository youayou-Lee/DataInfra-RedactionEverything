"""Issue #46 格式矩阵样张一键生成：统一 payload → eval/datasets/formats/ 全格式样张 + GT。

用法（仓库根执行）：
  python eval/datasets/generators/build_formats.py            # 全量重建
  python eval/datasets/generators/build_formats.py --skip-doc # 跳过 .doc（无 LibreOffice 时）

确定性：txt/md/html/htm/rtf/docx/pdf/图片逐字节一致（图片受 PIL 版本影响，
以「尺寸+GT 一致」为承诺）；.doc 由 LibreOffice 转换，只承诺 OLE 魔数与文本内容。

全部为合成虚构数据，样张与 GT 一并入库（沿用 #37 惯例）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from docx import Document
from PIL import Image

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gen_image  # noqa: E402
import gen_pdf  # noqa: E402
import gen_rtf  # noqa: E402
from gen_formats_payload import build_payload  # noqa: E402

FORMATS_DIR = _REPO_ROOT / "eval" / "datasets" / "formats"

_FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
_FIXED_ZIP_DATE = (1980, 1, 1, 0, 0, 0)
_DOC_TITLE = "授权确认书"

# 矩阵样张清单：文件名（fmt_<ext>）+ 类别（后端分派路径）
TEXT_FORMATS = ["txt", "md", "html", "htm", "rtf", "docx", "doc"]
IMAGE_EXTS = ["jpg", "jpeg", "png", "bmp", "gif", "webp", "tif", "tiff"]


def _freeze_zip(source: Path, out_path: Path) -> None:
    """重写 zip 条目时间戳为固定值（同 gen_docx，保证 docx 产物字节确定）。"""
    with zipfile.ZipFile(str(source)) as zf_in, \
            zipfile.ZipFile(str(out_path), "w", zipfile.ZIP_DEFLATED) as zf_out:
        for info in zf_in.infolist():
            new_info = zipfile.ZipInfo(info.filename, date_time=_FIXED_ZIP_DATE)
            new_info.compress_type = info.compress_type
            new_info.external_attr = info.external_attr
            new_info.create_system = info.create_system
            zf_out.writestr(new_info, zf_in.read(info.filename))


def _wrap_md(lines: list[str]) -> list[str]:
    """md 语法包装：标题/列表/加粗各一处，验证语法字符不干扰识别。"""
    body = [f"# {_DOC_TITLE}", ""]
    for i, line in enumerate(lines[2:7]):
        prefix = "- " if i in (1, 3) else ""
        body.append(prefix + line)
    body += ["", "---", "", lines[8]]
    return body


def _wrap_html(lines: list[str]) -> str:
    """HTML 骨架：<title> + <p> 标签，验证标签剥离。"""
    body = "\n".join(f"    <p>{line}</p>" for line in lines[2:8])
    return ("\n".join([
        "<!DOCTYPE html>",
        "<html lang=\"zh-CN\">",
        "<head><meta charset=\"utf-8\"><title>授权确认书</title></head>",
        "<body>",
        f"  <h1>{_DOC_TITLE}</h1>",
        body,
        f"  <p>{lines[8]}</p>",
        "</body>",
        "</html>",
    ]) + "\n")


def _build_docx(out_path: Path, lines: list[str]) -> None:
    document = Document()
    document.core_properties.created = _FIXED_TIME
    document.core_properties.modified = _FIXED_TIME
    document.core_properties.last_modified_by = "eval-gen46"
    document.add_heading(_DOC_TITLE, level=1)
    for line in lines[2:]:
        document.add_paragraph(line)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    document.save(str(tmp))
    _freeze_zip(tmp, out_path)
    tmp.unlink(missing_ok=True)


def _build_text_pdf(out_path: Path, lines: list[str]) -> None:
    doc = fitz_open()
    page = doc.new_page(width=gen_pdf.PAGE_W, height=gen_pdf.PAGE_H)
    gen_pdf._insert_lines(page, lines, _DOC_TITLE, 0, 1)
    _save_pdf(doc, out_path)


def _build_scanned_pdf(out_path: Path, lines: list[str]) -> None:
    pix = gen_pdf._rasterize_page(lines, _DOC_TITLE, 0, 1)
    doc = fitz_open()
    page = doc.new_page(width=gen_pdf.PAGE_W, height=gen_pdf.PAGE_H)
    page.insert_image(page.rect, pixmap=pix)
    _save_pdf(doc, out_path)


def fitz_open():
    import fitz
    return fitz.open()


def _save_pdf(doc, out_path: Path) -> None:
    import fitz
    doc.set_metadata(gen_pdf._FIXED_META)
    data = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    data = fitz_stabilize_id(data)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)


def fitz_stabilize_id(data: bytes) -> bytes:
    """固定 trailer /ID（同 gen_pdf，rfind 防内容流伪 /ID 误替换）。"""
    import re
    fixed = b"/ID [<46366d6174697834><46366d6174697834>]"
    pattern = re.compile(rb"/ID\s*\[<?[0-9A-Fa-f\s]+>?\s*<?[0-9A-Fa-f\s]+>?\]")
    idx = data.rfind(b"/ID")
    if idx != -1:
        data = data[:idx] + pattern.sub(fixed, data[idx:], count=1)
    return data


def _build_doc(docx_path: Path, out_dir: Path) -> Path:
    script = Path(__file__).resolve().parent / "gen_doc.sh"
    subprocess.run(["bash", str(script), str(docx_path), str(out_dir)],
                   check=True, capture_output=True, text=True)
    return out_dir / (docx_path.stem + ".doc")


def build_all(out_dir: Path = FORMATS_DIR, *, skip_doc: bool = False) -> list[dict]:
    payload = build_payload()
    lines, entities = payload["lines"], payload["entities"]
    out_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    docx_path: Path | None = None

    def register(name: str, kind: str, note: str, extra: dict | None = None) -> None:
        entry = {"file": name, "kind": kind, "notes": note}
        if extra:
            entry.update(extra)
        entries.append(entry)
        print(f"built formats/{name}（{sum(len(v) for v in entities.values())} 实体）")

    # --- 文本类 ---
    (out_dir / "fmt_txt.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    register("fmt_txt.txt", "text", "基线（T1+#37 已覆盖，回归对照）")
    (out_dir / "fmt_md.md").write_text("\n".join(_wrap_md(lines)) + "\n", encoding="utf-8")
    register("fmt_md.md", "text", "md 语法包装（标题/列表/分隔线）")
    html_text = _wrap_html(lines)
    (out_dir / "fmt_html.html").write_text(html_text, encoding="utf-8")
    register("fmt_html.html", "text", "HTML 骨架，验证标签剥离")
    (out_dir / "fmt_htm.htm").write_text(html_text, encoding="utf-8")
    register("fmt_htm.htm", "text", "与 .html 同内容（扩展名别名）")
    rtf_text = gen_rtf.build_rtf(out_dir / "fmt_rtf.rtf", lines=lines)
    register("fmt_rtf.rtf", "text", "\\uc1\\uN? CJK 转义——后端朴素解析风险点", {"rtf_chars": len(rtf_text)})
    _build_docx(out_dir / "fmt_docx.docx", lines)
    register("fmt_docx.docx", "text", "基线（payload 版，与 #37 语料解耦）")
    docx_path = out_dir / "fmt_docx.docx"
    if not skip_doc:
        raw_doc = _build_doc(docx_path, out_dir)
        doc_path = out_dir / "fmt_doc.doc"
        raw_doc.replace(doc_path)
        register("fmt_doc.doc", "text", "LibreOffice 转出；实例需 /usr/bin/soffice 才可解析")

    # --- PDF 两类（复用 #37 渲染管线，单页统一 payload）---
    _build_text_pdf(out_dir / "fmt_text_pdf.pdf", lines)
    register("fmt_text_pdf.pdf", "pdf_text", "文本层 PDF 基线")
    _build_scanned_pdf(out_dir / "fmt_scanned_pdf.pdf", lines)
    register("fmt_scanned_pdf.pdf", "pdf_scanned", "扫描型 PDF 基线（vision 链路）")

    # --- 图片类（仅打码：替换对图片未上线，产品边界）---
    img_info = gen_image.build_image(out_dir / "fmt_jpg.jpg", lines=lines)
    register("fmt_jpg.jpg", "image", "视觉链路单文件形态首次正式验证", img_info)
    for ext in IMAGE_EXTS[1:]:
        target = out_dir / f"fmt_{ext}.{ext}"
        Image.open(out_dir / "fmt_jpg.jpg").save(str(target), format=gen_image.EXT_TO_PIL_FORMAT[f".{ext}"])
        register(f"fmt_{ext}.{ext}", "image", f"解码兼容性（{ext}）", img_info)

    # --- GT（统一 payload，与样张一一对应）---
    gt = {"source": "synthetic", "issue": 46, "payload": payload,
          "doc_title": _DOC_TITLE, "canvas": img_info}
    (out_dir / "gt.json").write_text(json.dumps(gt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: {len(entries)} 样张 + gt.json -> {out_dir}")
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue #46 格式矩阵样张生成")
    parser.add_argument("--out", default=str(FORMATS_DIR))
    parser.add_argument("--skip-doc", action="store_true", help="跳过 .doc（无 LibreOffice）")
    args = parser.parse_args()
    build_all(Path(args.out), skip_doc=args.skip_doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
