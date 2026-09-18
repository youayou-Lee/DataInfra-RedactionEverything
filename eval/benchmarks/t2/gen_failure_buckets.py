"""合成失败模式桶生成器：数字串混淆/引述/长实体/低频类型/上下文干扰。
复用 make_ner_gt_corpus 的确定性实体函数（同构不同文）。"""
import importlib.util
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "make_ner_gt_corpus", _REPO / "backend" / "scripts" / "eval" / "make_ner_gt_corpus.py")
corp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(corp)

# 低频类型：preset 里有、日常语料几乎不出现（场景句内嵌）
LOWFREQ = {"民族": "汉族", "宗教信仰": "佛教", "政治面貌": "中共党员", "国籍": "中国"}


def _id_check(v: str) -> bool:
    return bool(re.fullmatch(r"\d{17}[\dX]", v))


def _entry(bucket: str, i: int, text: str, entities: dict) -> dict:
    entities = {k: sorted(set(v)) for k, v in entities.items() if v}
    for t, vals in entities.items():
        for v in vals:
            assert v in text, f"{bucket}/{i}: {t}:{v} 不在文中"
            if t == "身份证号":
                assert _id_check(v), f"{bucket}/{i}: 身份证格式非法 {v}"
    return {"id": f"{bucket}_{i:04d}", "bucket": bucket, "bucket_kind": "synthetic",
            "source": "generator", "input_modality": "text", "text": text, "entities": entities}


def build_entry(bucket: str, i: int) -> dict:
    name = corp.person_name(i)
    ida, tel, bank = corp.id_card(i), corp.phone(i), corp.bank_card(i)
    case_no = f"(202{ i % 6})粤{1 + i % 20}刑终{100 + i % 900}号"       # 案号
    plate = f"粤{chr(65 + i % 26)}{10000 + i * 37 % 89999}"           # 车牌
    if bucket == "digit-confusion":
        text = (f"被告人{name}（身份证号：{ida}）涉嫌盗窃罪，案号{case_no}，"
                f"驾驶车辆号牌{plate}，联系电话{tel}，赃款转入账户{bank}。")
        ents = {"姓名": [name], "身份证号": [ida], "案号": [case_no], "车牌号": [plate],
                "电话": [tel], "银行卡号": [bank]}
    elif bucket == "quoted-entity":
        text = (f"证人称：\"{name}当时说他在{corp.address(i)}住。\"笔录记载'{name}'签名确认。")
        ents = {"姓名": [name], "地址": [corp.address(i)]}
    elif bucket == "long-entity":
        org = f"{corp.org_name(i)}{corp.org_name(i + 31)}联合工作组"
        text = f"经{org}核查，被告人{name}（身份证号：{ida}）情况属实。"
        ents = {"姓名": [name], "身份证号": [ida], "机构名称": [org]}
    elif bucket == "lowfreq-type":
        pairs = list(LOWFREQ.items())
        t, v = pairs[i % len(pairs)]
        text = f"档案记载：{name}，{t}为{v}，现居{corp.address(i)}。"
        ents = {"姓名": [name], t: [v], "地址": [corp.address(i)]}
    else:  # context-distractor：干扰词紧贴实体
        text = (f"并非{name}本人，而是与\"{name}\"同名的另一个人；"
                f"电话并非{tel}，备案号{2000 + i * 7 % 8000}-{i % 9}也非电话。")
        ents = {"姓名": [name], "电话": [tel]}
    return _entry(bucket, i, text, ents)


def build_bucket(bucket: str, size: int) -> list[dict]:
    return [build_entry(bucket, i) for i in range(size)]
