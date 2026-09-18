# backend/tests/test_benchmark_t2_ingest.py
import json
import sys
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "eval" / "benchmarks" / "t2"))
import ingest_hardcase as ing  # noqa: E402


@pytest.fixture(scope="module")
def sample_pdf(tmp_path_factory):
    import fitz
    p = tmp_path_factory.mktemp("hc") / "doc.pdf"
    d = fitz.open()
    d.new_page().insert_text((72, 72), "被告人张三身份证号11010119600101000X案号(2023)粤01刑终100号", fontname="china-s")
    d.save(p); d.close()
    return p


def test_build_entry_ok(sample_pdf):
    e = ing.build_hardcase_entry(sample_pdf, 0, [("姓名", "张三")], story="姓名漏检", origin="issue#40")
    assert e["bucket"] == "hardcase" and e["bucket_kind"] == "hardcase"
    assert e["entities"]["姓名"] == ["张三"]
    assert e["origin"] == "issue#40" and "张三" in e["text"]
    assert ing.ENTRY_SCHEMA_KEYS <= set(e)


def test_reject_entity_not_in_text(sample_pdf):
    with pytest.raises(ValueError, match="不在文本中"):
        ing.build_hardcase_entry(sample_pdf, 0, [("姓名", "不存在的人")], story="x", origin="t")


def test_reject_unknown_type(sample_pdf):
    with pytest.raises(ValueError, match="不在 preset"):
        ing.build_hardcase_entry(sample_pdf, 0, [("外星类型", "张三")], story="x", origin="t")


def test_raw_form_substitutes(tmp_path_factory):
    # GT 串（规范形态）与文中形态不同时，raw_forms 命中即收
    import fitz
    p = tmp_path_factory.mktemp("hc2") / "doc.pdf"
    d = fitz.open()
    d.new_page().insert_text((72, 72), "身份证号 110101196001010 00X 备案", fontname="china-s")
    d.save(p); d.close()
    e = ing.build_hardcase_entry(
        p, 0, [("身份证号", "11010119600101000X")],
        story="空格打断", origin="t", raw_forms={"11010119600101000X": "110101196001010 00X"},
    )
    assert e["entities"]["身份证号"] == ["11010119600101000X"]
    assert e["raw_forms"]["11010119600101000X"] == "110101196001010 00X"


def test_reject_bad_id_format(sample_pdf):
    with pytest.raises(ValueError, match="身份证"):
        ing.build_hardcase_entry(sample_pdf, 0, [("身份证号", "123")], story="x", origin="t",
                                 raw_forms={"123": "11010119600101000X"})


def test_cli_writes_jsonl_and_manifest(sample_pdf, tmp_path, monkeypatch, capsys):
    out_dir = tmp_path / "hardcase"
    rc = ing.main([
        "--file", str(sample_pdf), "--page", "0",
        "--entity", "姓名:张三",
        "--story", "姓名漏检", "--origin", "issue#40",
        "--out-dir", str(out_dir),
    ])
    assert rc == 0
    jsonl = out_dir / "hardcase.jsonl"
    assert jsonl.exists()
    lines = [json.loads(l) for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1 and lines[0]["entities"]["姓名"] == ["张三"]
    manifest = tmp_path / "manifest.private.json"
    assert manifest.exists()
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert m["entries"][0]["id"] == lines[0]["id"]
    assert m["entries"][0]["origin"] == "issue#40"
    out = capsys.readouterr().out
    assert "hardcase" in out and "issue#40" in out
    # 追加第二条
    ing.main(["--file", str(sample_pdf), "--page", "0", "--entity", "姓名:张三",
              "--story", "y", "--origin", "t2", "--out-dir", str(out_dir)])
    assert len(jsonl.read_text(encoding="utf-8").splitlines()) == 2
    m = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(m["entries"]) == 2


def test_cli_reject_no_partial_write(sample_pdf, tmp_path):
    out_dir = tmp_path / "hardcase"
    rc = ing.main([
        "--file", str(sample_pdf), "--page", "0",
        "--entity", "姓名:张三", "--entity", "姓名:不存在",
        "--story", "x", "--origin", "t", "--out-dir", str(out_dir),
    ])
    assert rc != 0
    assert not (out_dir / "hardcase.jsonl").exists()
    assert not (tmp_path / "manifest.private.json").exists()

