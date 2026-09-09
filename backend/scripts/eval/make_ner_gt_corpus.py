"""生成 NER 质量评测用合成语料（含 ground truth 标注），Issue #23 质量闸门的评测输入。

用法：
  python make_ner_gt_corpus.py --out ner_gt_corpus.jsonl --pages 10

输出 JSONL，每行一页：
  {"page_id": 0, "text": "……", "entities": {"姓名": ["张三", "..."], ...}}

特性：
  - 确定性生成（按页/槽位索引算术变换，无随机数，重复运行逐字节一致）；
  - 9 种默认实体类型全覆盖（姓名/身份证号/护照号/电话/邮箱/地址/银行卡号/机构名称/日期），
    护照号仅在部分页出现（验证「无匹配类型不得返回」）；
  - 身份证号为 18 位含 GB 11643-1999 正确校验位（走 backend 全链路时正则兜底不误拒）；
  - 干扰项：合同编号、金额、快递单号等格式相似的非目标串；
  - 断言每个 GT 实体串原样出现在页文本中（生成器内置自检）。
内容为程序合成假数据，不含任何真实案卷信息，可入库。
"""

import argparse
import json
import sys

# 与 backend/config/preset_entity_types.json 默认勾选集一致（9 类）
DEFAULT_TYPES = ["姓名", "身份证号", "护照号", "电话", "邮箱", "地址", "银行卡号", "机构名称", "日期"]

SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许"
GIVEN = "伟芳娜敏静丽强磊军洋勇艳杰娟涛明超霞平刚桂英"
CITIES = [
    ("北京市", "朝阳区", "建国路"), ("上海市", "浦东新区", "世纪大道"), ("广州市", "天河区", "体育西路"),
    ("杭州市", "西湖区", "文三路"), ("成都市", "武侯区", "天府大道"), ("南京市", "鼓楼区", "中山北路"),
]
ORG_PRE = ["华宸", "瑞泰", "恒达", "明峻", "正阳", "广源"]
ORG_KIND = ["信息技术有限公司", "实业发展有限公司", "建筑工程有限公司", "商贸有限公司"]
COURTS = ["海州区人民法院", "临江市人民法院", "南湖经济技术开发区人民法院"]

DOC_KINDS = ["业务往来确认函", "执行异议书", "债权转让通知", "设备采购合同补充协议", "还款情况说明",
             "项目验收备忘录", "授权委托书", "对账确认函", "劳动争议答辩状", "档案移交清单"]


def person_name(i: int) -> str:
    return SURNAMES[i % len(SURNAMES)] + GIVEN[(i * 7) % len(GIVEN)] + GIVEN[(i * 13 + 5) % len(GIVEN)]


def id_card(seq: int) -> str:
    """18 位身份证（17 位 body + 1 校验位，可能为 X），GB 11643-1999。"""
    body = f"{110101 + seq % 90}{1960 + seq % 45}{1 + seq % 12:02d}{1 + seq % 28:02d}{(seq * 37) % 1000:03d}"
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check = "10X98765432"[sum(int(d) * w for d, w in zip(body, weights)) % 11]
    return body + check


def passport_no(seq: int) -> str:
    return f"{'EG'[seq % 2]}{10000000 + seq * 7919 % 89999999:08d}"


def phone(seq: int) -> str:
    return f"1{3 + seq % 7}{(seq * 137) % 10}{(10000000 + seq * 991137) % 100000000:08d}"


def email(seq: int) -> str:
    return f"user{seq % 100:02d}.chen@example-corp{seq % 5}.cn"


def address(seq: int) -> str:
    city, district, road = CITIES[seq % len(CITIES)]
    return f"{city}{district}{road}{18 + seq % 80}号院{1 + seq % 9}号楼{101 + seq % 30}室"


def bank_card(seq: int) -> str:
    return f"6222 0210 {1000 + seq % 9000:04d} {2000 + (seq * 3) % 8000:04d}"


def org_name(seq: int) -> str:
    if seq % 3 == 0:
        return COURTS[seq % len(COURTS)]
    return f"{ORG_PRE[seq % len(ORG_PRE)]}{ORG_KIND[(seq // 6) % len(ORG_KIND)]}第{1 + seq % 12}分公司"


def date_cn(seq: int) -> str:
    return f"202{seq % 6}年{1 + seq % 12:02d}月{1 + seq % 28:02d}日"


def date_iso(seq: int) -> str:
    return f"202{seq % 6}-{1 + seq % 12:02d}-{1 + seq % 28:02d}"


def build_page(page_id: int) -> dict:
    """构造一页合成文书：先造实体，再嵌入句子，GT 与正文严格一致。"""
    p = page_id
    a, b, c = person_name(p), person_name(p + 101), person_name(p + 202)
    ida = id_card(p)
    phone_a, phone_b = phone(p), phone(p + 55)
    mail = email(p)
    addr_a = address(p)
    bank = bank_card(p)
    org_a, org_b = org_name(p), org_name(p + 13)
    d1, d2 = date_cn(p), date_iso(p + 7)
    passport = passport_no(p) if p % 2 == 0 else None  # 奇数页无护照，验证空类型不返回

    lines = [
        f"{DOC_KINDS[p % len(DOC_KINDS)]}（编号：HT-2026-{1000 + p:04d}）",
        "",
        f"甲方（转让方）：{a}，身份证号：{ida}，联系地址：{addr_a}。",
        f"乙方（受让方）：{org_a}，法定代表人：{b}，联系电话：{phone_a}，电子邮箱：{mail}。",
    ]
    if passport:
        lines.append(f"第三人{c}（护照号：{passport}）作为见证人出席并签字确认。")
    else:
        lines.append(f"第三人{c}作为见证人出席并签字确认。")
    lines += [
        f"经双方核对，截至{d1}，乙方尚欠甲方货款本金人民币{(p % 90 + 10) * 1.37:.2f}万元，"
        f"约定于{d2}前一次性汇入甲方指定账户（开户行：中国工商银行{CITIES[p % 6][0]}分行，账号：{bank}）。",
        f"若发生争议，双方同意提交{org_b}审理，诉讼费用由败诉方承担。",
        f"甲方经办人{a}已于{d2}通过电话{phone_b}告知乙方上述安排，乙方联系人{c}确认无异议。",
        f"本函一式两份，双方各执一份，自{d1}起生效。备案登记号：FC-{2000 + p * 7 % 8000}-{p % 9}。",
        "快递单号：SF1350{0:06d}（文件寄送凭证，与个人信息无关）。".format(p * 9173 % 999999),
    ]

    text = "\n".join(lines)
    entities = {
        "姓名": [a, b, c],
        "身份证号": [ida],
        "电话": [phone_a, phone_b],
        "邮箱": [mail],
        "地址": [addr_a],
        "银行卡号": [bank],
        "机构名称": [org_a, org_b],
        "日期": [d1, d2],
    }
    if passport:
        entities["护照号"] = [passport]
    entities = {k: sorted(set(v)) for k, v in entities.items() if v}

    # 生成器自检：GT 实体必须原样出现在正文中（否则评测口径失效）
    for etype, values in entities.items():
        for v in values:
            if v not in text:
                raise AssertionError(f"page {page_id}: GT 实体 {etype}:{v!r} 未原样出现在文本中")
    unknown = set(entities) - set(DEFAULT_TYPES)
    if unknown:
        raise AssertionError(f"page {page_id}: GT 类型 {unknown} 不在默认类型集中")

    return {"page_id": page_id, "text": text, "entities": entities}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="输出 JSONL 路径")
    parser.add_argument("--pages", type=int, default=10, help="页数（默认 10，对齐 Issue #23 验收语料）")
    args = parser.parse_args()

    pages = [build_page(i) for i in range(args.pages)]
    with open(args.out, "w", encoding="utf-8") as f:
        for page in pages:
            f.write(json.dumps(page, ensure_ascii=False) + "\n")

    total = sum(len(v) for page in pages for v in page["entities"].values())
    digital = sum(len(page["entities"].get(t, [])) for page in pages for t in ("身份证号", "护照号", "电话", "银行卡号"))
    print(f"OK: {args.pages} 页 -> {args.out}（实体 {total} 个，其中数字实体 {digital} 个）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
