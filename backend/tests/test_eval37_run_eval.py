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
indicator_meta = run_eval.indicator_meta  # run_eval 已 import（同目录 sys.path）
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


def test_e2e_core_metrics_domain_separation():
    """I6：直接测组装路径——records(squash 域) 算 P/R，数字分级在原串域，空格差异落 near_miss。"""
    records = [{"page_id": 0,
                "gt": {"电话": ["13800138000"]},
                "pred": {"电话": ["13800138000"]},  # squash 域一致
                "latency_sec": 0.1}]
    gt_raw = {"电话": ["13800138000"]}
    pred_raw = {"电话": ["138 0013 8000"]}  # 原串域带空格
    metrics = run_eval.e2e_core_metrics(records, gt_raw, pred_raw)
    assert metrics["overall"]["recall"] == 1.0  # squash 域 P/R 不受空格影响
    assert metrics["digital"]["电话"]["exact"] == 0
    assert metrics["digital"]["电话"]["near_miss"] == 1
    assert metrics["digital_gate"]["pass"] is False  # 闸门在原串域，被空格差异拦下


def test_loose_span_metrics():
    """I4：span 对但类型错计入 wrong_type 与混淆矩阵。"""
    records = [{"page_id": 0,
                "gt": {"姓名": ["王建国"], "机构名称": []},
                "pred": {"机构名称": ["王建国"]},
                "latency_sec": 0.1}]
    loose = run_eval.loose_span_metrics(records)
    assert loose["wrong_type"] == 1
    assert loose["type_confusion_top"] == {"姓名→机构名称": 1}


def test_build_e2e_baseline_comparison(tmp_path):
    """I5：e2e 层基线对比 + 环境不匹配警告。"""
    baseline = {"env": {"env_label": "old-env", "target_label": "old-target"},
                "overall": {"overall": {"precision": 0.8, "recall": 0.7, "f1": 0.75, "tp": 1, "fp": 0, "fn": 1},
                            "digital": {"电话": {"total_gt": 10, "exact": 9}}}}
    p = tmp_path / "base.json"
    p.write_text(json.dumps(baseline), encoding="utf-8")

    class Args:
        baseline = str(p)
        env_label = "new-env"
    metrics = {"overall": {"overall": {"precision": 0.9, "recall": 0.7, "f1": 0.8, "tp": 2, "fp": 0, "fn": 1},
                           "digital": {"电话": {"total_gt": 10, "exact": 10}}}}
    cmp = run_eval.build_e2e_baseline_comparison(metrics, Args())
    assert cmp["env_mismatch"] is True
    assert cmp["rows"]["P"] == {"baseline": 0.8, "current": 0.9}
    assert cmp["rows"]["数字exact率"]["current"] == 1.0


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


def test_load_manifest_merges_private(tmp_path, monkeypatch):
    """v2：私有 manifest（真实案卷）存在时合并；suite=real 只取私有条目。"""
    private = tmp_path / "manifest.private.json"
    private.write_text(json.dumps({"files": [
        {"id": "real_x", "path": "/tmp/real_x.pdf", "gt": None, "source": "real",
         "carrier": "scanned_pdf", "levels": ["e2e"], "access": "private"}]}), encoding="utf-8")
    monkeypatch.setattr(run_eval, "PRIVATE_MANIFEST_PATH", private)
    real = run_eval.load_manifest("real")
    assert [f["id"] for f in real] == ["real_x"]
    merged = run_eval.load_manifest("all")
    assert any(f["id"] == "real_x" for f in merged) and any(f["source"] == "synthetic" for f in merged)


def test_indicator_meta_summary_rows():
    """v2 管理者摘要：每行必须带指标名/本次值/目标/状态/说明五要素。"""
    overall = {"overall": {"precision": 0.84, "recall": 0.86, "f1": 0.85, "tp": 1, "fp": 0, "fn": 1},
               "digital": {"电话": {"total_gt": 10, "exact": 5, "near_miss": 3, "miss": 2}},
               "loose": {"wrong_type": 1, "type_confusion_top": {}}}
    perf = {"pages": 10, "p50": 13.0, "p95": 26.0, "throughput": 4.0, "empty_pages": 2}
    rows = indicator_meta.build_e2e_summary(overall, perf, failed_count=1)
    for row in rows:
        assert all(k in row for k in ("指标", "本次", "目标/参考", "状态", "说明")), row
    by_name = {r["指标"]: r for r in rows}
    assert by_name["数字保真 exact 率"]["状态"] == "❌"  # 5/10 未达 100% 红线
    assert by_name["失败文件 / 空框页"]["本次"] == "1 / 2"
    md = indicator_meta.render_summary_md(rows)
    assert "| 指标 |" in md and "数字保真" in md
    assert len(indicator_meta.INDICATOR_DICTIONARY) >= 10  # 字典覆盖全部关键指标
    real_rows = indicator_meta.build_real_summary(perf, 0, ["real_encrypted_supplement"])
    assert any("加密卷拒识" in r["指标"] for r in real_rows)


def test_render_e2e_markdown_real_only():
    """v2：纯真实子集（无 overall）渲染速度/稳健性报告 + 管理者摘要，不出现效果表。"""
    result = {
        "overall": {}, "failed": [], "rejected": [],
        "per_file": [
            {"file": {"id": "real_zqc_wenshu", "carrier": "scanned_pdf", "gt_entities": 0},
             "perf": {"pages_total": 15, "steady_pages": 14, "wall_s": {"total": 200.0, "p50": 14.0, "p95": 20.0},
                      "throughput_pages_per_min": 4.3,
                      "pages_detail": [{"warmup": True, "wall_s": 9.0, "entities": {"姓名": ["张"]}},
                                       {"warmup": False, "wall_s": 14.0, "entities": {}},
                                       {"warmup": False, "wall_s": 15.0, "entities": {"电话": ["138"]}}]},
             "robustness": {"outcome": "ok", "detail": "14 steady 页，空框页 1", "empty_pages": 1}},
            {"file": {"id": "real_encrypted_supplement", "carrier": "encrypted_pdf", "gt_entities": 0},
             "perf": None,
             "robustness": {"outcome": "accepted_should_reject",
                            "detail": "加密卷被正常受理（未拒绝）", "wall_s": 0.4}}],
    }

    class Args:
        env_label = "t-env"
        target_label = "t-real"
        api_base = "http://x"
    md = run_eval.render_e2e_markdown(result, Args())
    assert md.startswith("---") and "一句话诊断" in md
    assert "稳健性（真实案卷，无标注不评效果）" in md  # 真实子集专节
    assert "accepted_should_reject" in md and "应明确拒绝却被受理" in md  # 缺陷进行动项
    assert "分类型 P/R/F1" not in md  # 无 GT 不渲染效果明细
    assert "结论与行动" in md


def test_merge_file_digital_sums_and_keeps_tuple_detail():
    """I-A/I-B：总体数字保真=逐文件桶求和（与分文件对账），明细保持元组格式可渲染。"""
    def bucket(exact, near, miss, detail):
        return {"total_gt": exact + near + miss, "exact": exact, "near_miss": near, "miss": miss,
                "near_miss_detail": detail, "miss_detail": [], "exact_rate": 0.0}
    shared = ("138 0013 8000", "13800138000")
    per_file = [
        {"digital": {"电话": bucket(5, 1, 1, [shared])}},
        {"digital": {"电话": bucket(3, 1, 2, [shared, ("139 1111", "1391111")])}},
    ]
    merged = run_eval.merge_file_digital(per_file)
    s = merged["电话"]
    assert (s["exact"], s["near_miss"], s["miss"], s["total_gt"]) == (8, 2, 3, 13)  # 求和对账
    assert s["exact_rate"] == 8 / 13
    assert s["near_miss_detail"] == [shared, ("139 1111", "1391111")]  # 元组去重，非 dict


def test_render_e2e_markdown_smoke():
    result = {
        "overall": {"overall": {"precision": 1.0, "recall": 0.5, "f1": 0.667, "tp": 1, "fp": 0, "fn": 1},
                    "per_type": {"姓名": {"precision": 1.0, "recall": 0.5, "f1": 0.667, "tp": 1, "fp": 0, "fn": 1}},
                    "digital": {"电话": {"exact": 1, "near_miss": 1, "miss": 0, "exact_rate": 0.5,
                                        "total_gt": 2,
                                        "near_miss_detail": [("138 0013 8000", "13800138000")],
                                        "miss_detail": []}},
                    "digital_gate": {"pass": False, "failures": ["电话"]},
                    "loose": {"wrong_type": 2, "type_confusion_top": {"姓名→机构名称": 2}}},
        "per_file": [{
            "file": {"id": "x", "carrier": "txt", "gt_entities": 2},
            "overall": {"precision": 1.0, "recall": 0.5, "f1": 0.667, "tp": 1, "fp": 0, "fn": 1},
            "digital_gate": {"pass": False, "failures": []},
            "perf": {"steady_pages": 3, "wall_s": {"total": 1.0, "p50": 2.0, "p95": 3.0},
                     "throughput_pages_per_min": 60,
                     "duration_ms": {"ocr": {"mean": 100.0, "p95": 120.0}},
                     "pages_detail": [{"warmup": False, "wall_s": 2.0, "entities": {"姓名": ["张"]}}]}}],
        "failed": [{"id": "y", "error": "RuntimeError: demo"}],
    }

    class Args:
        env_label = "t-env"
        target_label = "t-target"
        api_base = "http://x"
    md = run_eval.render_e2e_markdown(result, Args())
    assert md.startswith("---") and "tags:" in md  # Obsidian frontmatter
    assert "一句话诊断" in md and "发布冻结" in md  # 诊断结论先行（含决策）
    assert "健康度速览" in md and "四信号" in md  # 管理者四信号
    assert "[!danger]" in md and "预算消耗" in md  # SLO 预算隐喻
    assert "指标翻译" in md and "结论与行动" in md  # 通俗翻译 + 行动项
    assert "[!example]-" in md  # 明细折叠


