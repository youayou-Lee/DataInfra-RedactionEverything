"""Issue #37 leak_check 单测：干净通过 / 注入残留被拦 / 归一化变体 / docx 支持。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "eval" / "scripts"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


leak_check = _load("leak_check_under_test", SCRIPTS_DIR / "leak_check.py")

ROWS = [
    {"原文": "王建国", "类型": "姓名", "化名": "王某1"},
    {"原文": "11010119600101000X", "类型": "身份证号", "化名": "110101196001010011"},
    {"原文": "华宸信息技术有限公司", "类型": "机构名称", "化名": "某公司1"},
]


def test_clean_text_passes(tmp_path):
    target = tmp_path / "clean.txt"
    target.write_text("甲方王某1（身份证号 110101196001010011）与某公司1 签订合同。", encoding="utf-8")
    result = leak_check.run_check(target, ROWS)
    assert result["clean"] and result["findings"] == []


def test_raw_leak_detected(tmp_path):
    target = tmp_path / "leak.txt"
    target.write_text("甲方王某1，联系电话转王建国名下账户。", encoding="utf-8")
    result = leak_check.run_check(target, ROWS)
    assert not result["clean"]
    assert any(f["原文"] == "王建国" for f in result["findings"])


def test_punctuated_leak_detected(tmp_path):
    """残留以『王，建国』『王 建国』形态出现也要被抓（强归一化）。"""
    target = tmp_path / "leak2.txt"
    target.write_text("经查，王，建 国 已于 2026 年 3 月离职。", encoding="utf-8")
    result = leak_check.run_check(target, ROWS)
    assert any(f["原文"] == "王建国" for f in result["findings"])


def test_digital_leak_detected(tmp_path):
    target = tmp_path / "leak3.txt"
    target.write_text("证号：11010119600101000X（历史档案）。", encoding="utf-8")
    result = leak_check.run_check(target, ROWS)
    assert any(f["原文"].startswith("110101") for f in result["findings"])


def test_docx_supported(tmp_path):
    from docx import Document
    document = Document()
    document.add_paragraph("甲方某公司1 与 王建国 签订本协议。")
    target = tmp_path / "leak.docx"
    document.save(str(target))
    result = leak_check.run_check(target, ROWS)
    assert any(f["原文"] == "王建国" for f in result["findings"])


def test_csv_roundtrip(tmp_path):
    csv_path = tmp_path / "m.csv"
    leak_check  # noqa: B018 — 与本文件无关，占位避免误删 import
    import csv
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["原文", "类型", "化名"])
        writer.writeheader()
        writer.writerows(ROWS)
    rows = leak_check.load_mapping(csv_path)
    assert [r["原文"] for r in rows] == [r["原文"] for r in ROWS]
