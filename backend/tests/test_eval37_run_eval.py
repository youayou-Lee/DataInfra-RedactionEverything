"""Issue #37 run_eval 纯函数单测：实体抽取归一、数字分级、percentile、manifest 过滤。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "eval" / "scripts"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


common_api = _load("common_api_under_test", SCRIPTS_DIR / "common_api.py")
run_eval = _load("run_eval_under_test", SCRIPTS_DIR / "run_eval.py")
nerq = sys.modules.get("eval_ner_quality") or _load(
    "eval_ner_quality_direct", REPO_ROOT / "backend" / "scripts" / "eval" / "eval_ner_quality.py")


def test_type_id_normalized_to_chinese():
    resp = {"bounding_boxes": [
        {"type": "PERSON", "text": "王建国"},
        {"type": "ID_CARD", "text": " 11010119600101000X "},
        {"type": "SOME_FUTURE_TYPE", "text": "未知类型串"},
        {"type": "PERSON", "text": ""},  # 空文本跳过
    ]}
    entities = common_api.extract_page_entities(resp)
    assert entities["姓名"] == ["王建国"]
    assert entities["身份证号"] == ["11010119600101000X"]  # strip
    assert "SOME_FUTURE_TYPE" in entities  # 未映射类型保留不丢
    assert all(v for values in entities.values() for v in values)


def test_squash_domain():
    assert common_api.squash("赵 伟\n娜") == "赵伟娜"
    assert common_api.squash("6222 0210 1234 5678") == "6222021012345678"


def test_e2e_digital_exact_and_near_miss():
    gt = {"身份证号": ["11010119600101000X"], "电话": ["13800138000", "13911112222"]}
    pred = {"身份证号": ["11010119600101000X"], "电话": ["138 0013 8000"]}  # 一个 exact 一个 near_miss
    summary = run_eval.e2e_digital(gt, pred)
    assert summary["身份证号"]["exact"] == 1 and summary["身份证号"]["exact_rate"] == 1.0
    assert summary["电话"]["exact"] == 0 and summary["电话"]["near_miss"] == 1
    assert summary["电话"]["exact_rate"] < 1.0


def test_digital_gate_respects_original_domain():
    """squash 域 P/R 匹配不虚抬数字闸门：空格差异必须落在 near_miss 而非 exact。"""
    records = [{"page_id": 0, "gt": {"电话": ["13800138000"]}, "pred": {"电话": ["13800138000"]},
                "latency_sec": 0.1}]
    metrics = nerq.compute_metrics(records)
    assert metrics["digital"]["电话"]["exact_rate"] == 1.0
    records[0]["pred"] = {"电话": ["138 0013 8000"]}
    metrics = nerq.compute_metrics(records)
    assert metrics["digital"]["电话"]["exact_rate"] == 0.0
    assert metrics["digital"]["电话"]["near_miss"] == 1


def test_percentile():
    assert run_eval.percentile([], 95) == 0.0
    assert run_eval.percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5  # 线性插值
    assert run_eval.percentile([1.0, 2.0, 3.0, 4.0], 95) == pytest.approx(3.85)
    assert run_eval.percentile([5.0], 50) == 5.0


def test_load_manifest_suite_filter():
    synthetic = run_eval.load_manifest("synthetic")
    assert synthetic and all(f["source"] == "synthetic" for f in synthetic)
    all_files = run_eval.load_manifest("all")
    assert len(all_files) >= len(synthetic)
    manifest = json.loads(run_eval.MANIFEST_PATH.read_text(encoding="utf-8"))
    ner_only = [f for f in manifest["files"] if "ner" in f["levels"]]
    assert len(ner_only) == 1 and ner_only[0]["id"] == run_eval.NER_CORPUS_ID
    assert all(f["id"] != run_eval.NER_CORPUS_ID for f in all_files)  # e2e 层不含 ner 语料


def test_render_e2e_markdown_smoke():
    result = {
        "overall": {"overall": {"precision": 1.0, "recall": 0.5, "f1": 0.667, "tp": 1, "fp": 0, "fn": 1},
                    "per_type": {"姓名": {"precision": 1.0, "recall": 0.5, "f1": 0.667, "tp": 1, "fp": 0, "fn": 1}},
                    "digital": {"电话": {"exact": 1, "near_miss": 0, "miss": 0, "exact_rate": 1.0}},
                    "digital_gate": {"pass": True, "failures": []}},
        "per_file": [{
            "file": {"id": "x", "carrier": "txt", "gt_entities": 2},
            "overall": {"precision": 1.0, "recall": 0.5, "f1": 0.667, "tp": 1, "fp": 0, "fn": 1},
            "digital_gate": {"pass": True, "failures": []},
            "perf": {"wall_s": {"total": 1.0}, "throughput_pages_per_min": 60,
                     "duration_ms": {"ocr": {"mean": 100.0, "p95": 120.0}}}}],
    }

    class Args:
        env_label = "t-env"
        target_label = "t-target"
        api_base = "http://x"
    md = run_eval.render_e2e_markdown(result, Args())
    assert "t-env" in md and "数字保真闸门" in md and "| x | txt |" in md
