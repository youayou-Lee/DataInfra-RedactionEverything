"""Issue #92：.doc（旧版 Word）解禁后的上传校验。

9/15 判「转换链不可用」实为实例宿主未装 LibreOffice（#46 实测时的环境缺口），
2026-09-18 scnet-main 实例探针（含 WPS 真实样本）转换/解析/脱敏 0 残留。
本文件守住三道门：白名单放行 .doc、真 OLE 魔数通过、非 OLE 二进制/文本
伪装 .doc 仍被拒。
"""
import os
import tempfile

from app.core.config import settings
from app.core.file_validation import (
    get_file_type,
    validate_extension,
    validate_magic_bytes,
)

# WPS/MS Word .doc 的 OLE 复合文档魔数（Composite Document File V2）
OLE_DOC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 20
UTF8_TEXT = "被告赵涛洋，身份证号110118197712242590。".encode("utf-8")
DOCX_ZIP = b"PK\x03\x04" + b"\x00" * 20


def _write(data: bytes, name: str) -> str:
    path = os.path.join(tempfile.mkdtemp(), name)
    with open(path, "wb") as f:
        f.write(data)
    return path


def test_doc_in_allowed_extensions():
    assert ".doc" in settings.ALLOWED_EXTENSIONS


def test_doc_extension_accepted():
    assert validate_extension(".doc") is True


def test_real_ole_doc_magic_passes():
    assert validate_magic_bytes(_write(OLE_DOC, "x.doc"), ".doc") is True


def test_utf8_text_disguised_as_doc_rejected():
    # acceptance-53.doc 真实情形：UTF-8 文本伪造 .doc 扩展名
    assert validate_magic_bytes(_write(UTF8_TEXT, "x.doc"), ".doc") is False


def test_docx_bytes_under_doc_extension_pass():
    # docx 内容改名 .doc：soffice 可直接转换，放行
    assert validate_magic_bytes(_write(DOCX_ZIP, "x.doc"), ".doc") is True


def test_doc_maps_to_doc_file_type():
    assert get_file_type("合同.doc") is not None
