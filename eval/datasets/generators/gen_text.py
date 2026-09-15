"""合成文本语料生成器（Issue #37 评测集）。

按页生成「文书行文本 + GT 实体」；实体生成函数全部复用
backend/scripts/eval/make_ner_gt_corpus.py（校验位/号段/格式合法，D6：import 不复制）。

特性：
  - 确定性：无随机数，全部按索引算术派生，重复运行逐字节一致；
  - GT 自检：每个 GT 实体串必须原样出现在页文本中，否则断言失败；
  - 维度：doc_type（contract/judgment/warrant/bank_statement）× density（sparse/mid/dense）
    + 边界页（blank 空页 / table_only 纯表格页）。

内容为程序合成假数据，不含任何真实案卷信息，可入库。
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "backend" / "scripts" / "eval"))

import make_ner_gt_corpus as base  # noqa: E402

DOC_TYPES = ["contract", "judgment", "warrant", "bank_statement"]
DENSITIES = ["sparse", "mid", "dense"]

DOC_TITLES = {
    "contract": "货款结算与债权确认函",
    "judgment": "民事判决书",
    "warrant": "授权委托书",
    "bank_statement": "银行账户交易流水",
}

_DISRUPT_LINES = [  # 干扰项（格式相似的非目标串）
    "合同编号：HT-2026-{n:04d}（非个人敏感信息）",
    "快递单号：SF1350{n:06d}（文件寄送凭证）",
    "发票代码：044031900{n:03d}，发票号码：1273756{n:02d}。",
]


def _seq(page_id: int, slot: int, lap: int = 0) -> int:
    """确定性实体序号：页 × 槽位 × 轮次的算术派生（dense 轮次用 lap 错开取值域）。"""
    return page_id * 31 + slot * 7 + lap * 10007


def _page_contract(page_id: int, density: str) -> dict:
    laps = {"sparse": 1, "mid": 1, "dense": 3}[density]
    lines: list[str] = []
    entities: dict[str, list[str]] = {}

    def emit(seq: int) -> dict:
        """一轮实体槽 + 对应句子（mid=1 轮 ~12 实体，dense=3 轮）。"""
        a, b, c = base.person_name(seq), base.person_name(seq + 101), base.person_name(seq + 202)
        ida = base.id_card(seq)
        phone_a, phone_b = base.phone(seq), base.phone(seq + 55)
        mail = base.email(seq)
        addr = base.address(seq)
        bank = base.bank_card(seq)
        org_a, org_b = base.org_name(seq), base.org_name(seq + 13)
        d1, d2 = base.date_cn(seq), base.date_iso(seq + 7)
        passport = base.passport_no(seq) if seq % 2 == 0 else None
        block = [
            f"甲方（转让方）：{a}，身份证号：{ida}，联系地址：{addr}。",
            f"乙方（受让方）：{org_a}，法定代表人：{b}，联系电话：{phone_a}，电子邮箱：{mail}。",
        ]
        if passport:
            block.append(f"第三人{c}（护照号：{passport}）作为见证人出席并签字确认。")
        else:
            block.append(f"第三人{c}作为见证人出席并签字确认。")
        block += [
            f"经双方核对，截至{d1}，乙方尚欠甲方货款本金人民币{(seq % 90 + 10) * 1.37:.2f}万元，"
            f"约定于{d2}前一次性汇入甲方指定账户（开户行：中国工商银行某分行，账号：{bank}）。",
            f"若发生争议，双方同意提交{org_b}审理；甲方经办人{a}已于{d2}通过电话{phone_b}告知乙方。",
        ]
        ents = {"姓名": [a, b, c], "身份证号": [ida], "电话": [phone_a, phone_b], "邮箱": [mail],
                "地址": [addr], "银行卡号": [bank], "机构名称": [org_a, org_b], "日期": [d1, d2]}
        if passport:
            ents["护照号"] = [passport]
        return {"block": block, "ents": ents}

    for lap in range(laps):
        chunk = emit(_seq(page_id, 1, lap))
        lines += chunk["block"] + [""]
        for etype, values in chunk["ents"].items():
            entities.setdefault(etype, []).extend(values)

    if density == "sparse":  # 仅合同档位生效：整页裁剪为 ~2 实体
        seq = _seq(page_id, 1)
        first_person = base.person_name(seq)
        phone = base.phone(seq)
        lines = [f"甲方：{first_person}，联系电话：{phone}。", "（本页其余内容与个人信息无关）"]
        entities = {"姓名": [first_person], "电话": [phone]}
        return _finalize(lines, entities)

    lines += [_DISRUPT_LINES[page_id % len(_DISRUPT_LINES)].format(n=page_id * 9173 % 9999)]
    return _finalize(lines, entities)


def _page_judgment(page_id: int, density: str) -> dict:
    laps = {"sparse": 1, "mid": 1, "dense": 3}[density]
    lines = [f"（2026）民初{1000 + page_id:04d}号", ""]
    entities: dict[str, list[str]] = {}
    for lap in range(laps):
        seq = _seq(page_id, 2, lap)
        plaintiff, defendant, agent = base.person_name(seq), base.person_name(seq + 303), base.person_name(seq + 606)
        pid, did = base.id_card(seq), base.id_card(seq + 707)
        addr, phone = base.address(seq), base.phone(seq)
        court = base.COURTS[seq % len(base.COURTS)]
        d1, d2 = base.date_cn(seq), base.date_cn(seq + 11)
        lines += [
            f"原告{plaintiff}（身份证号：{pid}，住{addr}）与被告{defendant}（身份证号：{did}）"
            f"买卖合同纠纷一案，本院于{d1}立案受理。",
            f"被告委托诉讼代理人{agent}，联系电话{phone}。本院依法组成合议庭，于{d2}公开开庭审理。",
            f"本院查明事实后，依照相关法律规定，判决如下：被告{defendant}于本判决生效之日起十日内"
            f"向原告{plaintiff}支付货款人民币{(seq % 50 + 5) * 2.11:.2f}万元。",
            f"如不服本判决，可在判决书送达之日起十五日内向{court}递交上诉状。", "",
        ]
        chunk = {"姓名": [plaintiff, defendant, agent], "身份证号": [pid, did], "电话": [phone],
                 "地址": [addr], "机构名称": [court], "日期": [d1, d2]}
        for etype, values in chunk.items():
            entities.setdefault(etype, []).extend(values)
    return _finalize(lines, entities)


def _page_warrant(page_id: int, density: str) -> dict:
    laps = {"sparse": 1, "mid": 1, "dense": 3}[density]
    lines = []
    entities: dict[str, list[str]] = {}
    for lap in range(laps):
        seq = _seq(page_id, 3, lap)
        principal, agent = base.person_name(seq), base.person_name(seq + 404)
        pid, aid = base.id_card(seq), base.id_card(seq + 808)
        addr = base.address(seq)
        d1, d2 = base.date_cn(seq), base.date_cn(seq + 21)
        lines += [
            f"委托人：{principal}，身份证号：{pid}，住址：{addr}。",
            f"受托人：{agent}，身份证号：{aid}。",
            f"因{principal}与相关方合同纠纷一案，委托人委托受托人代为办理立案、开庭、和解等事项，"
            f"委托期限自{d1}起至{d2}止。",
            f"受托人在委托权限内签署的有关文件，委托人均予以承认。", "",
        ]
        chunk = {"姓名": [principal, agent], "身份证号": [pid, aid], "地址": [addr], "日期": [d1, d2]}
        for etype, values in chunk.items():
            entities.setdefault(etype, []).extend(values)
    return _finalize(lines, entities)


def _table_rows(page_id: int, count: int) -> tuple[list[str], dict[str, list[str]]]:
    """纯表格段：日期 | 摘要 | 对方户名 | 对方账号（人名/卡号/日期密集，金额为干扰项）。"""
    lines = ["交易日期        摘要            对方户名      对方账号             金额（元）"]
    entities: dict[str, list[str]] = {}
    for row in range(count):
        seq = _seq(page_id, 4, row)
        name = base.person_name(seq)
        bank = base.bank_card(seq)
        day = base.date_iso(seq)
        lines.append(f"{day}    网银转账      {name}      {bank}    {(seq % 900 + 100) * 10.5:.2f}")
        entities.setdefault("姓名", []).append(name)
        entities.setdefault("银行卡号", []).append(bank)
        entities.setdefault("日期", []).append(day)
    return lines, entities


def _page_bank_statement(page_id: int, density: str) -> dict:
    count = {"sparse": 4, "mid": 10, "dense": 20}[density]
    lines = [f"账户名：{base.person_name(_seq(page_id, 5))}    币种：人民币", ""]
    entities = {"姓名": [base.person_name(_seq(page_id, 5))]}
    rows, row_ents = _table_rows(page_id, count)
    lines += rows
    for etype, values in row_ents.items():
        entities.setdefault(etype, []).extend(values)
    return _finalize(lines, entities)


def _finalize(lines: list[str], entities: dict[str, list[str]]) -> dict:
    entities = {k: sorted(set(v)) for k, v in entities.items() if v}
    text = "\n".join(lines)
    for etype, values in entities.items():  # 生成器自检（与 make_ner_gt_corpus 同规）
        for value in values:
            if value not in text:
                raise AssertionError(f"GT 实体 {etype}:{value!r} 未原样出现在页文本中")
    unknown = set(entities) - set(base.DEFAULT_TYPES)
    if unknown:
        raise AssertionError(f"GT 类型 {unknown} 不在默认 9 类集中")
    return {"lines": lines, "entities": entities}


def build_page(page_id: int, doc_type: str = "contract", density: str = "mid",
               blank: bool = False, table_only: bool = False) -> dict:
    """构造一页合成文书。blank=空白页（无实体）；table_only=纯表格页。"""
    if doc_type not in DOC_TYPES or density not in DENSITIES:
        raise ValueError(f"doc_type/density 非法: {doc_type}/{density}")
    if blank:
        return {"lines": [], "entities": {}}
    if table_only:
        rows, entities = _table_rows(page_id, 12)
        return _finalize(rows, entities)
    builder = {"contract": _page_contract, "judgment": _page_judgment,
               "warrant": _page_warrant, "bank_statement": _page_bank_statement}[doc_type]
    return builder(page_id, density)
