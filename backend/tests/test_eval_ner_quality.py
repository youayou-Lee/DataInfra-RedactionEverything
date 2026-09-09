"""Issue #23 NER 质量评测工具（backend/scripts/eval/）纯函数单测。

评测脚本不在 app 包里，按文件路径 import（仿 test_lb_least_inflight.py）。
另含一条防漂移契约：评测脚本 prompt 必须与生产 has_client.py 模板逐字一致。
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "backend" / "scripts" / "eval"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


eval_mod = _load("eval_ner_quality_under_test", EVAL_DIR / "eval_ner_quality.py")
corpus_mod = _load("make_ner_gt_corpus_under_test", EVAL_DIR / "make_ner_gt_corpus.py")


# ---------- make_ner_gt_corpus ----------

def test_corpus_gt_entities_verbatim_in_text():
    for page_id in range(10):
        page = corpus_mod.build_page(page_id)
        for etype, values in page["entities"].items():
            for value in values:
                assert value in page["text"], f"page {page_id} {etype}: {value!r} 不在正文中"


def test_corpus_deterministic():
    first = [corpus_mod.build_page(i) for i in range(10)]
    second = [corpus_mod.build_page(i) for i in range(10)]
    assert first == second


def test_corpus_id_card_checksum():
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    for seq in range(30):
        card = corpus_mod.id_card(seq)
        assert len(card) == 18
        expect = "10X98765432"[sum(int(d) * w for d, w in zip(card[:17], weights)) % 11]
        assert card[17] == expect


def test_corpus_passport_only_on_even_pages():
    for page_id in range(10):
        page = corpus_mod.build_page(page_id)
        assert (("护照号" in page["entities"]) == (page_id % 2 == 0))


def test_corpus_covers_all_default_types():
    seen = set()
    for page_id in range(10):
        seen |= set(corpus_mod.build_page(page_id)["entities"])
    assert set(corpus_mod.DEFAULT_TYPES) <= seen


# ---------- eval_ner_quality：prompt 契约 ----------

def test_prompt_matches_production_has_client_template():
    """评测 prompt 必须与 backend/app/services/has_client.py 的模型卡模板逐字一致（防漂移）。"""
    source = (REPO_ROOT / "backend" / "app" / "services" / "has_client.py").read_text(encoding="utf-8")
    match = re.search(r'prompt = f"""(Recognize the following entity types.*?)"""', source, re.DOTALL)
    assert match, "has_client.py 中找不到 NER prompt 模板（模板是否被改动？请同步 eval 脚本）"
    template = match.group(1)

    types = ["姓名", "电话"]
    text = "张三的电话是13800000000。"
    types_str = json.dumps(types, ensure_ascii=False, separators=(",", ":"))
    rendered = template.replace("{{}}", "{}").replace("{types_str}", types_str) \
        .replace("{guidance_block}", "").replace("{text}", text)
    assert eval_mod.build_ner_prompt(text, types) == rendered


def test_prompt_types_no_space_and_empty_guidance_blank_line():
    prompt = eval_mod.build_ner_prompt("正文", ["姓名"])
    assert 'Specified types:["姓名"]\n\nReturn strict JSON only.' in prompt  # 冒号后无空格；空 guidance 留空行
    assert prompt.endswith("<text>正文</text>")


# ---------- eval_ner_quality：解析 ----------

def test_parse_model_json_variants():
    assert eval_mod.parse_model_json('{"姓名": ["张三"]}') == {"姓名": ["张三"]}
    assert eval_mod.parse_model_json('```json\n{"姓名": ["张三"]}\n```') == {"姓名": ["张三"]}
    assert eval_mod.parse_model_json('结果如下：{"电话": ["138"]} 以上。') == {"电话": ["138"]}
    assert eval_mod.parse_model_json("") == {}
    assert eval_mod.parse_model_json("模型跑偏了没有 JSON") == {}


# ---------- eval_ner_quality：分组 ----------

def test_group_types_default_nine_types():
    groups = eval_mod.group_types(corpus_mod.DEFAULT_TYPES)
    assert [set(g) for g in groups] == [{"姓名", "机构名称"},
                                        {"身份证号", "护照号", "电话", "银行卡号", "邮箱"},
                                        {"地址", "日期"}]
    assert sorted(t for g in groups for t in g) == sorted(corpus_mod.DEFAULT_TYPES)  # 无遗漏无重复


def test_group_types_unknown_types_go_to_tail_batch():
    groups = eval_mod.group_types(["姓名", "自定义A", "地址", "自定义B"])
    assert groups == [["姓名"], ["地址"], ["自定义A", "自定义B"]]


# ---------- eval_ner_quality：指标与闸门 ----------

def test_grade_digital_levels():
    graded = eval_mod.grade_digital(
        ["110101199001011234", "138 0000 0000"],
        ["110101199001011234", "13800000000", "13911112222"],
    )
    assert graded["exact"] == ["110101199001011234"]
    assert graded["near_miss"] == [("138 0000 0000", "13800000000")]  # 仅空格差异
    assert graded["miss"] == []


def test_compute_metrics_set_semantics():
    records = [{
        "page_id": 0,
        "gt": {"姓名": ["张三", "李四"], "电话": ["13800000000"]},
        "pred": {"姓名": ["张三", "王五"], "电话": ["1380000000"]},  # 李四漏、王五多、电话错一位
        "latency_sec": 1.0,
    }]
    metrics = eval_mod.compute_metrics(records)
    assert metrics["overall"]["tp"] == 1 and metrics["overall"]["fp"] == 2 and metrics["overall"]["fn"] == 2
    assert abs(metrics["overall"]["precision"] - 1 / 3) < 1e-9
    assert abs(metrics["overall"]["recall"] - 1 / 3) < 1e-9
    # 电话是闸门类型：GT 一条既非 exact 也非 near_miss → miss，exact_rate=0
    assert metrics["digital"]["电话"]["exact_rate"] == 0.0
    assert metrics["digital"]["电话"]["miss"] == 1
    assert metrics["digital"]["电话"]["miss_detail"] == ["13800000000"]


def test_digital_gate_and_comparison():
    baseline = {"overall": {"recall": 0.90, "precision": 0.95}}
    perfect = {"overall": {"recall": 0.895, "precision": 0.95},
               "digital": {"身份证号": {"exact_rate": 1.0}, "护照号": {"exact_rate": 1.0},
                           "电话": {"exact_rate": 1.0}, "银行卡号": {"exact_rate": 1.0}}}
    comparison = eval_mod.compare_with_baseline(perfect, baseline)
    assert comparison["gate_pass"] is True  # 召回 -0.5pp ≥ -1pp、P 持平、数字 100%

    broken = {"overall": {"recall": 0.96, "precision": 0.99},
              "digital": {"身份证号": {"exact_rate": 1.0}, "护照号": {"exact_rate": 1.0},
                          "电话": {"exact_rate": 0.5, "total_gt": 2, "exact": 1,
                                   "near_miss": 1, "miss": 0}, "银行卡号": {"exact_rate": 1.0}}}
    comparison = eval_mod.compare_with_baseline(broken, baseline)
    assert comparison["gate_pass"] is False  # 数字逐字非 100% 一票否决
    assert any("电话" in f for f in comparison["digital_gate_failures"])
