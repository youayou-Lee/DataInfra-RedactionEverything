"""化名脱敏子集构建管线（Issue #37，设计文档 D3/D4）：真实非扫描样本 → 可入库评测样本。

两段式（中间必须人工复核映射 = GT 补漏，方法学坑①的缓解）：

  # 1) draft：上传识别 → preview-map（derived 编号映射草稿）→ CSV 落私有目录
  python eval/scripts/build_pseudonym_set.py draft 真实样本.docx \
      --api-base http://127.0.0.1:8000 --private-dir /root/private_data/eval37 \
      --dataset-id pseudo_case_001

  # 2) 人工复核 CSV（改「化名」列；漏检实体补行：原文+类型+自拟化名——补行即 GT 补漏，
  #    会并入执行载荷一并替换，见 build_merged_entities）

  # 3) finalize：重新上传 → execute(pseudonym + custom_replacements，含补行) → 下载成品 →
  #    GT 定位 → leak_check（grep + 识别复扫双保险）→ 写入 pseudonymized/ + manifest
  python eval/scripts/build_pseudonym_set.py finalize 复核后.csv \
      --source 真实样本.docx --api-base ... --private-dir ... --dataset-id pseudo_case_001

安全：对照表与复核 CSV 只写 --private-dir（不入库）；产物入库前 leak_check 零残留强制。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common_api  # noqa: E402
import leak_check  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
DATASETS_DIR = _REPO_ROOT / "eval" / "datasets"
MAPPING_COLUMNS = ["原文", "类型", "化名"]
_ID_RE = re.compile(r"[A-Za-z0-9_-]+")  # dataset-id 白名单（防路径写穿，评审 M8）


def recognize_entities(api: common_api.EvalApi, path: Path) -> tuple[str, list[dict]]:
    """上传并识别，返回 (file_id, entities[Entity dict])。

    pdf 走逐页 vision；docx/txt 走 parse + hybrid NER（D7：vision 不支持该类载体）。
    """
    file_id = api.upload(path)
    suffix = path.suffix.lower()
    if suffix in (".docx", ".txt"):
        entities_map, _ = api.parse_and_hybrid_ner(file_id)
        name_to_id = {name: tid for tid, name in common_api.TYPE_ID_TO_NAME.items()}
        entities: list[dict] = []
        for etype, values in entities_map.items():
            for value in values:
                entities.append({"id": f"eval37-{len(entities) + 1}", "text": value,
                                 "type": name_to_id.get(etype, etype), "start": 0,
                                 "end": len(value), "page": 1})
        return file_id, entities
    pages = 1
    import fitz
    with fitz.open(str(path)) as doc:
        pages = doc.page_count
    entities: list[dict] = []
    seq = 0
    for page in range(1, pages + 1):
        resp = api.vision(file_id, page)
        for box in resp.get("bounding_boxes") or []:
            text = str(box.get("text") or "").strip()
            if not text:
                continue
            seq += 1
            entities.append({"id": f"eval37-{seq}", "text": text,
                             "type": str(box.get("type") or ""), "start": 0, "end": len(text),
                             "page": page})
    return file_id, entities


def draft_mapping(api: common_api.EvalApi, entities: list[dict]) -> dict[str, str]:
    """preview-map 拿默认编号映射（与生产同源，D4）。"""
    config = {"replacement_mode": "pseudonym", "custom_replacements": {}}
    r = api.client.post("/api/v1/redaction/preview-map",
                        json={"entities": entities, "config": config})
    r.raise_for_status()
    return r.json()["entity_map"]


def write_mapping_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MAPPING_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def read_mapping_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows or not set(MAPPING_COLUMNS).issubset(rows[0].keys()):
        raise ValueError(f"复核 CSV 列不合法，需 {MAPPING_COLUMNS}")
    cleaned = []
    for row in rows:
        original = (row.get("原文") or "").strip()
        pseudonym = (row.get("化名") or "").strip()
        if original and pseudonym:
            cleaned.append({"原文": original, "类型": (row.get("类型") or "").strip(), "化名": pseudonym})
    if len({r["化名"] for r in cleaned}) != len(cleaned):
        raise ValueError("复核 CSV 中存在重复化名（不同原文映射到同一化名），请修正后再 finalize")
    return cleaned


def build_merged_entities(entities: list[dict], mapping_rows: list[dict]) -> list[dict]:
    """识别实体 + 人工补行（原文不在识别结果中）合并为执行载荷（评审 I1）。

    补行不并入则 execute 不会替换它 → finalize 必然失败，「补行=GT 补漏」成为死路。
    """
    known = {e["text"] for e in entities}
    name_to_id = {name: tid for tid, name in common_api.TYPE_ID_TO_NAME.items()}
    merged = list(entities)
    for row in mapping_rows:
        if row["原文"] not in known:
            merged.append({"id": f"eval37-manual-{len(merged) + 1}", "text": row["原文"],
                           "type": name_to_id.get(row["类型"], row["类型"]), "start": 0,
                           "end": len(row["原文"]), "page": 1, "manual": True})
    return merged


def execute_pseudonym(api: common_api.EvalApi, file_id: str, entities: list[dict],
                      mapping_rows: list[dict]) -> dict:
    custom = {row["原文"]: row["化名"] for row in mapping_rows}
    merged = build_merged_entities(entities, mapping_rows)
    config = {"replacement_mode": "pseudonym", "custom_replacements": custom}
    r = api.client.post("/api/v1/redaction/execute",
                        json={"file_id": file_id, "entities": merged, "bounding_boxes": [],
                              "config": config},
                        headers={"X-Idempotency-Key": f"eval37-{file_id}"})
    r.raise_for_status()
    return r.json()


def download(api: common_api.EvalApi, download_url: str, out_path: Path) -> None:
    r = api.client.get(download_url if download_url.startswith("http")
                       else download_url.lstrip("/"))
    r.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(r.content)


def locate_gt(product_path: Path, mapping_rows: list[dict]) -> list[dict]:
    """在化名版成品中定位化名串出现 → GT pages（页粒度，类型沿用映射表）。

    未在成品中出现的化名（人工补行/替换失败）记入 returned 的 missing 字段由调用方处理。
    """
    suffix = product_path.suffix.lower()
    page_texts: list[str] = []
    if suffix == ".pdf":
        import fitz
        with fitz.open(str(product_path)) as doc:
            page_texts = [page.get_text() for page in doc]
    elif suffix == ".docx":
        from docx import Document
        document = Document(str(product_path))
        parts = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    parts.append(cell.text)
        page_texts = ["\n".join(parts)]
    elif suffix == ".txt":
        page_texts = [product_path.read_text(encoding="utf-8")]
    else:
        raise ValueError(f"不支持: {product_path}")

    pages = [{"page": i, "entities": {}} for i in range(len(page_texts))]
    missing: list[dict] = []
    for row in mapping_rows:
        hit_pages = [i for i, text in enumerate(page_texts) if row["化名"] in text]
        if not hit_pages:
            missing.append(row)
            continue
        for i in hit_pages:
            pages[i]["entities"].setdefault(row["类型"], [])
            if row["化名"] not in pages[i]["entities"][row["类型"]]:
                pages[i]["entities"][row["类型"]].append(row["化名"])
    return {"pages": pages, "missing": missing, "page_count": len(page_texts)}


def update_manifest(dataset_id: str, file_name: str, carrier: str, page_count: int) -> None:
    manifest_path = DATASETS_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if any(f["id"] == dataset_id for f in manifest["files"]):
        raise ValueError(f"manifest 已存在 {dataset_id}")
    manifest["files"].append({
        "id": dataset_id, "path": f"pseudonymized/{file_name}", "gt": f"pseudonymized/{file_name}.gt.json",
        "source": "pseudonymized", "carrier": carrier, "doc_type": "real", "density": "n/a",
        "pages": page_count, "generator": "build_pseudonym_set.py（人工复核映射，见私有目录复核记录）",
        "levels": ["e2e"], "notes": "化名脱敏样本（GT 为化名版坐标，无原文）",
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="化名子集构建管线（Issue #37）")
    sub = parser.add_subparsers(dest="stage", required=True)

    p_draft = sub.add_parser("draft", help="识别 + 映射草稿")
    p_draft.add_argument("source", type=Path, help="真实样本（非扫描 pdf/docx/txt，不入库）")
    p_draft.add_argument("--api-base", default="http://127.0.0.1:8000")
    p_draft.add_argument("--api-user", default="eval_user")
    p_draft.add_argument("--api-pass", default="EvalUser!2026")
    p_draft.add_argument("--private-dir", required=True, type=Path, help="私有目录（不入库）")
    p_draft.add_argument("--dataset-id", required=True)
    p_draft.add_argument("--timeout", type=float, default=600.0)

    p_fin = sub.add_parser("finalize", help="执行 + GT + leak_check + 入库")
    p_fin.add_argument("mapping", type=Path, help="复核后的映射 CSV")
    p_fin.add_argument("--source", required=True, type=Path, help="原始真实样本")
    p_fin.add_argument("--api-base", default="http://127.0.0.1:8000")
    p_fin.add_argument("--api-user", default="eval_user")
    p_fin.add_argument("--api-pass", default="EvalUser!2026")
    p_fin.add_argument("--private-dir", required=True, type=Path)
    p_fin.add_argument("--dataset-id", required=True)
    p_fin.add_argument("--timeout", type=float, default=600.0)

    args = parser.parse_args()
    api = common_api.EvalApi(args.api_base, args.api_user, args.api_pass, timeout=args.timeout)
    try:
        if args.stage == "draft":
            return do_draft(api, args)
        return do_finalize(api, args)
    finally:
        api.close()


def do_draft(api: common_api.EvalApi, args: argparse.Namespace) -> int:
    if not _ID_RE.fullmatch(args.dataset_id):  # 与 finalize 对称（评审 Minor-3）
        print(f"❌ dataset-id 只允许 [A-Za-z0-9_-]+（收到 {args.dataset_id!r}）", file=sys.stderr)
        return 1
    file_id, entities = recognize_entities(api, Path(args.source))
    try:
        entity_map = draft_mapping(api, entities)
    finally:
        api.delete_file(file_id)
    type_of = {}
    for e in entities:
        type_of.setdefault(e["text"], common_api.TYPE_ID_TO_NAME.get(e["type"], e["type"]))
    rows = [{"原文": original, "类型": type_of.get(original, ""), "化名": mapped}
            for original, mapped in entity_map.items()]
    csv_path = args.private_dir / f"{args.dataset_id}.mapping.draft.csv"
    write_mapping_csv(csv_path, rows)
    print(f"识别实体 {len(entities)} 个，默认映射 {len(rows)} 条 -> {csv_path}")
    print("下一步：人工复核（改「化名」列、为漏检实体补行），然后 finalize。")
    print("补行实体（漏检原文）将并入执行载荷一并替换；其化名必须出现在成品中，否则 finalize 拦截。")
    return 0


def do_finalize(api: common_api.EvalApi, args: argparse.Namespace) -> int:
    if not _ID_RE.fullmatch(args.dataset_id):
        print(f"❌ dataset-id 只允许 [A-Za-z0-9_-]+（收到 {args.dataset_id!r}）", file=sys.stderr)
        return 1
    rows = read_mapping_csv(args.mapping)
    source = Path(args.source)
    file_id, entities = recognize_entities(api, source)
    try:
        result = execute_pseudonym(api, file_id, entities, rows)
    finally:
        api.delete_file(file_id)
    if result.get("residual_entities"):
        print(f"❌ 执行自检发现残留（应为空）：{result['residual_entities'][:5]}", file=sys.stderr)
        return 1
    product = DATASETS_DIR / "pseudonymized" / f"{args.dataset_id}{source.suffix.lower()}"
    download(api, result["download_url"], product)
    write_mapping_csv(args.private_dir / f"{args.dataset_id}.mapping.final.csv", rows)  # 复核底稿

    located = locate_gt(product, rows)
    if located["missing"]:
        print(f"❌ {len(located['missing'])} 个化名未在成品中找到（补行或替换失败）："
              f"{[r['化名'] for r in located['missing']][:5]}", file=sys.stderr)
        return 1
    gt = {"file": product.name, "source": "pseudonymized",
          "carrier": source.suffix.lower().lstrip("."), "doc_type": "real", "density": "n/a",
          "seed": None, "pages": located["pages"]}
    gt_path = Path(str(product) + ".gt.json")
    gt_path.write_text(json.dumps(gt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    check = leak_check.run_check(product, rows)
    rescan = leak_check.run_rescan(api, product, rows)  # 双保险②：入库前必跑（评审 I2）
    if not check["clean"] or not rescan["clean"]:
        findings = check["findings"] + rescan["findings"]
        print(f"❌ leak_check 发现残留，禁止入库：{findings[:5]}", file=sys.stderr)
        product.unlink(missing_ok=True)
        gt_path.unlink(missing_ok=True)
        return 1
    update_manifest(args.dataset_id, product.name, gt["carrier"], located["page_count"])
    print(f"✅ {args.dataset_id} 入库就绪：{product.name} + GT（leak_check 零残留）")
    print(f"   复核底稿与对照表在私有目录（不入库）：{args.private_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
