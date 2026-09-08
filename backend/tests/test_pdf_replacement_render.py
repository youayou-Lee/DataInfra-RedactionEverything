"""文本 PDF 回写回归：CJK 替换词必须可渲染可提取（helv 缺字形曾写成 ???），
数字串放不下时 insert_textbox 整体不写（曾静默丢失身份证虚构号）。"""

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


@pytest.mark.asyncio
async def test_pdf_pseudonym_replacements_render_and_extract(_dirs):
    up, out = _dirs
    src = up / "t.pdf"
    _make_pdf(src)
    entities = [
        Entity(id="e1", text="陈明飞", type="PERSON", start=0, end=3, page=1, selected=True),
        Entity(id="e2", text="110101199003078515", type="ID_CARD", start=0, end=18, page=1, selected=True),
    ]
    pools = {"PERSON": {"words": ["王某"], "strategy": "numbered", "custom_map": {}}}
    result = await Redactor().redact(
        file_info={"file_path": str(src), "file_type": "pdf"},
        entities=entities, bounding_boxes=[],
        config=RedactionConfig(replacement_mode="pseudonym", word_pools=pools),
    )
    assert result["redacted_count"] == 2
    text = fitz.open(result["output_path"]).load_page(0).get_text()
    assert "陈明飞" not in text and "110101199003078515" not in text
    assert "王某" in text, "CJK 替换词未被写入/提取（helv 缺字形）"
    assert result["entity_map"]["110101199003078515"] in text.replace(" ", ""), \
        "数字替换词未写入（insert_textbox 放不下静默丢弃）"
