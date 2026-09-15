"""Issue #46 格式矩阵判定关卡（纯函数，表驱动单测，不打网）。

四关判定（设计文档 §4）：
  G1 上传受理   HTTP 2xx 且返回 file_id
  G2 解析       parse content 非空且不含兜底文案（.doc 兜底=转换链不可用 → FAIL）
  G3 识别       GT 实体级召回 ≥ 80%，身份证号/电话逐字 exact 检出（数字保真一票否决口径）
  G4 成品       下载 2xx + 复扫/归一 grep 零原文残留 + 载体完整性 +（化名格）对照表自证

三档结论（按格式聚合全部格子）：
  承诺支持 = 全部格子 PASS
  实验性   = 打码格 PASS 但化名格 FAIL/低召回（手册标注降级用法）
  前端禁用 = 5xx / 解析崩溃 / 成品损坏 / 残留 / 兜底不可用
"""

from __future__ import annotations

# 兜底文案标记（file_parser.py 的两个不可解析出口；出现即解析链不可用）
FALLBACK_MARKERS = ("[无法解析 .doc 文件", "[无法解析文件")
# 数字保真一票否决的类型（镜像 #23/#37 口径）
EXACT_TYPES = ("身份证号", "电话")
MIN_RECALL = 0.8

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_SKIP = "SKIP"
STATUS_ERROR = "ERROR"  # 脚本侧异常（网络/超时等），不计格式 FAIL，重跑裁决

TIERS = ("承诺支持", "实验性", "前端禁用")


def gate_result(status: str, detail: dict | None = None) -> dict:
    return {"status": status, "detail": detail or {}}


def g1_upload(http_ok: bool, file_id: str | None) -> dict:
    if http_ok and file_id:
        return gate_result(STATUS_PASS)
    return gate_result(STATUS_FAIL, {"reason": f"上传未受理 http_ok={http_ok} file_id={file_id!r}"})


def g2_parse(content: str | None) -> dict:
    text = str(content or "")
    if not text.strip():
        return gate_result(STATUS_FAIL, {"reason": "解析内容为空"})
    for marker in FALLBACK_MARKERS:
        if marker in text:
            return gate_result(STATUS_FAIL, {"reason": f"命中解析兜底文案: {marker}…（转换链不可用）"})
    return gate_result(STATUS_PASS, {"chars": len(text)})


def g3_recognition(recognized: dict[str, list[str]], gt: dict[str, list[str]]) -> dict:
    """实体级召回（squash 归一域，同 #37）+ 数字保真 exact。"""
    from common_api import squash

    flat = [v for values in recognized.values() for v in values]
    flat_sq = {squash(v) for v in flat}
    total, hit, exact_misses, missing = 0, 0, [], []
    for etype, values in gt.items():
        for value in values:
            total += 1
            if squash(value) in flat_sq:
                hit += 1
            else:
                missing.append({"type": etype, "value": value})
            if etype in EXACT_TYPES and value not in flat:
                exact_misses.append({"type": etype, "value": value})
    recall = hit / total if total else 0.0
    detail = {"recall": round(recall, 4), "hit": hit, "total": total,
              "missing": missing, "exact_misses": exact_misses}
    if recall < MIN_RECALL:
        return gate_result(STATUS_FAIL, detail | {"reason": f"召回 {recall:.0%} < {MIN_RECALL:.0%}"})
    if exact_misses:
        return gate_result(STATUS_FAIL, detail | {"reason": f"数字保真 exact 未检出 {len(exact_misses)} 例"})
    return gate_result(STATUS_PASS, detail)


# ---- G4 载体完整性 ----

def check_docx_integrity(path) -> dict:
    from docx import Document
    document = Document(str(path))
    return {"ok": True, "paragraphs": len(document.paragraphs)}


def check_pdf_integrity(path, *, expect_pages: int = 1) -> dict:
    import fitz
    with fitz.open(str(path)) as doc:
        return {"ok": doc.page_count == expect_pages,
                "pages": doc.page_count, "expect_pages": expect_pages}


def check_image_integrity(path, *, expect_size: tuple[int, int] | None = None) -> dict:
    from PIL import Image
    with Image.open(str(path)) as img:
        size = list(img.size)
    return {"ok": expect_size is None or size == list(expect_size), "size": size,
            "expect_size": list(expect_size) if expect_size else None}


def check_pseudonym_mapping(entity_map: dict[str, str], originals: list[str]) -> dict:
    """化名对照自证：送入 execute 的每个原文都有非空化名且不等于原文。"""
    missing = []
    for value in originals:
        alias = entity_map.get(value)
        if not alias or alias == value:
            missing.append({"value": value, "alias": alias})
    return {"ok": not missing, "missing": missing, "mapped": len(entity_map)}


def grep_residual(product_text: str, originals: list[str]) -> list[str]:
    """归一化 grep（强归一，口径直接复用 leak_check._norm 消除漂移面）。"""
    from leak_check import _norm

    norm = _norm(product_text or "")
    return [o for o in originals if o and _norm(o) in norm]


def g4_product(*, download_ok: bool, residual_originals: list[str],
               integrity: dict, pseudonym_check: dict | None = None) -> dict:
    problems: list[str] = []
    if not download_ok:
        problems.append("成品下载失败")
    if residual_originals:
        problems.append(f"成品残留原文 {len(residual_originals)} 处: {residual_originals[:3]}")
    if not integrity.get("ok"):
        problems.append(f"载体完整性校验失败: {integrity}")
    if pseudonym_check is not None and not pseudonym_check.get("ok"):
        problems.append(f"化名对照自证失败: {pseudonym_check.get('missing')}")
    if problems:
        return gate_result(STATUS_FAIL, {"problems": problems, "integrity": integrity})
    return gate_result(STATUS_PASS, {"integrity": integrity,
                                     "pseudonym": pseudonym_check or {"mode": "mask"}})


def aggregate_format(cells: list[dict]) -> str:
    """按格式聚合三档结论。cells: [{mode, status, fail_class}]（runner 预先归类）。

    fail_class：hard = 5xx/解析崩溃/成品损坏/原文残留/解析兜底不可用；
                soft = 化名格 FAIL 或召回低于门槛但无残留。
    规则（设计文档 §4）：该格式全部格子 PASS → 承诺支持；任一 hard → 前端禁用；
    其余（打码可用、软失败）→ 实验性。
    含 ERROR 格子时结论不可给绿灯（未裁决 ≠ 通过）→ 返回待定值，重跑后聚合。
    SKIP 格子不参与聚合。
    """
    if any(c["status"] == STATUS_ERROR for c in cells):
        return "待定（含ERROR未重跑）"
    counted = [c for c in cells if c["status"] in (STATUS_PASS, STATUS_FAIL)]
    if not counted:
        return "无有效格子"
    if all(c["status"] == STATUS_PASS for c in counted):
        return TIERS[0]
    if any(c.get("fail_class") == "hard" for c in counted):
        return TIERS[2]
    return TIERS[1]
