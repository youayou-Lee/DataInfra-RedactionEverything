"""locate-entities 端点（Issue #66 预览定位）：
文本型 PDF 打码模式下，NER 实体 → 页面归一化框，供图像工作台叠加显示。
与执行链路共用 locate_entity_texts 核心，所见即所打。
"""

import os

import fitz
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services import file_management_service as fms

client = TestClient(app)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    up = tmp_path / "uploads"
    up.mkdir()
    monkeypatch.setattr(settings, "AUTH_ENABLED", False)  # require_auth → anonymous
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(up))
    fms.file_store.clear()
    yield


def _register_pdf(file_type: str = "pdf", name: str = "t.pdf") -> str:
    path = os.path.join(settings.UPLOAD_DIR, name)
    if file_type == "pdf":
        doc = fitz.open()
        for _ in range(2):
            doc.new_page()
        for i in range(2):
            doc[i].insert_text((72, 130), "委托人陈明飞到案", fontsize=12, fontname="china-s")
        doc.save(path)
        doc.close()
    else:
        with open(path, "w") as f:
            f.write("x")
    file_id = f"loc_{name.replace('.', '_')}"
    fms.file_store[file_id] = {
        "file_id": file_id,
        "file_path": path,
        "file_type": file_type,
        "original_filename": name,
        "owner_id": "anonymous",
    }
    return file_id


def test_locate_entities_returns_boxes_and_missed():
    file_id = _register_pdf()
    resp = client.post(
        f"/api/v1/redaction/{file_id}/locate-entities",
        json={
            "entities": [
                {"id": "e1", "text": "陈明飞", "type": "PERSON", "start": 0, "end": 3, "page": 1, "selected": True},
                {"id": "e2", "text": "不存在的文本XYZ", "type": "ORG", "start": 0, "end": 7, "page": 1, "selected": True},
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    # 两页同名 → 两框（C1 全文搜索语义）；ghost → missed；框类型=实体类型
    assert len(data["boxes"]) == 2
    assert {b["page"] for b in data["boxes"]} == {1, 2}
    assert all(b["type"] == "PERSON" for b in data["boxes"])
    assert all(0 <= b["x"] < 1 and 0 < b["width"] <= 1 for b in data["boxes"])
    assert data["missed"] == ["不存在的文本XYZ"]


def test_locate_entities_rejects_non_pdf():
    file_id = _register_pdf(file_type="txt", name="t.txt")
    resp = client.post(
        f"/api/v1/redaction/{file_id}/locate-entities",
        json={"entities": []},
    )
    assert resp.status_code == 400


def test_locate_entities_owner_check(tmp_path, monkeypatch):
    file_id = _register_pdf(name="t2.pdf")
    # store 取出的是副本，须整条重写才生效（AUTH_ENABLED=False 时
    # require_auth 恒 anonymous，用属主不匹配验证 404 分支）
    info = dict(fms.file_store[file_id])
    info["owner_id"] = "someone-else"
    fms.file_store[file_id] = info
    resp = client.post(
        f"/api/v1/redaction/{file_id}/locate-entities",
        json={"entities": []},
    )
    assert resp.status_code == 404
