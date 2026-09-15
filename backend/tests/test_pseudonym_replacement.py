"""化名（pseudonym）替换核心引擎测试 — Issue #7。

覆盖：全文一致性、词池耗尽（numbered/cycle）、类型匹配、格式虚构号合法性
（身份证校验位 / 手机号段 / 银行卡 Luhn）、精确映射优先级、无词池回退。
"""

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
        total = sum(int(c) * w for c, w in zip(body, weights, strict=False))
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
    # 默认词池可读（出厂 derived 策略）
    merged = word_pool_service.load_word_pools()
    assert merged["PERSON"]["strategy"] == "derived"
    # 租户覆盖 + 回退
    svc = word_pool_service
    svc.update_word_pool("PERSON", svc.WordPoolUpdate(
        words=["赵大", "钱二"], strategy="cycle", custom_map={"老王": "老李"}), owner_id="u1")
    view = svc.list_word_pools(owner_id="u1")
    assert view["merged"]["PERSON"]["words"] == ["赵大", "钱二"]
    assert svc.load_word_pools(owner_id="u1")["PERSON"]["custom_map"]["老王"] == "老李"
    # 其它租户不受影响
    assert svc.load_word_pools(owner_id="u2")["PERSON"]["strategy"] == "derived"
    # 导入导出
    exported = svc.export_word_pools(owner_id="u1")
    count = svc.import_word_pools(exported, owner_id="u3")
    assert count >= 1
    assert svc.load_word_pools(owner_id="u3")["PERSON"]["custom_map"]["老王"] == "老李"
    # 删除覆盖回退默认
    assert svc.delete_word_pool("PERSON", owner_id="u1") is True
    assert svc.load_word_pools(owner_id="u1")["PERSON"]["strategy"] == "derived"


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
    """机关精化池的 custom_map 命中后登记占用，跨池不误伤。"""
    pools = {
        "INSTITUTION_NAME": {"words": ["某公司"], "strategy": "numbered", "custom_map": {}},
        "GOVERNMENT_AGENCY": {"words": ["某局"], "strategy": "numbered",
                              "custom_map": {"某市公安局": "某公安局"}},
    }
    ctx = _ctx(pools)
    a = ctx.get_replacement(_entity("某市公安局", type_="INSTITUTION_NAME"))
    b = ctx.get_replacement(_entity("某县自然资源局", type_="INSTITUTION_NAME"))
    c = ctx.get_replacement(_entity("某科技有限公司", type_="INSTITUTION_NAME"))
    assert a == "某公安局"          # 机关池 custom_map 精确映射（非池词）
    assert b == "某局"              # 某公安局已被占用，机关池顺延
    assert c == "某公司"            # 公司池不受机关池占用影响


def test_gov_text_refinement_no_false_positive():
    """名字含机关词但以公司后缀结尾的，留在公司池。"""
    pools = {
        "INSTITUTION_NAME": {"words": ["某公司"], "strategy": "numbered", "custom_map": {}},
        "GOVERNMENT_AGENCY": {"words": ["某公安局"], "strategy": "numbered", "custom_map": {}},
    }
    ctx = _ctx(pools)
    assert ctx.get_replacement(_entity("某司法鉴定服务有限公司", type_="INSTITUTION_NAME")) == "某公司"
    assert ctx.get_replacement(_entity("海关咨询有限公司", type_="INSTITUTION_NAME")) == "某公司1"  # 池耗尽顺延编号，仍在公司池
    assert ctx.get_replacement(_entity("某县公安局", type_="INSTITUTION_NAME")) == "某公安局"


def test_base_pool_custom_map_fallback_for_refined_text():
    """精化池没有该原文的 custom_map 时，回退查基座池的存量映射（数据兼容）。"""
    pools = {
        "INSTITUTION_NAME": {"words": ["某公司"], "strategy": "numbered",
                             "custom_map": {"某市公安局": "自定义机关甲"}},
        "GOVERNMENT_AGENCY": {"words": ["某公安局"], "strategy": "numbered", "custom_map": {}},
    }
    ctx = _ctx(pools)
    assert ctx.get_replacement(_entity("某市公安局", type_="INSTITUTION_NAME")) == "自定义机关甲"


# ---------- derived 策略：司法编号式化名（张某1/某公司1，Issue #6 T2） ----------

DERIVED_POOLS = {
    "PERSON": {"words": [], "strategy": "derived", "custom_map": {}},
    "INSTITUTION_NAME": {"words": [], "strategy": "derived", "custom_map": {}},
    "GOVERNMENT_AGENCY": {"words": [], "strategy": "derived", "custom_map": {}},
    "BANK_NAME": {"words": [], "strategy": "derived", "custom_map": {}},
}


def test_derived_person_surname_and_per_surname_seq():
    """人名取原文姓氏 + 某 + 同姓独立序号：陈明飞→陈某1、陈成龙→陈某2、李四光→李某1。"""
    ctx = _ctx(DERIVED_POOLS)
    assert ctx.get_replacement(_entity("陈明飞", coref="c1")) == "陈某1"
    assert ctx.get_replacement(_entity("陈成龙", coref="c2")) == "陈某2"
    assert ctx.get_replacement(_entity("李四光", coref="c3")) == "李某1"
    # 同一原文全文一致（含 coref 混排）
    assert ctx.get_replacement(_entity("陈明飞", coref="c9")) == "陈某1"


def test_derived_person_non_cjk_fallback():
    """非中文人名无法取姓氏时回退「某人N」。"""
    ctx = _ctx(DERIVED_POOLS)
    assert ctx.get_replacement(_entity("John Smith", coref="c1")) == "某人1"
    assert ctx.get_replacement(_entity("A001", coref="c2")) == "某人2"


def test_derived_person_text_collision_bumped():
    """原文恰好等于将生成的编号词时顺延，不能原样替换（陈某1→陈某2）。"""
    ctx = _ctx(DERIVED_POOLS)
    assert ctx.get_replacement(_entity("陈某1", coref="c1")) == "陈某2"


def test_derived_institution_numbered():
    ctx = _ctx(DERIVED_POOLS)
    assert ctx.get_replacement(_entity("宏图贸易有限公司", "INSTITUTION_NAME", "c1")) == "某公司1"
    assert ctx.get_replacement(_entity("星辰科技", "INSTITUTION_NAME", "c2")) == "某公司2"


def test_derived_gov_keyword_and_seq():
    """机关按名称后缀关键词派生基名并按关键词独立编号。"""
    ctx = _ctx(DERIVED_POOLS)
    assert ctx.get_replacement(_entity("某市公安局", "INSTITUTION_NAME", "c1")) == "某公安局1"
    assert ctx.get_replacement(_entity("乙县公安局", "INSTITUTION_NAME", "c2")) == "某公安局2"
    assert ctx.get_replacement(_entity("某县人民法院", "INSTITUTION_NAME", "c3")) == "某人民法院1"
    # 委员会结尾：池键仍归公司池（存量池精化不变），仅派生基名按委员会取
    assert ctx.get_replacement(_entity("某市监察委员会", "INSTITUTION_NAME", "c4")) == "某委员会1"
    assert ctx.get_replacement(_entity("某区人大常务委员会", "INSTITUTION_NAME", "c5")) == "某委员会2"


def test_derived_person_empty_and_single_char():
    """空文本/单字人名：无法可靠取姓时回退「某人N」，单字姓氏照常派生。"""
    ctx = _ctx(DERIVED_POOLS)
    assert ctx.get_replacement(_entity("", coref="c1")) == "某人1"
    assert ctx.get_replacement(_entity("陈", coref="c2")) == "陈某1"


def test_derived_bank_uniform():
    ctx = _ctx(DERIVED_POOLS)
    assert ctx.get_replacement(_entity("工商银行某支行", "INSTITUTION_NAME", "c1")) == "某银行1"
    assert ctx.get_replacement(_entity("建设银行", "INSTITUTION_NAME", "c2")) == "某银行2"


def test_derived_explicit_mapping_still_wins():
    ctx = _ctx(DERIVED_POOLS)
    ctx.set_custom_replacements({"陈明飞": "甲方代表"})
    assert ctx.get_replacement(_entity("陈明飞")) == "甲方代表"


def test_derived_avoids_reserved_word():
    """显式映射占用的词（含已派生词形）不再发给其他实体。"""
    pools = {"PERSON": {"words": [], "strategy": "derived", "custom_map": {}}}
    ctx = _ctx(pools)
    ctx.set_custom_replacements({"甲某": "陈某1"})
    assert ctx.get_replacement(_entity("甲某")) == "陈某1"
    assert ctx.get_replacement(_entity("陈明飞", coref="c1")) == "陈某2"


def test_default_pools_ship_derived_style():
    """默认词池出厂即 derived：无需配置即得司法编号式化名；租户覆盖仍可换回词池。"""
    from app.services import word_pool_service as svc

    merged = svc.load_word_pools()
    for key in ("PERSON", "INSTITUTION_NAME", "GOVERNMENT_AGENCY", "BANK_NAME"):
        assert merged[key]["strategy"] == "derived"
    ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=merged)
    assert ctx.get_replacement(_entity("陈明飞", coref="c1")) == "陈某1"
    assert ctx.get_replacement(_entity("某县公安局", type_="INSTITUTION_NAME")) == "某公安局1"


# ---------- 组织子类型分池 + 公共机构保留（Issue #55 / #56） ----------

ORG_POOLS = {
    "INSTITUTION_NAME": {"words": ["某公司", "某集团"], "strategy": "numbered", "custom_map": {}},
    "LAW_FIRM": {"words": ["某律师事务所"], "strategy": "numbered", "custom_map": {}},
    "HOSPITAL": {"words": ["某医院"], "strategy": "numbered", "custom_map": {}},
    "SCHOOL": {"words": ["某大学", "某学院", "某学校"], "strategy": "numbered", "custom_map": {}},
}


def test_law_firm_routed_to_firm_pool_not_company():
    ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=ORG_POOLS)
    out = ctx.get_replacement(_entity("北京德恒（南宁）律师事务所", type_="ORG"))
    assert out == "某律师事务所"


def test_org_subtype_pools_by_suffix():
    cases = {
        "广西某律师事务所": ("LEGAL_LAW_FIRM", "某律师事务所"),
        "协和医院": ("ORG", "某医院"),
        "清华大学": ("ORG", "某大学"),
    }
    for text, (type_, expected) in cases.items():
        ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=ORG_POOLS)
        assert ctx.get_replacement(_entity(text, type_=type_)) == expected, text


def test_law_firms_get_distinct_consistent_pseudonyms():
    ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=ORG_POOLS)
    a = ctx.get_replacement(_entity("甲律师事务所", type_="ORG", coref="c1"))
    b = ctx.get_replacement(_entity("乙律师事务所", type_="ORG", coref="c2"))
    assert a != b
    # 同一律所再次出现复用同一化名
    assert ctx.get_replacement(_entity("甲律师事务所", type_="ORG")) == a


def test_company_entities_still_use_institution_pool():
    ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=ORG_POOLS)
    out = ctx.get_replacement(_entity("某某贸易有限公司", type_="ORG"))
    assert out in {"某公司", "某集团"}


def test_subtype_routing_beats_gov_keyword_refinement():
    # 律所原文即使含机关关键词也不落机关池（子类型后缀路由优先）
    ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=ORG_POOLS)
    out = ctx.get_replacement(_entity("某司法局律师事务所", type_="ORG"))
    assert out == "某律师事务所"


def test_public_institutions_preserved_verbatim():
    preserved = [
        "中华人民共和国司法部",
        "司法部",
        "中国律师事务中心",
        "司法所法律援助中心",
        "国家某公证处",
        "最高人民法院",
    ]
    for text in preserved:
        ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=ORG_POOLS)
        assert ctx.get_replacement(_entity(text, type_="GOVERNMENT_AGENCY")) == text, text
        assert ctx.get_replacement(_entity(text, type_="ORG")) == text, text


def test_local_government_agencies_still_anonymized():
    # preview2.0.0 已验收行为：地方机关照常匿名化，不在白名单范围
    for text in ("某市公安局", "南宁市司法局", "南宁市青秀区人民法院"):
        ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=ORG_POOLS)
        assert ctx.get_replacement(_entity(text, type_="ORG")) != text, text


def test_company_named_bu_not_preserved():
    ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=ORG_POOLS)
    out = ctx.get_replacement(_entity("某某贸易部", type_="ORG"))
    assert out != "某某贸易部"


def test_public_institution_rules_apply_only_in_pseudonym_mode():
    from app.models.common import ReplacementMode as RM

    ctx = RedactionContext(RM.MASK, word_pools=ORG_POOLS)
    assert ctx.get_replacement(_entity("司法部", type_="ORG")) == "***"


# ---------- 验收反馈回归：出版物 + 党的机关 + 厅级机关（2026-09-15） ----------


def test_publications_preserved_verbatim():
    # NER 常把出版物误判为机构名称；其名公开、无脱敏必要，保留原文
    for text in (
        "《首席法务杂志》",
        "首席法务杂志",
        "《首席法务官》杂志",
        "《亚洲法律杂志》",
        "亚洲法律概况",
        "2020亚太法律指南",
        "《商法》",
        "最高人民法院公报",
    ):
        ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=ORG_POOLS)
        assert ctx.get_replacement(_entity(text, type_="ORG")) == text, text


def test_central_party_organs_preserved_local_organs_anonymized():
    # 中央级机关保留原文（对齐司法部口径）
    for text in ("中共中央组织部", "中共中央宣传部"):
        ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=ORG_POOLS)
        assert ctx.get_replacement(_entity(text, type_="ORG")) == text, text


def test_provincial_organs_anonymized_with_matched_type():
    # 厅级/地方党政机关照常匿名化，但派生基名匹配机关类型，不落「某公司」
    cases = {
        "广西司法厅": "某司法厅1",
        "广西壮族自治区司法厅": "某司法厅1",
        "某省委组织部": "某组织部1",
        "某市政法委": "某政法委1",
        "某县委宣传部": "某宣传部1",
    }
    for text, expected in cases.items():
        pools = {
            **ORG_POOLS,
            "GOVERNMENT_AGENCY": {"words": [], "strategy": "derived", "custom_map": {}},
        }
        ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=pools)
        assert ctx.get_replacement(_entity(text, type_="ORG")) == expected, text


def test_publication_and_party_rules_negative_controls():
    # 经营主体不因后缀相近被误保留
    for text in (
        "某某出版集团有限公司",
        "某某贸易有限公司",
        "某某贸易部",
    ):
        ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=ORG_POOLS)
        out = ctx.get_replacement(_entity(text, type_="ORG"))
        assert out != text, text


# ---------- 验收反馈第二轮：国际组织/国资委/公共服务机构/媒体（2026-09-15） ----------


def test_international_orgs_preserved_verbatim():
    for text in ("欧盟", "欧洲联盟", "联合国开发计划署", "世界贸易组织"):
        ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=ORG_POOLS)
        assert ctx.get_replacement(_entity(text, type_="ORG")) == text, text


def test_sasac_routed_to_organ_pseudonym():
    cases = {
        "国资委": "某国资委1",
        "广西区国资委": "某国资委1",
        "南宁市国资委": "某国资委1",
    }
    for text, expected in cases.items():
        pools = {
            **ORG_POOLS,
            "GOVERNMENT_AGENCY": {"words": [], "strategy": "derived", "custom_map": {}},
        }
        ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=pools)
        assert ctx.get_replacement(_entity(text, type_="ORG")) == expected, text


def test_public_service_tail_strips_leading_region():
    cases = {
        "广西区政府顾问人才库": "某政府顾问人才库1",
        "某市法学会": "某法学会1",
        "南宁市公共资源交易中心": "某公共资源交易中心1",
    }
    pools = {
        **ORG_POOLS,
        "INSTITUTION_NAME": {"words": [], "strategy": "derived", "custom_map": {}},
    }
    for text, expected in cases.items():
        ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=pools)
        assert ctx.get_replacement(_entity(text, type_="ORG")) == expected, text


def test_media_and_websites_preserved_verbatim():
    for text in ("人民网", "中国采购与招标网", "广西新闻网", "某省电视台"):
        ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=ORG_POOLS)
        assert ctx.get_replacement(_entity(text, type_="ORG")) == text, text


def test_public_service_rules_negative_controls():
    # 公司不因词尾相近被误改基名
    pools = {
        **ORG_POOLS,
        "INSTITUTION_NAME": {"words": [], "strategy": "derived", "custom_map": {}},
    }
    ctx = RedactionContext(ReplacementMode.PSEUDONYM, word_pools=pools)
    assert ctx.get_replacement(_entity("某某贸易有限公司", type_="ORG")) == "某公司1"
    assert ctx.get_replacement(_entity("某某建工集团", type_="ORG")) == "某公司2"
