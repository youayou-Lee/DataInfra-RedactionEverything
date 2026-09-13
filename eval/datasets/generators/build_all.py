"""一键重建全部合成评测样本与 manifest（Issue #37，设计文档 §5 矩阵）。

用法（仓库根执行）：
  python eval/datasets/generators/build_all.py            # 重建到 eval/datasets/
  python eval/datasets/generators/build_all.py --check    # 只校验现有产物与配置一致

确定性：全部生成器无随机数；重复运行产物一致（GT 逐字节一致，PDF/docx 内容一致）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "backend" / "scripts" / "eval"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gen_docx  # noqa: E402
import gen_pdf  # noqa: E402
import gen_text  # noqa: E402
import make_ner_gt_corpus  # noqa: E402

DATASETS_DIR = _REPO_ROOT / "eval" / "datasets"

# 设计文档 §5 v1 矩阵：id → 生成参数（levels: 参与的评测层）
MATRIX: list[dict] = [
    dict(id="syn_contract_1p_mid", carrier="scanned_pdf", doc_type="contract", density="mid", pages=1,
         levels=["e2e"], notes="单页延迟档"),
    dict(id="syn_contract_10p_mid", carrier="scanned_pdf", doc_type="contract", density="mid", pages=10,
         levels=["e2e"], notes="速度主力档（对齐 perf_bench 语料规模）"),
    dict(id="syn_contract_50p_mid", carrier="scanned_pdf", doc_type="contract", density="mid", pages=50,
         levels=["e2e"], notes="吞吐/批量档"),
    dict(id="syn_judgment_3p_mid", carrier="scanned_pdf", doc_type="judgment", density="mid", pages=3,
         levels=["e2e"], notes="文档类型多样性"),
    dict(id="syn_contract_10p_dense", carrier="scanned_pdf", doc_type="contract", density="dense", pages=10,
         levels=["e2e"], notes="高密度召回压力"),
    dict(id="syn_contract_10p_sparse", carrier="scanned_pdf", doc_type="contract", density="sparse", pages=10,
         levels=["e2e"], notes="低密度档"),
    dict(id="syn_contract_3p_mid", carrier="text_pdf", doc_type="contract", density="mid", pages=3,
         levels=["e2e"], notes="文本层链路"),
    dict(id="syn_judgment_3p_mid", carrier="text_pdf", doc_type="judgment", density="mid", pages=3,
         levels=["e2e"], notes="文本层×类型"),
    dict(id="syn_warrant_1p_mid", carrier="text_pdf", doc_type="warrant", density="mid", pages=1,
         levels=["e2e"], notes="最小文件"),
    dict(id="syn_hybrid_4p_mid", carrier="hybrid_pdf", doc_type="contract", density="mid", pages=4,
         levels=["e2e"], notes="混合载体（2 文本层+2 扫描）"),
    dict(id="syn_contract_2p_mid", carrier="docx", doc_type="contract", density="mid", pages=2,
         levels=["e2e"], notes="docx 链路"),
    dict(id="syn_judgment_2p_mid", carrier="docx", doc_type="judgment", density="mid", pages=2,
         levels=["e2e"], notes="docx×类型"),
    dict(id="syn_contract_txt_1p_mid", carrier="txt", doc_type="contract", density="mid", pages=1,
         levels=["e2e"], notes="纯文本（走 parse+hybrid NER 链路，vision 不支持 txt——D7）"),
    dict(id="syn_statement_1p_table", carrier="txt", doc_type="bank_statement", density="dense", pages=1,
         levels=["e2e"], notes="表格密集边界（同上，parse+hybrid NER）"),
    dict(id="syn_edge_3p_mixed", carrier="scanned_pdf", doc_type="contract", density="mid", pages=3,
         edge=True, levels=["e2e"], notes="边界页（空页+纯表格页+正常页）"),
]

NER_CORPUS_ID = "ner_corpus_10p"
NER_CORPUS_PAGES = 10

_EXT = {"scanned_pdf": ".pdf", "text_pdf": ".pdf", "hybrid_pdf": ".pdf", "docx": ".docx", "txt": ".txt"}


def _generator_desc(spec: dict) -> str:
    if spec["carrier"] == "txt":
        return f"gen_text.build_page(doc_type={spec['doc_type']!r}, density={spec['density']!r})"
    return (f"gen_pdf.build_pdf(carrier={spec['carrier'].removesuffix('_pdf')!r}, "
            f"doc_type={spec['doc_type']!r}, density={spec['density']!r}, pages={spec['pages']}"
            + (", edge=True)" if spec.get("edge") else ")"))


def build_all(out_dir: Path) -> list[dict]:
    manifest_files: list[dict] = []
    synthetic = out_dir / "synthetic"
    synthetic.mkdir(parents=True, exist_ok=True)
    for spec in MATRIX:
        file_id = spec["id"]
        path = synthetic / f"{file_id}{_EXT[spec['carrier']]}"
        gt_path = synthetic / f"{file_id}.gt.json"
        if spec["carrier"] in ("scanned_pdf", "text_pdf", "hybrid_pdf"):
            gt_pages = gen_pdf.build_pdf(
                path, pages=spec["pages"], carrier=spec["carrier"].removesuffix("_pdf"),
                doc_type=spec["doc_type"], density=spec["density"], edge=bool(spec.get("edge")))
        elif spec["carrier"] == "docx":
            gt_pages = gen_docx.build_docx(
                path, pages=spec["pages"], doc_type=spec["doc_type"], density=spec["density"])
        else:  # txt
            lines: list[str] = []
            entities_all: dict[str, list[str]] = {}
            for i in range(spec["pages"]):
                data = gen_text.build_page(i, doc_type=spec["doc_type"], density=spec["density"])
                lines.append(f"— 第 {i + 1} 页 —")
                lines += data["lines"]
                for etype, values in data["entities"].items():
                    entities_all.setdefault(etype, []).extend(values)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            gt_pages = [{"page": 0, "entities": {k: sorted(set(v)) for k, v in entities_all.items() if v}}]
        gt = {"file": path.name, "source": "synthetic", "carrier": spec["carrier"],
              "doc_type": spec["doc_type"], "density": spec["density"], "seed": 0,
              "pages": gt_pages}
        gt_path.write_text(json.dumps(gt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest_files.append({
            "id": file_id, "path": f"synthetic/{path.name}", "gt": f"synthetic/{gt_path.name}",
            "source": "synthetic", "carrier": spec["carrier"], "doc_type": spec["doc_type"],
            "density": spec["density"], "pages": spec["pages"],
            "generator": _generator_desc(spec), "levels": spec["levels"], "notes": spec.get("notes", ""),
        })
        print(f"built {path.name}（{sum(len(e) for p in gt_pages for e in p['entities'].values())} 实体）")

    ner_path = synthetic / f"{NER_CORPUS_ID}.jsonl"
    pages = [make_ner_gt_corpus.build_page(i) for i in range(NER_CORPUS_PAGES)]
    ner_path.write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in pages), encoding="utf-8")
    manifest_files.append({
        "id": NER_CORPUS_ID, "path": f"synthetic/{ner_path.name}", "gt": f"synthetic/{ner_path.name}",
        "source": "synthetic", "carrier": "txt", "doc_type": "mixed", "density": "mid",
        "pages": NER_CORPUS_PAGES, "generator": "make_ner_gt_corpus.py（复用 #23 M4，零漂移）",
        "levels": ["ner"], "notes": "NER 引擎层语料（JSONL，GT 内嵌）",
    })
    return manifest_files


def main() -> int:
    parser = argparse.ArgumentParser(description="重建合成评测集（Issue #37）")
    parser.add_argument("--out", default=str(DATASETS_DIR), help="输出目录（默认 eval/datasets/）")
    args = parser.parse_args()
    out_dir = Path(args.out)
    manifest_files = build_all(out_dir)
    manifest = {"version": 1, "files": manifest_files}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                            encoding="utf-8")
    print(f"OK: {len(manifest_files)} 条目 -> {out_dir}/manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
