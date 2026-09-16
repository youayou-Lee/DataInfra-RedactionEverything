"""DATE 正则保证层（#66 复验反馈）：
HaS 漏检的日期（如跨行/普通正文书写日期）在识别阶段由内置正则兜底，
不再只在执行侧兜。Stage 2 扩展为内置 regex 类型 + 与 HaS 结果去重。
"""

import pytest
from app.models.schemas import Entity
from app.services.hybrid_ner_service import HybridNERService


class _Type:
    def __init__(self, id: str, regex_pattern: str | None = None):
        self.id = id
        self.regex_pattern = regex_pattern
        self.enabled = True
        self.use_llm = True
        self.name = id


def _service() -> HybridNERService:
    return HybridNERService(has_service_instance=None)


def _ent(text: str, start: int, end: int, type_id: str = "DATE", source: str = "has") -> Entity:
    return Entity(id=f"e_{type_id}_{start}", text=text, type=type_id, start=start, end=end, page=1, source=source)


def test_select_regex_types_includes_builtin_with_pattern():
    types = [
        _Type("PERSON"),  # 无正则 → 不选
        _Type("DATE", r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"),
        _Type("MY_CUSTOM_TYPE", r"FOO\d+"),  # 自定义类型仍被选
    ]
    selected = _service()._select_regex_types(types)
    ids = [t.id for t in selected]
    assert ids == ["DATE", "MY_CUSTOM_TYPE"], "内置带正则类型与自定义类型都应入选"


def test_regex_fallback_catches_date_has_missed(monkeypatch):
    """HaS 漏检跨行日期时，正则保证层补出实体（含换行的匹配文本）。"""
    svc = _service()
    text = "我院于2022 年01 月25 日受理；于2022 年1 月\n26 日告知被告人"
    types = [_Type("DATE", r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")]

    async def fake_has(**kwargs):  # HaS 本轮全漏
        return []

    monkeypatch.setattr(svc, "_select_has_semantic_types", lambda types: types)
    monkeypatch.setattr(svc.has_service, "is_available", lambda: False)

    entities = svc._custom_regex_extract(text, types)
    texts = [e.text for e in entities]
    assert any("2022 年01 月25 日" in t for t in texts)
    assert any("2022 年1 月\n26 日" in t for t in texts), "跨行日期应由正则命中"
    assert all(e.source == "regex" for e in entities)


def test_drop_overlapping_keeps_has_and_missed_regex():
    """与 HaS 已有实体重叠的正则命中丢弃（不重复出框）；未重叠的保留。"""
    existing = [_ent("2022 年1 月25 日", 0, 12)]
    regex_hits = [
        _ent("2022 年1 月25 日", 0, 12, source="regex"),   # 与 has 重叠 → 丢
        _ent("2022 年1 月\n26 日", 40, 53, source="regex"),  # 无重叠 → 留
    ]
    kept = HybridNERService._drop_overlapping(regex_hits, existing)
    assert [e.text for e in kept] == ["2022 年1 月\n26 日"]
