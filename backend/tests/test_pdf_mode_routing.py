"""文本型 PDF 双链路路由（Issue #61）：
MASK=实体定位→整页栅格化真打码（与扫描件同构）；
替换模式=PDF→docx→替换→PDF 回转，转换失败回退原位替换。
"""

import asyncio
import os

import fitz
import pytest

from app.core.config import settings
from app.models.entity_schemas import Entity
from app.models.redaction_schemas import RedactionConfig
from app.services.redactor import Redactor


@pytest.fixture()
def _dirs(tmp_path, monkeypatch):
    up = tmp_path / "uploads"; out = tmp_path / "outputs"
    up.mkdir(); out.mkdir()
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(up))
    monkeypatch.setattr(settings, "OUTPUT_DIR", str(out))
    return up, out


def _make_pdf(path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 130), "委托人：陈明飞，身份证号 110101199003078515", fontsize=12, fontname="china-s")
    doc.save(str(path)); doc.close()


def _entities():
    return [
        Entity(id="e1", text="陈明飞", type="PERSON", start=0, end=3, page=1, selected=True),
        Entity(id="e2", text="110101199003078515", type="ID_CARD", start=0, end=18, page=1, selected=True),
    ]


def _page_has_images(pdf_path):
    doc = fitz.open(pdf_path)
    try:
        imgs = doc.load_page(0).get_images(full=True)
    finally:
        doc.close()
    return len(imgs) > 0


@pytest.mark.asyncio
async def test_pdf_mask_rasterizes_and_mosaics(_dirs):
    """MASK 模式：文本型 PDF 栅格化为图像页，原文从文本层消失。"""
    up, _ = _dirs
    src = up / "t.pdf"
    _make_pdf(src)
    result = await Redactor().redact(
        file_info={"file_path": str(src), "file_type": "pdf"},
        entities=_entities(), bounding_boxes=[],
        config=RedactionConfig(replacement_mode="mask"),
    )
    assert result["redacted_count"] >= 2
    doc = fitz.open(result["output_path"])
    try:
        text = doc.load_page(0).get_text()
    finally:
        doc.close()
    assert "陈明飞" not in text and "110101199003078515" not in text
    assert _page_has_images(result["output_path"]), "MASK 产物应含栅格化图像页"
    assert result["residual_entities"] == []


@pytest.mark.asyncio
async def test_pdf_mask_missing_entity_falls_back_to_text_mask(_dirs):
    """实体在文本层定位失败（跨行等）：回退文本掩码链路，原文仍删除，
    未定位实体进 residual_entities 显性暴露，不允许静默漏打码。"""
    up, _ = _dirs
    src = up / "t.pdf"
    _make_pdf(src)
    ghost = Entity(id="e3", text="不存在的实体文本XYZ", type="PERSON", start=0, end=3, page=1, selected=True)
    result = await Redactor().redact(
        file_info={"file_path": str(src), "file_type": "pdf"},
        entities=_entities() + [ghost], bounding_boxes=[],
        config=RedactionConfig(replacement_mode="mask"),
    )
    doc = fitz.open(result["output_path"])
    try:
        text = doc.load_page(0).get_text()
    finally:
        doc.close()
    assert "陈明飞" not in text, "可定位实体仍被真打码，原文不应在文本层"
    assert "不存在的实体文本XYZ" in result["residual_entities"]


@pytest.mark.asyncio
async def test_pdf_replacement_routes_via_docx_roundtrip(_dirs):
    """替换模式：走 pdf→docx→替换→PDF 回转；LibreOffice 缺失时自动回退
    原位替换——两种产物都必须满足『原文消失+替换词落盘』契约。"""
    up, _ = _dirs
    src = up / "t.pdf"
    _make_pdf(src)
    pools = {"PERSON": {"words": ["王某"], "strategy": "numbered", "custom_map": {}}}
    result = await Redactor().redact(
        file_info={"file_path": str(src), "file_type": "pdf"},
        entities=_entities(), bounding_boxes=[],
        config=RedactionConfig(replacement_mode="pseudonym", word_pools=pools),
    )
    text = "".join(p.get_text() for p in fitz.open(result["output_path"]))
    assert "陈明飞" not in text.replace(" ", "")
    assert "110101199003078515" not in text.replace(" ", "")
    assert "王某" in text.replace(" ", ""), "替换词未落盘"
    assert result["redacted_count"] == 2


@pytest.mark.asyncio
async def test_pdf_replacement_prefers_docx_roundtrip(_dirs, monkeypatch):
    """路由断言：替换模式必须优先尝试 docx 回转链路（而非静默走原位）。"""
    up, _ = _dirs
    src = up / "t.pdf"
    _make_pdf(src)
    called = {}

    def _fake_pdf2docx(src, wd):
        from docx import Document as _Doc
        import shutil as _sh
        fake_docx = os.path.join(wd, "fake_source.docx")
        _Doc().save(fake_docx)
        called["pdf2docx"] = True
        _sh.move(fake_docx, os.path.join(wd, "source.docx"))
        return os.path.join(wd, "source.docx")

    monkeypatch.setattr(Redactor, "_pdf_to_docx", staticmethod(_fake_pdf2docx))
    async def _fake_docx2pdf(docx, out):
        called["docx2pdf"] = True
        import shutil; shutil.copy(str(src), out); return True
    monkeypatch.setattr(Redactor, "_docx_to_pdf", staticmethod(_fake_docx2pdf))
    result = await Redactor().redact(
        file_info={"file_path": str(src), "file_type": "pdf"},
        entities=_entities(), bounding_boxes=[],
        config=RedactionConfig(replacement_mode="structured"),
    )
    assert called.get("pdf2docx") and called.get("docx2pdf"), "替换模式未走 docx 回转链路"


def test_entities_to_norm_boxes_coordinates(_dirs):
    """实体定位框为归一化坐标且落在页面范围内。"""
    up, _ = _dirs
    src = up / "t.pdf"
    _make_pdf(src)
    boxes, missed = Redactor._entities_to_norm_boxes(str(src), _entities())
    assert not missed
    assert len(boxes) == 2
    for b in boxes:
        assert 0 <= b.x < 1 and 0 <= b.y < 1
        assert 0 < b.width <= 1 and 0 < b.height <= 1
        assert b.selected and b.page == 1
