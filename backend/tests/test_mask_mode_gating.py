"""打码(MASK)模式格式门控测试 — Issue #57。

MASK 仅对 PDF/图片类文件有意义；文本格式（DOCX/TXT/MD 等）带 mask 请求
应在 execute_redaction 入口降级为 SMART，不产生逐字符星号文本。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.models.common import ReplacementMode
from app.models.schemas import RedactionConfig, RedactionRequest
from app.services import redaction_orchestrator as orch


class _StubStore(dict):
    def set(self, key, value):
        self[key] = value


class _StubRedactor:
    def __init__(self, capture: dict[str, Any]):
        self._capture = capture

    async def redact(self, *, file_info, entities, bounding_boxes, config):
        self._capture["mode"] = config.replacement_mode
        return {
            "output_file_id": "out-1",
            "output_path": "/tmp/out",
            "redacted_count": 0,
            "entity_map": {},
            "residual_entities": [],
        }


@pytest.mark.asyncio
async def test_mask_downgraded_to_smart_for_text_formats(monkeypatch):
    capture: dict[str, Any] = {}
    monkeypatch.setattr(orch, "Redactor", lambda: _StubRedactor(capture))
    monkeypatch.setattr(orch, "_get_file_store", lambda: _StubStore({"f-1": {"file_type": "docx"}}))
    import asyncio
    monkeypatch.setattr(orch, "_get_file_store_lock", lambda: asyncio.Lock())

    config = RedactionConfig(replacement_mode=ReplacementMode.MASK)
    request = RedactionRequest(file_id="f-1", entities=[], bounding_boxes=[], config=config)
    await orch.execute_redaction(request)
    assert capture["mode"] == ReplacementMode.SMART


@pytest.mark.asyncio
async def test_mask_kept_for_pdf(monkeypatch):
    capture: dict[str, Any] = {}
    monkeypatch.setattr(orch, "Redactor", lambda: _StubRedactor(capture))
    monkeypatch.setattr(orch, "_get_file_store", lambda: _StubStore({"f-1": {"file_type": "pdf"}}))
    import asyncio
    monkeypatch.setattr(orch, "_get_file_store_lock", lambda: asyncio.Lock())

    config = RedactionConfig(replacement_mode=ReplacementMode.MASK)
    request = RedactionRequest(file_id="f-1", entities=[], bounding_boxes=[], config=config)
    await orch.execute_redaction(request)
    assert capture["mode"] == ReplacementMode.MASK
