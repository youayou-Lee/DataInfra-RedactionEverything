# backend/tests/test_benchmark_t2_gen.py
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "eval" / "benchmarks" / "t2"))
import gen_failure_buckets as gen  # noqa: E402

DIGITAL_TYPES = {"身份证号", "护照号", "电话", "银行卡号"}


def test_digit_confusion_has_all_four_types():
    entry = gen.build_entry("digit-confusion", 0)
    assert set(entry["entities"]) >= {"身份证号", "电话", "银行卡号"}
    assert entry["bucket_kind"] == "synthetic"


def test_gt_in_text_and_deterministic():
    for bucket in ("digit-confusion", "quoted-entity", "long-entity", "lowfreq-type", "context-distractor"):
        for i in (0, 7, 49):
            e = gen.build_entry(bucket, i)
            for t, vals in e["entities"].items():
                for v in vals:
                    assert v in e["text"], f"{bucket}/{i}: {t}:{v} 不在文中"


def test_deterministic():
    import json
    a = json.dumps(gen.build_bucket("digit-confusion", 10), ensure_ascii=False)
    b = json.dumps(gen.build_bucket("digit-confusion", 10), ensure_ascii=False)
    assert a == b
