"""Issue #13：扫描件判定阈值提升为 settings + 解析时预热扫描页缓存。"""

from pathlib import Path

import fitz
import pytest

from app.core.config import settings
from app.models.common import FileType
from app.services.file_parser import FileParser


def _make_page_with_partial_image(path: Path, coverage: float) -> None:
    """生成一页内嵌 coverage 比例面积图片的单页 PDF。"""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    w = page.rect.width * coverage
    h = page.rect.height * coverage
    rect = fitz.Rect(0, 0, w, h)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, max(1, int(w)), max(1, int(h))), 0)
    pix.clear_with(240)
    page.insert_image(rect, pixmap=pix)
    doc.save(str(path))
    doc.close()


@pytest.fixture(autouse=True)
def _allow_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))


def test_image_coverage_ratio_follows_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """覆盖率阈值应可由 settings 覆盖：0.95 面积的图默认判扫描，调高阈值后不判。"""
    p = tmp_path / "partial.pdf"
    _make_page_with_partial_image(p, coverage=0.95)
    doc = fitz.open(str(p))
    page = doc.load_page(0)

    monkeypatch.setattr(settings, "SCAN_PAGE_IMAGE_COVERAGE_RATIO", 0.9)
    assert FileParser._is_scanned_page(page) is True

    monkeypatch.setattr(settings, "SCAN_PAGE_IMAGE_COVERAGE_RATIO", 0.99)
    assert FileParser._is_scanned_page(page) is False
    doc.close()


def test_fragment_thresholds_follow_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """碎片化判定阈值应可由 settings 覆盖。"""
    normal = "\n".join(f"这是正常长度的一行文本内容 {i}" for i in range(8))
    assert not FileParser.has_fragmented_text_layer(normal)

    # 平均行长阈值调到 100、短行占比阈值降到 0：短行文本也会被判为碎片化
    monkeypatch.setattr(settings, "SCAN_FRAGMENT_MIN_AVG_LINE_LEN", 100)
    monkeypatch.setattr(settings, "SCAN_FRAGMENT_SHORT_LINE_RATIO", 0.0)
    broken2 = "\n".join(["短", "行", "文本", "001", "报警", "回执", "002", "003"])
    assert FileParser.has_fragmented_text_layer(broken2)

    # 短行占比阈值调到 1.0：任何文本都不判碎片化
    broken = "\n".join(["顺", "序", "号", "文 件", "001", "报警", "回执", "002"])
    monkeypatch.setattr(settings, "SCAN_FRAGMENT_MIN_AVG_LINE_LEN", 12)
    monkeypatch.setattr(settings, "SCAN_FRAGMENT_SHORT_LINE_RATIO", 1.0)
    assert not FileParser.has_fragmented_text_layer(broken)


@pytest.mark.asyncio
async def test_parse_pdf_warms_scan_cache(tmp_path: Path) -> None:
    """_parse_pdf 逐页判定扫描页后应回填 _pdf_page_scan_cache，供视觉链路命中。"""
    from app.services.file_parser import FileParser as FP

    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page(width=595, height=842)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 595, 842), 0)
        pix.clear_with(240)
        page.insert_image(page.rect, pixmap=pix)
    p = tmp_path / "scanned.pdf"
    doc.save(str(p))
    doc.close()

    resolved = str(p.resolve())
    # 清掉同类测试可能残留的类级缓存
    with FP._pdf_page_scan_cache_lock:
        FP._pdf_page_scan_cache.clear()

    result = await FileParser().parse(str(p), FileType.PDF)
    assert result.is_scanned

    with FP._pdf_page_scan_cache_lock:
        keys = {key for key in FP._pdf_page_scan_cache if key[0] == resolved}
        values = [FP._pdf_page_scan_cache[k] for k in keys]
    assert len(keys) == 2
    assert all(values)
