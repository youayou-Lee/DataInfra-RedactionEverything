"""Issue #8 导出契约：Word 修订历史（w:delText）中的实体也替换；
成品自检（residual_entities）能发现文本层残留。"""

import asyncio
import zipfile
from io import BytesIO

import pytest
from docx import Document
from lxml import etree

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


def _make_docx_with_tracked_deletion(path) -> bytes:
    """构造带 w:del（已删除敏感词仍留在修订历史）的 docx。"""
    doc = Document()
    doc.add_paragraph("委托人：陈明飞，联系电话 13800138000")
    buf = BytesIO(); doc.save(buf)

    # 直接在 document.xml 中插入一段修订：w:del 包着 w:delText
    raw = zipfile.ZipFile(BytesIO(buf.getvalue())).read("word/document.xml")
    root = etree.fromstring(raw)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    p = root.findall(".//w:p", ns)[0]
    del_run = etree.SubElement(p, f"{{{ns['w']}}}r")
    etree.SubElement(del_run, f"{{{ns['w']}}}delText").text = "，代理人成龙飞已删除此句"
    out = BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for item in zipfile.ZipFile(BytesIO(buf.getvalue())).infolist():
            data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True) \
                if item.filename == "word/document.xml" else zipfile.ZipFile(BytesIO(buf.getvalue())).read(item.filename)
            z.writestr(item, data)
    blob = out.getvalue()
    with open(path, "wb") as f:
        f.write(blob)
    return blob


@pytest.mark.asyncio
async def test_docx_tracked_deletion_redacted(_dirs):
    up, out = _dirs
    src = up / "t.docx"
    _make_docx_with_tracked_deletion(str(src))

    entities = [
        Entity(id="e1", text="陈明飞", type="PERSON", start=0, end=3, page=1, selected=True),
        Entity(id="e2", text="成龙飞", type="PERSON", start=0, end=3, page=1, selected=True),
        Entity(id="e3", text="13800138000", type="PHONE", start=0, end=11, page=1, selected=True),
    ]
    pools = {"PERSON": {"words": ["王某", "赵某"], "strategy": "numbered", "custom_map": {}}}
    result = await Redactor().redact(
        file_info={"file_path": str(src), "file_type": "docx"},
        entities=entities, bounding_boxes=[],
        config=RedactionConfig(replacement_mode="pseudonym", word_pools=pools),
    )
    # 修订历史里的 w:delText 也必须替换（导出自检以 XML 全文兜底）
    with zipfile.ZipFile(result["output_path"]) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert "成龙飞" not in xml and "陈明飞" not in xml, "修订历史残留敏感词"
    assert "王某" in xml and "赵某" in xml
    # 自检契约：文本层无残留（当前 _extract 走 python-docx 不含 delText，
    # 但正文替换 + XML 断言已覆盖；此处验证 residual 列表本身工作正常）
    assert result["residual_entities"] == []


@pytest.mark.asyncio
async def test_residual_detection_contract(_dirs):
    """自检契约能发现残留：给一个 docx 两个实体，其中一个替换词恰好等于
    另一个实体原文（构造残留），residual_entities 必须报出来。"""
    up, out = _dirs
    src = up / "t2.docx"
    doc = Document()
    doc.add_paragraph("甲方张三，乙方李四")
    doc.save(str(src))

    entities = [
        Entity(id="e1", text="张三", type="PERSON", start=0, end=2, page=1, selected=True),
        # custom 映射把「李四」换成「张三」——成品里出现原文「张三」构成残留
        Entity(id="e2", text="李四", type="PERSON", start=0, end=2, page=1, selected=True),
    ]
    pools = {"PERSON": {"words": [], "strategy": "numbered", "custom_map": {"李四": "张三"}}}
    result = await Redactor().redact(
        file_info={"file_path": str(src), "file_type": "docx"},
        entities=entities, bounding_boxes=[],
        config=RedactionConfig(replacement_mode="pseudonym", word_pools=pools),
    )
    assert result["entity_map"] == {"张三": "赵某1", "李四": "张三"} or result["entity_map"].get("李四") == "张三"
    assert "张三" in result["residual_entities"], "自检未发现成品中的原文残留"
