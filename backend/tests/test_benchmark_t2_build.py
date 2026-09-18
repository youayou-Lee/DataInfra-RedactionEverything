"""T2 桶构建测试：mini raw 目录（fixture 现造）驱动 build_buckets 全链路。零数据入库。Ref #93"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "eval" / "benchmarks" / "t2"))
import build_buckets  # noqa: E402
import spec  # noqa: E402


def _cluener_line(i: int) -> dict:
    return {"text": f"张伟{i}在北京市朝阳区人民法院参加了听证会。",
            "label": {"name": {f"张伟{i}": {"0": "3"}},
                      "government": {"朝阳区人民法院": {"6": "14"}},
                      "movie": {"流浪地球": {"0": "4"}}}}  # 未映射类型 -> 丢弃计数


def _resume_line(i: int) -> dict:
    return {"text": f"李雷{i}，汉族，生于内蒙古包头市，现就职于腾讯科技有限公司。",
            "label": {"NAME": {f"李雷{i}": {"0": "3"}},
                      "LOCATION": {"内蒙古包头市": {"9": "14"}},
                      "EDUCATION": {"本科": {"40": "42"}}}}


@pytest.fixture()
def mini_raw(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    (raw / "cluener").mkdir(parents=True)
    (raw / "cluener" / "train.json").write_text(
        "\n".join(json.dumps(_cluener_line(i), ensure_ascii=False) for i in range(30)) + "\n",
        encoding="utf-8")
    (raw / "resume").mkdir(parents=True)
    (raw / "resume" / "resume.train").write_text(
        "\n".join(json.dumps(_resume_line(i), ensure_ascii=False) for i in range(20)) + "\n",
        encoding="utf-8")
    return raw


def _run_build(mini_raw: Path, out_dir: Path) -> dict:
    return build_buckets.build_all(mini_raw, out_dir)


def test_bucket_files_and_schema(mini_raw, tmp_path):
    out = tmp_path / "out"
    manifest = _run_build(mini_raw, out)
    for bucket, meta in spec.BUCKETS.items():
        f = out / "buckets" / f"{bucket}.jsonl"
        if meta["kind"] == "hardcase":
            assert not f.exists()  # 跳过：不创建也不覆盖（ingest 管理）
            continue
        assert f.is_file(), bucket
        lines = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        for e in lines:
            assert spec.ENTRY_SCHEMA_KEYS <= set(e)
            assert e["bucket"] == bucket
        if meta["kind"] == "synthetic":
            assert len(lines) == meta["size"]
    # 公开集桶条数 = min(size, 候选数)
    assert len([l for l in (out / "buckets" / "cluener-person.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()]) == 30
    assert len([l for l in (out / "buckets" / "resume-person.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()]) == 20


def test_manifest_structure_and_drops(mini_raw, tmp_path):
    out = tmp_path / "out"
    manifest = _run_build(mini_raw, out)
    assert manifest["seed"] == 42
    assert "generated_at" in manifest and "sources" in manifest and "buckets" in manifest
    # cluener 每行丢弃 movie 1 个 span
    assert manifest["buckets"]["cluener-person"]["dropped_spans"]["cluener"] == 30
    # resume 每行丢弃 EDUCATION 1 个 span
    assert manifest["buckets"]["resume-person"]["dropped_spans"]["resume"] == 20
    assert manifest["buckets"]["cluener-person"]["count"] == 30
    assert manifest["buckets"]["cluener-person"]["sampled"] == 30  # 实际落桶条数
    assert manifest["buckets"]["cluener-person"]["total_candidates"] == 30  # 原候选总数另名保留
    assert manifest["sources"]["cluener"]["files"] == [
        str(mini_raw / "cluener" / "train.json")]  # 同源多桶去重，不重复出现
    assert manifest["sources"]["cluener"]["url"].startswith("https://github.com/CLUEbenchmark")
    assert manifest["buckets"]["hardcase"]["skipped"] is True
    mfile = out / "manifest.private.json"
    assert mfile.is_file() and json.loads(mfile.read_text(encoding="utf-8"))["seed"] == 42


def test_leven_blocked_message(mini_raw, tmp_path, capsys):
    out = tmp_path / "out"
    _run_build(mini_raw, out)
    err = capsys.readouterr().err
    assert "BLOCKED" in err and "leven-judicial-person" in err and "leven-judicial-org" in err
    for b in ("leven-judicial-person", "leven-judicial-org"):
        mf = json.loads((out / "manifest.private.json").read_text(encoding="utf-8"))
        assert mf["buckets"][b]["blocked"] is True


def test_missing_raw_gives_download_hint(tmp_path, capsys):
    empty = tmp_path / "raw"
    empty.mkdir()
    empty.mkdir(exist_ok=True)
    with pytest.raises(FileNotFoundError) as ei:
        build_buckets.build_all(empty, tmp_path / "out")
    msg = str(ei.value)
    assert "cluener" in msg and "CLUEbenchmark/CLUENER" in msg
    assert "https://" in msg


def test_determinism(mini_raw, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _run_build(mini_raw, a)
    _run_build(mini_raw, b)
    for f in sorted((a / "buckets").glob("*.jsonl")):
        assert f.read_text(encoding="utf-8") == (b / "buckets" / f.name).read_text(encoding="utf-8")


def test_manifest_preserves_hardcase_entries(mini_raw, tmp_path):
    out = tmp_path / "out"
    out.mkdir(parents=True)
    (out / "manifest.private.json").write_text(
        json.dumps({"entries": [{"id": "hardcase_x_001", "origin": "u", "story": "s",
                                 "ts": "2026-09-18T00:00:00"}]}, ensure_ascii=False),
        encoding="utf-8")
    manifest = _run_build(mini_raw, out)
    assert manifest["entries"][0]["id"] == "hardcase_x_001"


def test_resume_bmes_converted(tmp_path):
    """LatticeLSTM 原生 char-level BMES 自动转 jsonl（LOC->LOCATION / ORG->ORGANIZATION 归一）。"""
    raw = tmp_path / "raw"
    (raw / "resume").mkdir(parents=True)
    bmes = ("高 B-NAME\n勇 E-NAME\n： O\n男 O\n， O\n汉 B-RACE\n族 E-RACE\n， O\n"
            "内 B-LOC\n蒙 M-LOC\n古 E-LOC\n包 B-LOC\n头 E-LOC\n市 O\n"
            "腾 B-ORG\n讯 M-ORG\n公 E-ORG\n司 O\n本 B-EDU\n科 E-EDU\n\n"
            "第 B-NAME\n二 E-NAME\n句 O\n")
    (raw / "resume" / "train.char.bmes").write_text(bmes, encoding="utf-8")
    (raw / "cluener").mkdir(parents=True)  # build_all 全桶跑，cluener 也需在（单行即可）
    (raw / "cluener" / "train.json").write_text(
        json.dumps(_cluener_line(0), ensure_ascii=False) + "\n", encoding="utf-8")
    manifest = build_buckets.build_all(raw, tmp_path / "out")
    entries = [json.loads(l) for l in (tmp_path / "out" / "buckets" / "resume-person.jsonl")
               .read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(entries) == 2
    e0 = next(e for e in entries if "高勇" in e["text"])
    assert e0["entities"] == {"姓名": ["高勇"], "民族": ["汉族"], "籍贯": ["内蒙古", "包头"],
                              "工作单位": ["腾讯公"]}  # BMES 标注粒度如此
    # EDU（本科，仅第一句）未映射 -> 丢弃计数 1
    assert manifest["buckets"]["resume-person"]["dropped_spans"]["resume"] == 1
    # 旧 manifest 临时合并文件不进桶目录
    assert not (tmp_path / "out" / "buckets" / ".resume.merged.tmp").exists()


def test_public_bucket_target_focus(mini_raw, tmp_path):
    """cluener-person 桶优先取含姓名的候选（fixture 全命中，30/30）。"""
    out = tmp_path / "out"
    _run_build(mini_raw, out)
    entries = [json.loads(l) for l in (out / "buckets" / "cluener-person.jsonl")
               .read_text(encoding="utf-8").splitlines() if l.strip()]
    assert all("姓名" in e["entities"] for e in entries)
    assert all(e["source"] == "cluener" and e["bucket_kind"] == "public" for e in entries)
