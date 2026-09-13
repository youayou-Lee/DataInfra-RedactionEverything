"""Issue #37 合成生成器单测：确定性、GT 自检、载体特征、格式合法。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import fitz
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GEN_DIR = REPO_ROOT / "eval" / "datasets" / "generators"
sys.path.insert(0, str(GEN_DIR))
sys.path.insert(0, str(REPO_ROOT / "backend" / "scripts" / "eval"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen_text = _load("gen_text_under_test", GEN_DIR / "gen_text.py")
gen_pdf = _load("gen_pdf_under_test", GEN_DIR / "gen_pdf.py")
gen_docx = _load("gen_docx_under_test", GEN_DIR / "gen_docx.py")
build_all = _load("build_all_under_test", GEN_DIR / "build_all.py")
base_corpus = sys.modules.get("make_ner_gt_corpus") or _load(
    "make_ner_gt_corpus_direct", REPO_ROOT / "backend" / "scripts" / "eval" / "make_ner_gt_corpus.py")


# ---------- 确定性 ----------

def _pdf_digest(path: Path) -> tuple:
    with fitz.open(str(path)) as doc:
        return (doc.page_count,
                tuple(page.get_text() for page in doc),
                tuple(bool(page.get_images()) for page in doc))


def _docx_digest(path: Path) -> tuple:
    with zipfile.ZipFile(str(path)) as zf:
        return tuple(sorted((n, hashlib.sha256(zf.read(n)).hexdigest()) for n in zf.namelist()))


@pytest.mark.parametrize("carrier,kw", [
    ("text", dict(pages=2, doc_type="contract")),
    ("scanned", dict(pages=2, doc_type="judgment")),
    ("hybrid", dict(pages=2, doc_type="contract")),
    ("scanned", dict(pages=3, doc_type="contract", edge=True)),
])
def test_pdf_deterministic_content(tmp_path, carrier, kw):
    outs = []
    for run in (1, 2):
        p = tmp_path / f"run{run}.pdf"
        gen_pdf.build_pdf(p, carrier=carrier, **kw)
        outs.append(_pdf_digest(p))
    assert outs[0] == outs[1]


def test_txt_and_gt_byte_deterministic(tmp_path):
    first, second = [], []
    for run in (1, 2):
        d = tmp_path / f"run{run}"
        build_all.build_all(d)
        for f in sorted(d.joinpath("synthetic").iterdir()):
            if f.suffix in (".json", ".jsonl", ".txt", ".gt.json"):
                first.append((f.name, hashlib.sha256(f.read_bytes()).hexdigest())) if run == 1 \
                    else second.append((f.name, hashlib.sha256(f.read_bytes()).hexdigest()))
    assert first == second


def test_docx_deterministic(tmp_path):
    digests = []
    for run in (1, 2):
        p = tmp_path / f"run{run}.docx"
        gen_docx.build_docx(p, pages=2, doc_type="judgment")
        digests.append(_docx_digest(p))
    assert digests[0] == digests[1]


# ---------- GT 自检与载体特征 ----------

@pytest.mark.parametrize("doc_type,density", [("contract", "mid"), ("judgment", "mid"),
                                              ("warrant", "mid"), ("bank_statement", "dense"),
                                              ("contract", "dense"), ("contract", "sparse")])
def test_gt_entities_verbatim_in_text(doc_type, density):
    for page_id in range(4):
        page = gen_text.build_page(page_id, doc_type=doc_type, density=density)
        text = "\n".join(page["lines"])
        for etype, values in page["entities"].items():
            for value in values:
                assert value in text, f"{doc_type}/{density} p{page_id} {etype}: {value!r}"


def test_text_pdf_gt_visible_in_text_layer(tmp_path):
    p = tmp_path / "t.pdf"
    gt_pages = gen_pdf.build_pdf(p, pages=2, carrier="text", doc_type="contract", density="mid")
    with fitz.open(str(p)) as doc:
        layer = ["".join(page.get_text().split()) for page in doc]  # 去全部空白后比对
    for gt in gt_pages:
        for values in gt["entities"].values():
            for value in values:
                assert "".join(value.split()) in layer[gt["page"]], \
                    f"{value!r} 未出现在文本层第 {gt['page']} 页"


def test_scanned_pdf_has_no_text_layer(tmp_path):
    p = tmp_path / "s.pdf"
    gen_pdf.build_pdf(p, pages=1, carrier="scanned", doc_type="contract", density="mid")
    with fitz.open(str(p)) as doc:
        assert doc[0].get_text().strip() == ""
        assert doc[0].get_images()


def test_hybrid_carrier_split(tmp_path):
    p = tmp_path / "h.pdf"
    gen_pdf.build_pdf(p, pages=4, carrier="hybrid", doc_type="contract", density="mid")
    with fitz.open(str(p)) as doc:
        assert doc[0].get_text().strip() != "" and not doc[0].get_images()
        assert doc[2].get_text().strip() == "" and doc[2].get_images()


def test_edge_pages(tmp_path):
    p = tmp_path / "e.pdf"
    gt_pages = gen_pdf.build_pdf(p, pages=3, carrier="scanned", doc_type="contract",
                                 density="mid", edge=True)
    assert gt_pages[0]["entities"] == {}
    assert gt_pages[1]["entities"]  # 表格页有实体
    assert gen_text.build_page(0, blank=True) == {"lines": [], "entities": {}}


def test_density_scaling():
    mid = sum(len(v) for v in gen_text.build_page(0, density="mid")["entities"].values())
    dense = sum(len(v) for v in gen_text.build_page(0, density="dense")["entities"].values())
    sparse = sum(len(v) for v in gen_text.build_page(0, density="sparse")["entities"].values())
    assert sparse < mid < dense


# ---------- 格式合法 ----------

def test_id_card_checksum():
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    for seq in range(30):
        value = base_corpus.id_card(seq)
        expect = "10X98765432"[sum(int(d) * w for d, w in zip(value[:17], weights)) % 11]
        assert value[17] == expect and len(value) == 18


def test_phone_bank_format():
    for seq in range(20):
        assert len(base_corpus.phone(seq)) == 11 and base_corpus.phone(seq).startswith("1")
        assert len(base_corpus.bank_card(seq).replace(" ", "")) == 16


# ---------- 矩阵完整性 ----------

def test_manifest_matches_matrix():
    manifest = json.loads((REPO_ROOT / "eval" / "datasets" / "manifest.json").read_text(encoding="utf-8"))
    files = manifest["files"]
    assert len(files) == len(build_all.MATRIX) + 1  # + ner_corpus
    for f in files:
        if f["id"] == build_all.NER_CORPUS_ID:
            assert f["levels"] == ["ner"]
            continue
        assert f["levels"] == ["e2e"]  # txt/docx 走 parse+hybrid NER 链路（D7）
        data = REPO_ROOT / "eval" / "datasets" / f["path"]
        gt = REPO_ROOT / "eval" / "datasets" / f["gt"]
        assert data.exists() and gt.exists(), f"{f['id']} 缺产物"
        gt_doc = json.loads(gt.read_text(encoding="utf-8"))
        assert gt_doc["source"] == "synthetic"
        assert len(gt_doc["pages"]) == f["pages"] or f["carrier"] in ("docx", "txt")
    carriers = {f["carrier"] for f in files}
    assert carriers == {"scanned_pdf", "text_pdf", "hybrid_pdf", "docx", "txt"}
