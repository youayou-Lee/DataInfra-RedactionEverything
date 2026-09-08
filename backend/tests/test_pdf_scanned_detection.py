"""扫描型 PDF（带内嵌 OCR 文本层）应被识别为扫描件，而不是按文本型处理。

对应 Issue：扫描型 PDF 被误判为文本型，走文本提取链路导致敏感信息漏识别。
"""

from pathlib import Path

import fitz
import pytest

from app.models.common import FileType
from app.core.config import settings
from app.services.file_parser import FileParser


def _make_text_pdf(path: Path) -> None:
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), f"这是第 {i + 1} 页的正常文本型 PDF 内容。" * 5, fontsize=11)
    doc.save(str(path))
    doc.close()


def _make_scanned_pdf_with_ocr_layer(path: Path) -> None:
    """模拟扫描件：整页图片 + 叠加的低质量 OCR 文本层（断行、短行多）。"""
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page(width=595, height=842)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 595, 842), 0)
        pix.clear_with(240)
        page.insert_image(page.rect, pixmap=pix)
        # OCR 文本层的典型形态：大量碎片短行
        y = 40
        for chunk in ["张", "三，身份", "证号 3", "30101", "19900", "71234", "5，住", "址：北", "京市海", "淀区XX", "街道。"]:
            page.insert_text((72, y), chunk, fontsize=10)
            y += 16
    doc.save(str(path))
    doc.close()


@pytest.fixture(autouse=True)
def _allow_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))


@pytest.mark.asyncio
async def test_text_pdf_not_classified_as_scanned(tmp_path: Path) -> None:
    p = tmp_path / "text.pdf"
    _make_text_pdf(p)
    result = await FileParser().parse(str(p), FileType.PDF)
    assert not result.is_scanned
    assert result.content.strip()


@pytest.mark.asyncio
async def test_scanned_pdf_with_ocr_layer_classified_as_scanned(tmp_path: Path) -> None:
    p = tmp_path / "scanned.pdf"
    _make_scanned_pdf_with_ocr_layer(p)
    result = await FileParser().parse(str(p), FileType.PDF)
    assert result.is_scanned
    # 扫描件不应把低质量文本层交给文本链路
    assert not result.content.strip()
    assert not result.pages


def test_fragmented_text_layer_detected() -> None:
    broken = "\n".join(["顺", "序", "号", "文 件", "001", "报警", "回执", "002"])
    assert FileParser.has_fragmented_text_layer(broken)
    normal = "这是一段正常排版的文本行，长度足够长，不会被判为碎片化文本层。" * 3
    assert not FileParser.has_fragmented_text_layer(normal)


@pytest.mark.asyncio
async def test_prime_probe_marks_scanned_page_and_skips_layer(tmp_path: Path) -> None:
    """视觉链路的文本层预热探测应把扫描页记为强信号，整份文件后续跳过文本层。"""
    from app.services.vision.pdf_text_layer_probe import (
        _PDF_TEXT_LAYER_SPARSE_COUNTS,
        _should_skip_sparse_pdf_text_layer,
    )
    from app.services.vision_service import prime_pdf_text_layer_sparse_probe

    p = tmp_path / "scanned.pdf"
    _make_scanned_pdf_with_ocr_layer(p)
    _PDF_TEXT_LAYER_SPARSE_COUNTS.clear()

    stats = await prime_pdf_text_layer_sparse_probe(str(p), FileType.PDF_SCANNED)
    assert stats["ran"] is True
    assert stats.get("scan_page") is True
    assert _should_skip_sparse_pdf_text_layer(str(p), FileType.PDF_SCANNED)

    _PDF_TEXT_LAYER_SPARSE_COUNTS.clear()


@pytest.mark.asyncio
async def test_detect_with_pdf_text_layer_rejects_scan_page(tmp_path: Path) -> None:
    """文本层检测遇到扫描页（整页图 + OCR 文本层）应抛 ValueError 回退图像 OCR。"""
    from app.services import vision_service as vs

    p = tmp_path / "scanned.pdf"
    _make_scanned_pdf_with_ocr_layer(p)

    service = vs.VisionService.__new__(vs.VisionService)
    service.file_parser = FileParser()
    service.last_pdf_text_layer_duration_ms = 0
    service.last_pdf_text_layer_stats = {}

    with pytest.raises(ValueError, match="scanned page"):
        await service._detect_with_pdf_text_layer(str(p), 1, None)
    assert service.last_pdf_text_layer_stats.get("scan_page") is True
