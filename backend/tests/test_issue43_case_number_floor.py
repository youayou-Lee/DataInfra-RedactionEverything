"""Issue #43（父 #40 F2）：案号/车牌默认勾选 + regex 保证层精确 span + DATE 误标抑制。

取证背景（#40 分诊评论）：默认类型集不含 LEGAL_CASE_ID/LICENSE_PLATE 导致首页案件编号、
受案字案号整串漏检，且案号内「2023」被 HaS 过抽为 DATE；regex 兜底旧行为按整行打码，
对叙述行过度遮盖。本文件锁定：默认开关、regex 模式精度、精确 span、DATE 抑制。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]  # backend/

ENTITY_PRESET = json.loads(
    (REPO_ROOT / "config" / "preset_entity_types.json").read_text(encoding="utf-8"))
PIPELINE_PRESET = json.loads(
    (REPO_ROOT / "config" / "preset_pipeline_types.json").read_text(encoding="utf-8"))


# ── 配置：默认勾选与 regex 模式存在 ────────────────────────────

@pytest.mark.parametrize("type_id", ["LEGAL_CASE_ID", "LICENSE_PLATE"])
def test_type_is_default_enabled_in_both_presets(type_id):
    assert ENTITY_PRESET[type_id]["default_enabled"] is True, "entity 预设需默认勾选"
    assert ENTITY_PRESET[type_id]["regex_pattern"], "entity 预设需带 regex 保证层"
    ocr_has = {t["id"]: t for t in PIPELINE_PRESET["ocr_has"]}
    assert ocr_has[type_id]["default_enabled"] is True, "pipeline 预设需镜像默认勾选"


def test_legal_preset_includes_license_plate():
    legal = next(p for p in json.loads(
        (REPO_ROOT / "config" / "industry_presets.json").read_text(encoding="utf-8"))
        if p["id"] == "industry_contract_legal_disclosure")
    assert "LICENSE_PLATE" in legal["selectedEntityTypeIds"]
    assert "LICENSE_PLATE" in legal["ocrHasTypes"]


# ── regex 模式精度 ────────────────────────────────────────────

CASE_NUMBER_RE = re.compile(ENTITY_PRESET["LEGAL_CASE_ID"]["regex_pattern"])
PLATE_RE = re.compile(ENTITY_PRESET["LICENSE_PLATE"]["regex_pattern"])


@pytest.mark.parametrize("text", [
    "清公清新(交警)受案字（2023）00236号",
    "清公清新(交警)受案字 （2023） 00236 号",   # OCR 空格噪声
    "清公清新立字（2023）00951号",
    "（2023）粤0104民初12345号",                # 法院裁判文书风格，无前缀机构
])
def test_case_number_regex_matches(text):
    assert CASE_NUMBER_RE.search(text)


@pytest.mark.parametrize("text", [
    "2023年06月15日",
    "2023年6月15日00时许",
    "441282198306062316",                       # 身份证（无括号年份+号尾）
    "0763-5811222",
    "案件编号：A4418271500002023060027",         # 编号无括号年份，交给 HaS 语义
])
def test_case_number_regex_does_not_overmatch(text):
    assert not CASE_NUMBER_RE.search(text)


@pytest.mark.parametrize("text", ["粤RH0472", "粤RH0472号牌", "京A12345", "悬挂粤RH0472号牌的普通二轮摩托车"])
def test_plate_regex_matches(text):
    assert PLATE_RE.search(text)


@pytest.mark.parametrize("text", ["HA1729", "2023年", "A4418271500002023060027"])
def test_plate_regex_does_not_overmatch(text):
    assert not PLATE_RE.search(text)


# ── regex 兜底：精确 span（不再整行打码）──────────────────────

def _block_with_chars(text: str, char_px: int = 10, height: int = 20):
    from app.services.ocr_has_vision_service import OCRTextBlock
    return OCRTextBlock(
        text=text,
        polygon=[[0, 0], [char_px * len(text), 0], [char_px * len(text), height], [0, height]],
        chars=[{"c": c, "x1": i * char_px, "y1": 0, "x2": i * char_px + char_px - 2, "y2": height}
               for i, c in enumerate(text)],
    )


def test_regex_fallback_masks_plate_span_not_whole_line():
    from app.services.ocr_has_vision_service import OcrHasVisionService
    svc = OcrHasVisionService()
    line = "悬挂粤RH0472号牌的普通二轮摩托车"
    blocks = [_block_with_chars(line)]

    regions = svc._apply_regex_fallback(blocks, 10 * len(line), 20)

    plate = [r for r in regions if r.entity_type == "LICENSE_PLATE"]
    assert plate, "车牌 regex 保证层应命中"
    r = plate[0]
    assert "粤RH0472" in r.text
    assert r.width < 10 * len(line), "必须精确到车牌 span，不得整行打码"


def test_regex_fallback_masks_case_number_span():
    from app.services.ocr_has_vision_service import OcrHasVisionService
    svc = OcrHasVisionService()
    line = "清公清新(交警)受案字（2023）00236 号"
    blocks = [_block_with_chars(line)]

    regions = svc._apply_regex_fallback(blocks, 10 * len(line), 20)

    hits = [r for r in regions if r.entity_type == "LEGAL_CASE_ID"]
    assert hits, "案号 regex 保证层应命中"
    assert "00236" in hits[0].text


# ── DATE 误标抑制 ────────────────────────────────────────────

def _region(etype, left, top, width, height):
    from app.services.ocr_has_vision_service import SensitiveRegion
    return SensitiveRegion(text=etype, entity_type=etype, left=left, top=top,
                           width=width, height=height)


def test_dates_inside_case_number_are_dropped():
    from app.services.ocr_has_vision_service import OcrHasVisionService
    svc = OcrHasVisionService()
    regions = [
        _region("LEGAL_CASE_ID", 100, 10, 300, 20),
        _region("DATE", 220, 12, 40, 16),      # 完全落在案号框内（年份过抽）
        _region("DATE", 50, 100, 60, 16),      # 页面其它日期，必须保留
        _region("PERSON", 120, 12, 40, 16),    # 非 DATE 不抑制
    ]

    kept = svc._suppress_dates_inside_number_codes(regions)

    types = [(r.entity_type, r.left) for r in kept]
    assert ("LEGAL_CASE_ID", 100) in types
    assert ("DATE", 50) in types
    assert ("PERSON", 120) in types
    assert all(not (t == "DATE" and l == 220) for t, l in types)


def test_suppression_noop_without_code_regions():
    from app.services.ocr_has_vision_service import OcrHasVisionService
    svc = OcrHasVisionService()
    regions = [_region("DATE", 10, 10, 40, 16)]

    assert svc._suppress_dates_inside_number_codes(regions) == regions
