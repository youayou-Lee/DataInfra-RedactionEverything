"""Issue #51 异常输入健壮性：损坏/空文件必须结构化报错，不允许 500 崩溃或静默空结果。

判据（#46 实测口径）：结构化错误 = PASS；500 崩溃 / 静默产出 = FAIL。
- 截断 docx（合法 zip 头+截断体）→ parse 抛 ValueError（端点映射 400），不得裸异常穿透成 500；
- 截断 png（合法头+截断体）→ _parse_image 校验解码，抛 ValueError，不得放行到视觉链路静默 0 框；
- 0 字节 txt → 拒绝（上传校验 400 + 解析层抛 ValueError 双保险）；
- 截断 pdf 同 docx 口径。
"""
import os
import zipfile
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.models.schemas import FileType
from app.services.file_parser import FileParser


@pytest.fixture()
def parser(tmp_path, monkeypatch):
    # 放行测试目录：解析器有路径守卫（仅允许 UPLOAD_DIR/OUTPUT_DIR）
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    return FileParser()


def _write(tmp_path, name: str, data: bytes) -> str:
    path = os.path.join(str(tmp_path), name)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


# ── 截断 docx ────────────────────────────────────────────────


def test_truncated_docx_raises_value_error_not_crash(parser, tmp_path):
    # 合法 zip 头 + 截断体：zipfile 直接 BadZipFile，python-docx 还可能抛其他结构错误
    path = _write(tmp_path, "truncated.docx", b"PK\x03\x04" + b"\x00" * 128)
    with pytest.raises(ValueError, match="损坏或格式异常"):
        __import__("asyncio").run(parser.parse(path, FileType.DOCX))


def test_truncated_docx_body_raises_value_error(parser, tmp_path):
    # 更隐蔽的形态：zip 目录完整但 document.xml 被截断
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document")  # 未闭合
    path = _write(tmp_path, "cut-body.docx", buf.getvalue())
    with pytest.raises(ValueError, match="损坏或格式异常"):
        __import__("asyncio").run(parser.parse(path, FileType.DOCX))


# ── 截断 pdf ─────────────────────────────────────────────────


def test_truncated_pdf_raises_value_error(parser, tmp_path):
    path = _write(tmp_path, "truncated.pdf", b"%PDF-1.4\n" + b"\x00" * 128)
    with pytest.raises(ValueError, match="损坏或格式异常"):
        __import__("asyncio").run(parser.parse(path, FileType.PDF))


# ── 截断 png ─────────────────────────────────────────────────


def test_truncated_png_raises_value_error(parser, tmp_path):
    # 合法 PNG 签名 + 截断体：此前静默放行进视觉链路 → 0 框
    path = _write(tmp_path, "truncated.png", b"\x89PNG\r\n\x1a\n" + b"IHDR" + b"\x00" * 64)
    with pytest.raises(ValueError, match="图片.*损坏|损坏或格式异常"):
        __import__("asyncio").run(parser.parse(path, FileType.IMAGE))


def test_valid_png_still_parses(parser, tmp_path):
    from PIL import Image

    path = os.path.join(str(tmp_path), "ok.png")
    Image.new("RGB", (8, 8), "white").save(path)
    result = __import__("asyncio").run(parser.parse(path, FileType.IMAGE))
    assert result.is_scanned is True  # 正常图片仍进入视觉处理


# ── 空 txt ───────────────────────────────────────────────────


def test_empty_txt_rejected_at_parse(parser, tmp_path):
    path = _write(tmp_path, "empty.txt", b"")
    with pytest.raises(ValueError, match="空"):
        __import__("asyncio").run(parser.parse(path, FileType.TXT))


# ── 上传层：0 字节拒绝 ───────────────────────────────────────


def test_validate_file_rejects_zero_byte():
    from app.api.files import validate_file

    upload = MagicMock()
    upload.filename = "empty.txt"
    upload.size = 0
    with pytest.raises(HTTPException) as exc:
        validate_file(upload)
    assert exc.value.status_code == 400
    assert "空文件" in exc.value.detail


def test_validate_file_allows_nonzero():
    from app.api.files import validate_file

    upload = MagicMock()
    upload.filename = "ok.txt"
    upload.size = 128
    validate_file(upload)  # 不抛
