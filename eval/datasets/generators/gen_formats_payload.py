"""格式矩阵统一 payload（Issue #46）：全部 16 种格式共用同一组合成实体。

payload：2 人名 + 1 身份证号 + 1 手机号 + 1 地址（设计文档 §3）。
实体函数复用 backend/scripts/eval/make_ner_gt_corpus.py（校验位合法，D6：import 不复制）；
确定性：固定序号派生，无随机数——同工具链重建逐字节一致；跨工具链版本以
魔数 + 尺寸 + GT 自检为准（.doc/PDF/图片受 LibreOffice/PyMuPDF/PIL 版本影响）。

内容为程序合成假数据，不含任何真实案卷信息，可入库。
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "backend" / "scripts" / "eval"))

import make_ner_gt_corpus as base  # noqa: E402

# 固定序号（与 #37 语料取值域错开，避免同实体跨评测集复用造成记忆假象）
_SEQ = 460_000


def build_payload() -> dict:
    """构造统一文书行 + GT 实体（每格式一份同内容样张的单一事实源）。"""
    name_a, name_b = base.person_name(_SEQ), base.person_name(_SEQ + 101)
    id_card = base.id_card(_SEQ + 7)
    phone = base.phone(_SEQ + 13)
    addr = base.address(_SEQ + 21)
    lines = [
        "授权确认书",
        "",
        f"委托人：{name_a}，身份证号：{id_card}。",
        f"联系地址：{addr}。",
        f"受托人：{name_b}，联系电话：{phone}。",
        "委托事项：代为办理文件签收与材料递交事宜。",
        "本确认书自签署之日起生效，有效期为六个月。",
        "",
        "（本文件为程序合成的测试样张，所载信息纯属虚构。）",
    ]
    entities = {
        "姓名": [name_a, name_b],
        "身份证号": [id_card],
        "电话": [phone],
        "地址": [addr],
    }
    text = "\n".join(lines)
    for etype, values in entities.items():  # GT 自检（与 gen_text._finalize 同规）
        for value in values:
            if value not in text:
                raise AssertionError(f"GT 实体 {etype}:{value!r} 未原样出现在 payload 文本中")
    return {"lines": lines, "entities": entities}


if __name__ == "__main__":
    payload = build_payload()
    print("\n".join(payload["lines"]))
