# backend/tests/test_benchmark_t2_runner.py
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "eval" / "benchmarks" / "t2"))
import benchmark_t2 as rt  # noqa: E402


def make_perfect_engine(entries, name="echo"):
    """闭包持有 entries：predict 返回对应 GT（完美预测，用于编排验证）。"""
    by_text = {e["text"]: e["entities"] for e in entries}

    class PerfectEngine:
        supports = None  # None = 全支持

        async def predict(self, text, types):
            return {t: list(by_text[text].get(t, [])) for t in types}

    eng = PerfectEngine()
    eng.name = name
    return eng


class NaEngine:
    name = "partial"
    supports = {"hardcase"}  # 只申报 hardcase

    async def predict(self, text, types):
        return {}


ENTRIES = [
    {"id": "a", "bucket": "cluener-person", "bucket_kind": "public", "source": "cluener",
     "input_modality": "text", "text": "张三在朝阳。", "entities": {"姓名": ["张三"]}},
    {"id": "b", "bucket": "hardcase", "bucket_kind": "hardcase", "source": "ingest",
     "input_modality": "text", "text": "李四电话13800000000。", "entities": {"姓名": ["李四"], "电话": ["13800000000"]}},
]


def test_run_engines_per_bucket_metrics():
    result = rt.run_engines(ENTRIES, engines={"echo": make_perfect_engine(ENTRIES)})
    assert set(result["buckets"]) == {"cluener-person", "hardcase"}
    assert set(result["engines"]) == {"echo"}
    assert result["buckets"]["cluener-person"]["echo"]["overall"]["recall"] == 1.0
    assert result["buckets"]["hardcase"]["echo"]["overall"]["recall"] == 1.0


def test_engine_na_bucket():
    result = rt.run_engines(ENTRIES, engines={"partial": NaEngine()})
    assert result["buckets"]["cluener-person"]["partial"] == "N/A"
    assert result["buckets"]["hardcase"]["partial"]["overall"]["recall"] == 0.0


def test_render_report_markdown_table():
    result = rt.run_engines(ENTRIES, engines={"echo": make_perfect_engine(ENTRIES),
                                              "partial": NaEngine()})
    md = rt.render_report(result)
    assert "| 桶 | 引擎 | P | R | F1 | 数字exact |" in md
    assert "| cluener-person | echo | 1.0000 | 1.0000 | 1.0000 |" in md
    assert "| cluener-person | partial | N/A | N/A | N/A | N/A |" in md


def test_load_entries_filters_buckets(tmp_path):
    d = tmp_path / "b1"
    d.mkdir()
    (d / "x.jsonl").write_text(
        "\n".join(json_dumps(e) for e in ENTRIES) + "\n", encoding="utf-8")
    assert len(rt.load_entries(tmp_path, None)) == 2
    assert [e["id"] for e in rt.load_entries(tmp_path, ["hardcase"])] == ["b"]


def json_dumps(e):
    import json
    return json.dumps(e, ensure_ascii=False)
