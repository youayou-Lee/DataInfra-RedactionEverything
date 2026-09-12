"""化名（pseudonym）替换核心引擎测试 — Issue #7。

覆盖：全文一致性、词池耗尽（numbered/cycle）、类型匹配、格式虚构号合法性
（身份证校验位 / 手机号段 / 银行卡 Luhn）、精确映射优先级、无词池回退。
"""

import pytest

from app.models.common import ReplacementMode
from app.models.entity_schemas import Entity
from app.services.redaction.replacement_strategy import (
    RedactionContext,
    _fictional_bank_card,
    _fictional_id_card,
    _fictional_license_plate,
    _fictional_phone,
)

POOLS = {
    "PERSON": {
        "words": ["张三", "李四"],
        "strategy": "numbered",
        "custom_map": {"陈明飞": "王某"},
    },
    "INSTITUTION_NAME": {
        "words": ["某公司", "某集团"],
        "strategy": "numbered",
        "custom_map": {},
    },
}


def _entity(text: str, type_: str = "PERSON", coref: str | None = None) -> Entity:
    return Entity(
        id=f"e-{text}", text=text, type=type_, start=0, end=len(text),
        page=1, source="regex", coref_id=coref,
    )


def _ctx(pools: dict | None = POOLS) -> RedactionContext:
    return RedactionContext(ReplacementMode.PSEUDONYM, word_pools=pools)


def test_same_entity_consistent_across_document():
    ctx = _ctx()
    # 同一实体（不同 coref 键路径）：文本键与 coref 键都应得到同一个替换词
    a = ctx.get_replacement(_entity("陈明飞"))
    b = ctx.get_replacement(_entity("陈明飞", coref="coref-1"))
    c = ctx.get_replacement(_entity("陈明飞", coref="coref-1"))
    assert a == b == c
    assert ctx.entity_map["陈明飞"] == a


def test_pool_exhaustion_numbered():
    ctx = _ctx()
    r1 = ctx.get_replacement(_entity("甲", coref="c1"))
    r2 = ctx.get_replacement(_entity("乙", coref="c2"))
    r3 = ctx.get_replacement(_entity("丙", coref="c3"))
    r4 = ctx.get_replacement(_entity("丁", coref="c4"))
    assert [r1, r2] == ["张三", "李四"]
    # 耗尽后加序号
    assert r3 == "张三1"
    assert r4 == "李四1"
    # 不同实体不撞词
    assert len({r1, r2, r3, r4}) == 4


def test_pool_exhaustion_cycle():
    pools = {"PERSON": {"words": ["张三"], "strategy": "cycle", "custom_map": {}}}
    ctx = _ctx(pools)
    assert ctx.get_replacement(_entity("甲", coref="c1")) == "张三"
    assert ctx.get_replacement(_entity("乙", coref="c2")) == "张三"


def test_type_aware_no_cross_type_leak():
    ctx = _ctx()
    person = ctx.get_replacement(_entity("甲", "PERSON", "c1"))
    org = ctx.get_replacement(_entity("某贸易公司", "INSTITUTION_NAME", "c2"))
    assert person in ("张三", "李四")
    assert org in ("某公司", "某集团")


def test_exact_mapping_and_custom_replacements_take_priority():
    ctx = _ctx()
    ctx.set_custom_replacements({"陈明飞": "化名甲"})
    assert ctx.get_replacement(_entity("陈明飞")) == "化名甲"

    ctx2 = _ctx()
    assert ctx2.get_replacement(_entity("陈明飞")) == "王某"  # 词池 custom_map


def test_format_fictional_valid():
    ctx = _ctx()
    idc = ctx.get_replacement(_entity("110101199003078515", "ID_CARD", "c1"))
    assert len(idc) == 18 and idc[:6] == "110101"
    # 校验生成器
    for seq in range(1, 30):
        assert len(_fictional_id_card(seq)) == 18
        body, check = _fictional_id_card(seq)[:-1], _fictional_id_card(seq)[-1]
        weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
        total = sum(int(c) * w for c, w in zip(body, weights))
        assert check == "10X98765432"[total % 11]

        phone = _fictional_phone(seq)
        assert len(phone) == 11 and phone[0] == "1" and phone[1] in "3456789"

        card = _fictional_bank_card(seq)
        assert len(card) == 19 and card.startswith("6222")
        # Luhn 复算
        total = 0
        for i, ch in enumerate(reversed(card)):
            d = int(ch)
            if i % 2 == 1:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        assert total % 10 == 0

        plate = _fictional_license_plate(seq)
        assert len(plate) == 7 and plate[0] != plate[1]

    # 生成号也不重复
    ids = {_fictional_id_card(s) for s in range(1, 50)}
    assert len(ids) == 49


def test_format_type_not_from_word_pool():
    ctx = _ctx()
    # ID_CARD 无词池也应生成合法虚构号，而非智能标签
    r = ctx.get_replacement(_entity("13800138000", "PHONE", "c1"))
    assert len(r) == 11 and r != "[电话一]"


def test_no_pool_non_format_falls_back_to_smart():
    ctx = _ctx(pools={})
    r = ctx.get_replacement(_entity("某物", "AMOUNT", "c1"))
    assert r.startswith("[")


def test_pool_type_aliases():
    ctx = _ctx()
    # JUDGE 归并到 PERSON 词池
    r = ctx.get_replacement(_entity("王法官", "JUDGE", "c1"))
    assert r in ("张三", "李四")


def test_word_pool_service_roundtrip(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services import word_pool_service

    monkeypatch.setattr(settings, "WORD_POOL_STORE_PATH", str(tmp_path / "wp.json"))
    # 默认词池可读
    merged = word_pool_service.load_word_pools()
    assert "张三" in merged["PERSON"]["words"]
    # 租户覆盖 + 回退
    svc = word_pool_service
    svc.update_word_pool("PERSON", svc.WordPoolUpdate(
        words=["赵大", "钱二"], strategy="cycle", custom_map={"老王": "老李"}), owner_id="u1")
    view = svc.list_word_pools(owner_id="u1")
    assert view["merged"]["PERSON"]["words"] == ["赵大", "钱二"]
    assert svc.load_word_pools(owner_id="u1")["PERSON"]["custom_map"]["老王"] == "老李"
    # 其它租户不受影响
    assert "张三" in svc.load_word_pools(owner_id="u2")["PERSON"]["words"]
    # 导入导出
    exported = svc.export_word_pools(owner_id="u1")
    count = svc.import_word_pools(exported, owner_id="u3")
    assert count >= 1
    assert svc.load_word_pools(owner_id="u3")["PERSON"]["custom_map"]["老王"] == "老李"
    # 删除覆盖回退默认
    assert svc.delete_word_pool("PERSON", owner_id="u1") is True
    assert "张三" in svc.load_word_pools(owner_id="u1")["PERSON"]["words"]


def test_pool_word_equal_to_entity_text_not_identity_replaced():
    """实体原文恰好在词池里时不能原样替换（张三→李四 而非 张三→张三）。"""
    pools = {"PERSON": {"words": ["张三", "李四"], "strategy": "numbered", "custom_map": {}}}
    ctx = _ctx(pools)
    a = ctx.get_replacement(_entity("张三", coref="c1"))
    b = ctx.get_replacement(_entity("李四", coref="c2"))
    c = ctx.get_replacement(_entity("王五", coref="c3"))
    assert a != "张三" and b != "李四"
    assert len({a, b, c}) == 3


def test_mixed_coref_and_no_coref_consistent():
    """同一原文（无 coref 正则命中 + 有 coref 模型命中混排）必须分到同一个化名。"""
    pools = {"PERSON": {"words": ["张三", "李四"], "strategy": "numbered", "custom_map": {}}}
    ctx = _ctx(pools)
    a = ctx.get_replacement(_entity("王建国"))                     # 无 coref，按文本键
    b = ctx.get_replacement(_entity("王建国", coref="coref-9"))     # 有 coref，不同键
    c = ctx.get_replacement(_entity("王建国"))                      # 回到无 coref
    assert a == b == c
    # 别的实体照常分到下一个词
    d = ctx.get_replacement(_entity("李建国", coref="coref-10"))
    assert d != a


def test_custom_map_word_reserved_from_pool():
    """custom_map 用掉的词要从词池分配中扣除，避免两个实体拿到同一个词。"""
    pools = {"PERSON": {"words": ["张三", "李四"], "strategy": "numbered",
                        "custom_map": {"老王": "张三"}}}
    ctx = _ctx(pools)
    a = ctx.get_replacement(_entity("老王"))
    b = ctx.get_replacement(_entity("甲", coref="c1"))
    c = ctx.get_replacement(_entity("乙", coref="c2"))
    assert a == "张三"
    assert b == "李四"  # 张三已被 custom_map 占用
    assert c == "张三1"


def test_attach_word_pools_normalizes_client_pools():
    from app.models.redaction_schemas import RedactionConfig
    from app.services.redaction_orchestrator import _attach_word_pools

    cfg = RedactionConfig(
        replacement_mode="pseudonym",
        word_pools={"PERSON": {"words": "张三李四", "strategy": "bad"}},
    )
    _attach_word_pools(cfg, "someone")
    # 畸形结构被丢弃，回退加载租户词池（含默认）
    assert isinstance(cfg.word_pools, dict) and "PERSON" in cfg.word_pools

def test_coref_grouping_does_not_merge_different_names():
    """模型共指误组（不同人同组）不得共享化名——化名语义=同一原文同一化名。

    真实案卷实测：NER 把 6 个不同人名标成同一 coref 组，旧逻辑全组共享一个
    化名，用户看到"多个人名映射到同一个名字"。
    """
    pools = {"PERSON": {"words": ["张三", "李四", "王五"], "strategy": "numbered", "custom_map": {}}}
    ctx = _ctx(pools)
    a = ctx.get_replacement(_entity("刘美丽", coref="coref_002"))
    b = ctx.get_replacement(_entity("徐超凡", coref="coref_002"))
    c = ctx.get_replacement(_entity("罗中洲", coref="coref_002"))
    assert len({a, b, c}) == 3
    # 同一原文仍然全文一致（含 coref 混排）
    assert ctx.get_replacement(_entity("刘美丽")) == a
    assert ctx.get_replacement(_entity("刘美丽", coref="coref_002")) == a


def test_government_institution_text_uses_gov_pool():
    """机关类机构文本落机关词池，不落公司词池（公安局→某公安局，非某公司）。"""
    pools = {
        "INSTITUTION_NAME": {"words": ["某公司"], "strategy": "numbered", "custom_map": {}},
        "GOVERNMENT_AGENCY": {
            "words": ["某公安局", "某人民法院"], "strategy": "numbered", "custom_map": {},
        },
    }
    ctx = _ctx(pools)
    assert ctx.get_replacement(_entity("某市公安局", type_="INSTITUTION_NAME")) == "某公安局"
    assert ctx.get_replacement(_entity("某县人民法院", type_="INSTITUTION_NAME")) == "某人民法院"
    # 非机关机构照旧落公司池
    assert ctx.get_replacement(_entity("某科技有限公司", type_="INSTITUTION_NAME")) == "某公司"


def test_bank_institution_text_uses_bank_pool():
    """银行类机构文本落银行词池。"""
    pools = {
        "INSTITUTION_NAME": {"words": ["某公司"], "strategy": "numbered", "custom_map": {}},
        "BANK_NAME": {"words": ["某银行某支行"], "strategy": "numbered", "custom_map": {}},
    }
    ctx = _ctx(pools)
    assert ctx.get_replacement(_entity("工商银行某支行", type_="INSTITUTION_NAME")) == "某银行某支行"


def test_custom_map_reserve_uses_refined_pool_key():
    """custom_map 命中登记占用时按同一精化规则归池，跨子池不误伤。"""
    pools = {
        "INSTITUTION_NAME": {"words": ["某公司"], "strategy": "numbered",
                             "custom_map": {"某市公安局": "某公安局"}},
        "GOVERNMENT_AGENCY": {"words": ["某公安局", "某局"], "strategy": "numbered",
                              "custom_map": {}},
    }
    ctx = _ctx(pools)
    a = ctx.get_replacement(_entity("某市公安局", type_="INSTITUTION_NAME"))
    b = ctx.get_replacement(_entity("某县自然资源局", type_="INSTITUTION_NAME"))
    c = ctx.get_replacement(_entity("某科技有限公司", type_="INSTITUTION_NAME"))
    assert a == "某公安局"          # custom_map 精确映射
    assert b == "某局"              # 某公安局已被占用，机关池顺延（"局$"后缀 → 机关池）
    assert c == "某公司"            # 公司池不受机关池占用影响
