"""Issue #40 ①：出生地/籍贯默认勾选。

取证：p13 跨行地址「广东省广宁县/江屯镇」在 ADDRESS 批次 HaS 0.6B 召回不稳，
NATIVE_PLACE（籍贯）批次召回（实例实验，见 #40 评论）。政法文书出生地/籍贯是
标准字段，翻默认勾选并入法律预设。
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

ENTITY_PRESET = json.loads(
    (REPO_ROOT / "config" / "preset_entity_types.json").read_text(encoding="utf-8"))
PIPELINE_PRESET = json.loads(
    (REPO_ROOT / "config" / "preset_pipeline_types.json").read_text(encoding="utf-8"))


def test_native_place_default_enabled_in_both_presets():
    assert ENTITY_PRESET["NATIVE_PLACE"]["default_enabled"] is True
    ocr_has = {t["id"]: t for t in PIPELINE_PRESET["ocr_has"]}
    assert ocr_has["NATIVE_PLACE"]["default_enabled"] is True


def test_legal_preset_includes_native_place():
    legal = next(p for p in json.loads(
        (REPO_ROOT / "config" / "industry_presets.json").read_text(encoding="utf-8"))
        if p["id"] == "industry_contract_legal_disclosure")
    assert "NATIVE_PLACE" in legal["selectedEntityTypeIds"]
    assert "NATIVE_PLACE" in legal["ocrHasTypes"]
