"""Issue #23 轴A：HaS NER 类型语义分组（HAS_NER_TYPE_GROUPING=semantic）。

只测批次划分语义（_iter_ner_type_batches / _semantic_type_batches）：
- 默认 9 类拆 G1/G2/G3 三批，互不相交、并集完备；
- 未映射 builtin 与 custom 类型落入兜底批；
- off（默认）与现状逐字节一致（单批）；
- 组内超限时组内自适应再拆；
- 无一命中映射时回退现状分批。
"""

import pytest

from app.core.config import settings
from app.services import has_service as has_service_module
from app.services.has_service import HaSService
from app.services.entity_type_service import EntityTypeConfig


DEFAULT_NINE = [
    ("PERSON", "姓名"), ("ID_CARD", "身份证号"), ("PASSPORT", "护照号"),
    ("PHONE", "电话"), ("EMAIL", "邮箱"), ("ADDRESS", "地址"),
    ("BANK_CARD", "银行卡号"), ("INSTITUTION_NAME", "机构名称"), ("DATE", "日期"),
]


def make_types(pairs):
    return [EntityTypeConfig(id=tid, name=name) for tid, name in pairs]


def batch_names(service, batches):
    return [service._convert_entity_types_to_chinese(batch) for batch in batches]


@pytest.fixture
def service():
    return HaSService(base_url="http://127.0.0.1:18080/v1")


def test_semantic_groups_default_nine_types(service, monkeypatch):
    monkeypatch.setattr(settings, "HAS_NER_TYPE_GROUPING", "semantic")
    batches = service._iter_ner_type_batches(make_types(DEFAULT_NINE))
    names = batch_names(service, batches)
    assert names == [
        ["姓名", "机构名称"],
        ["身份证号", "护照号", "电话", "邮箱", "银行卡号"],  # 组内保持勾选顺序
        ["地址", "日期"],
    ]
    flat = [t for batch in batches for t in batch]
    assert len(flat) == 9  # 并集完备、无重复


def test_semantic_grouping_off_keeps_current_single_batch(service):
    assert settings.HAS_NER_TYPE_GROUPING == "off"  # 默认关闭 = 零行为变更
    batches = service._iter_ner_type_batches(make_types(DEFAULT_NINE))
    assert len(batches) == 1 and len(batches[0]) == 9


def test_semantic_unmapped_builtin_and_custom_go_to_tail(service, monkeypatch):
    monkeypatch.setattr(settings, "HAS_NER_TYPE_GROUPING", "semantic")
    types = make_types([
        ("PERSON", "姓名"), ("SOCIAL_SECURITY", "社保号"),
        ("custom_warranty_no", "质保号"),
    ])
    batches = service._iter_ner_type_batches(types)
    names = batch_names(service, batches)
    assert names[0] == ["姓名"]
    assert sorted(names[1] + names[2]) == ["社保号", "质保号"]  # 兜底批（builtin 与 custom 各一批）


def test_semantic_group_splits_partial_overlap(service, monkeypatch):
    monkeypatch.setattr(settings, "HAS_NER_TYPE_GROUPING", "semantic")
    batches = service._iter_ner_type_batches(make_types([("PHONE", "电话"), ("ADDRESS", "地址")]))
    assert batch_names(service, batches) == [["电话"], ["地址"]]


def test_semantic_none_matched_falls_back(service, monkeypatch):
    monkeypatch.setattr(settings, "HAS_NER_TYPE_GROUPING", "semantic")
    types = make_types([("SOCIAL_SECURITY", "社保号"), ("PLATE_NUMBER", "车牌号")])
    assert service._semantic_type_batches(types) is None
    batches = service._iter_ner_type_batches(types)
    assert len(batches) == 1 and len(batches[0]) == 2  # 回退现状：预算内单批


def test_semantic_group_over_limit_splits_within_group(service, monkeypatch):
    monkeypatch.setattr(settings, "HAS_NER_TYPE_GROUPING", "semantic")
    monkeypatch.setattr(settings, "HAS_NER_MAX_TYPES_PER_REQUEST", 2)
    batches = service._iter_ner_type_batches(make_types(DEFAULT_NINE))
    names = batch_names(service, batches)
    assert names[0] == ["姓名", "机构名称"]
    # G2 五类被 max_types=2 切成 3 批（2+2+1）
    g2_batches = [n for n in names if set(n) <= {"身份证号", "护照号", "电话", "银行卡号", "邮箱"} and n]
    assert sorted(t for n in g2_batches for t in n) == sorted(["身份证号", "护照号", "电话", "银行卡号", "邮箱"])
    assert len(g2_batches) == 3


def test_semantic_group_map_ids_match_registry():
    # 映射表里的 id 必须真实存在于预置清单（防拼写漂移）
    import json
    from pathlib import Path
    preset_path = Path(__file__).resolve().parents[1] / "config" / "preset_entity_types.json"
    preset_ids = set(json.loads(preset_path.read_text(encoding="utf-8")).keys())
    for type_id in has_service_module._NER_SEMANTIC_TYPE_GROUPS:
        assert type_id in preset_ids, f"{type_id} 不在 preset_entity_types.json 中"


def test_semantic_groups_are_disjoint():
    groups = has_service_module._NER_SEMANTIC_TYPE_GROUPS
    g1 = {t for t, g in groups.items() if g == "g1"}
    g2 = {t for t, g in groups.items() if g == "g2"}
    g3 = {t for t, g in groups.items() if g == "g3"}
    assert g1 and g2 and g3
    assert not (g1 & g2 or g1 & g3 or g2 & g3)
