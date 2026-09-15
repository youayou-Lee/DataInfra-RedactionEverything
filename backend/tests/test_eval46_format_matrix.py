"""Issue #46 格式矩阵单测：生成器 golden + 判定关卡表驱动 + 报告渲染快照（不打网）。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GEN_DIR = REPO_ROOT / "eval" / "datasets" / "generators"
SCRIPTS_DIR = REPO_ROOT / "eval" / "scripts"
FORMATS_DIR = REPO_ROOT / "eval" / "datasets" / "formats"
sys.path.insert(0, str(GEN_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(REPO_ROOT / "backend" / "scripts" / "eval"))

import format_gates as gates  # noqa: E402
import gen_rtf  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


payload_mod = _load("payload_under_test", GEN_DIR / "gen_formats_payload.py")
build_formats = _load("build_formats_under_test", GEN_DIR / "build_formats.py")
gen_image = _load("gen_image_under_test", GEN_DIR / "gen_image.py")

GT_ENTITIES = payload_mod.build_payload()["entities"]
GT_FLAT = [v for values in GT_ENTITIES.values() for v in values]


# ---------- 统一 payload ----------

def test_payload_shape_and_determinism():
    p1 = payload_mod.build_payload()
    p2 = payload_mod.build_payload()
    assert p1 == p2
    assert p1["entities"] == GT_ENTITIES
    assert set(GT_ENTITIES) == {"姓名", "身份证号", "电话", "地址"}
    assert len(GT_ENTITIES["姓名"]) == 2


# ---------- RTF 生成/解码 ----------

def test_rtf_roundtrip_and_magic():
    lines = payload_mod.build_payload()["lines"]
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "x.rtf"
        content = gen_rtf.build_rtf(path, lines=lines)
        assert content.startswith(r"{\rtf1\ansi\ansicpg936")
        decoded = gen_rtf.decode_rtf(content)
        for value in GT_FLAT:
            assert value in decoded, f"RTF 解码缺 {value}"


# ---------- 图片生成器 ----------

@pytest.mark.parametrize("ext,magic", [
    (".jpg", b"\xff\xd8\xff"), (".png", b"\x89PNG"), (".bmp", b"BM"),
    (".gif", b"GIF8"), (".webp", b"RIFF"), (".tif", b"II*"), (".tiff", b"II*"),
])
def test_image_magic_and_size(ext, magic):
    import tempfile
    import shutil
    if not shutil.which("fc-match") and not any(Path(p).exists() for p in [
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"]):
        pytest.skip("环境无 fontconfig 与已知 CJK 字体，无法渲染样图")
    lines = payload_mod.build_payload()["lines"]
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / f"x{ext}"
        info = gen_image.build_image(path, lines=lines)
        raw = path.read_bytes()
        if ext == ".webp":
            assert raw[8:12] == b"WEBP"
        else:
            assert raw.startswith(magic)
        assert info["size"] == [1240, 1754]


# ---------- 入库样张 golden（若存在）----------

@pytest.mark.skipif(not FORMATS_DIR.exists(), reason="样张未生成")
def test_committed_samples_consistency():
    gt = json.loads((FORMATS_DIR / "gt.json").read_text(encoding="utf-8"))
    assert gt["payload"]["entities"] == GT_ENTITIES
    doc = (FORMATS_DIR / "fmt_doc.doc").read_bytes()
    assert doc[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # OLE 魔数
    rtf_decoded = gen_rtf.decode_rtf((FORMATS_DIR / "fmt_rtf.rtf").read_text(encoding="ascii"))
    for value in GT_FLAT:
        assert value in rtf_decoded
    assert (FORMATS_DIR / "fmt_html.html").read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


# ---------- G2 ----------

@pytest.mark.parametrize("content,expect", [
    ("正常内容", "PASS"),
    ("", "FAIL"),
    ("   \n  ", "FAIL"),
    ("[无法解析 .doc 文件，请将文件另存为 .docx 格式后重试]", "FAIL"),
    ("[无法解析文件: x.doc]", "FAIL"),
])
def test_g2_parse(content, expect):
    assert gates.g2_parse(content)["status"] == expect


# ---------- G3 ----------

def _recognized_with(extra=()):
    return {etype: list(values) for etype, values in GT_ENTITIES.items()}, extra


def test_g3_full_hit_pass():
    recognized, _ = _recognized_with()
    result = gates.g3_recognition(recognized, GT_ENTITIES)
    assert result["status"] == "PASS"
    assert result["detail"]["recall"] == 1.0


def test_g3_missing_person_still_passes_recall():
    recognized = {k: list(v) for k, v in GT_ENTITIES.items()}
    recognized["姓名"] = recognized["姓名"][:1]  # 5 实体缺 1 → 恰好 80% 达标线
    result = gates.g3_recognition(recognized, GT_ENTITIES)
    assert result["status"] == "PASS"
    assert result["detail"]["recall"] == 0.8


def test_g3_exact_id_card_veto():
    recognized = {k: list(v) for k, v in GT_ENTITIES.items()}
    id_card = GT_ENTITIES["身份证号"][0]
    recognized["身份证号"] = [id_card[:-1] + "9"]  # 逐字不匹配
    result = gates.g3_recognition(recognized, GT_ENTITIES)
    assert result["status"] == "FAIL"
    assert result["detail"]["exact_misses"]


def test_g3_squash_normalization_hit():
    spaced_name = GT_ENTITIES["姓名"][0][:1] + " " + GT_ENTITIES["姓名"][0][1:]
    recognized = {"姓名": [spaced_name],
                  "身份证号": list(GT_ENTITIES["身份证号"]),
                  "电话": list(GT_ENTITIES["电话"]),
                  "地址": list(GT_ENTITIES["地址"])}
    result = gates.g3_recognition(recognized, GT_ENTITIES)
    assert result["status"] == "PASS"  # 姓名带空格仍算命中（squash 归一域）
    assert result["detail"]["recall"] == 0.8  # 两个姓名只识别出 1 个


# ---------- G4 ----------

def test_g4_mask_pass(tmp_path):
    integrity = {"ok": True}
    result = gates.g4_product(download_ok=True, residual_originals=[],
                              integrity=integrity)
    assert result["status"] == "PASS"


def test_g4_residual_is_hard():
    result = gates.g4_product(download_ok=True, residual_originals=["张三"],
                              integrity={"ok": True})
    assert result["status"] == "FAIL"
    assert "成品残留" in result["detail"]["problems"][0]


def test_g4_pseudonym_missing_alias():
    check = gates.check_pseudonym_mapping({"张三": "李四"}, ["张三", "王五"])
    assert not check["ok"] and len(check["missing"]) == 1


# ---------- fail_class 分类（评审 Critical：残留/上传拒必须 hard）----------

_G4 = lambda problems: {"status": "FAIL", "detail": {"problems": problems}}


@pytest.mark.parametrize("gates_map,expect", [
    # G1 上传被拒 / G2 解析兜底 → hard
    ({"g1": {"status": "FAIL", "detail": {"reason": "上传被拒: HTTP 400"}}}, "hard"),
    ({"g2": {"status": "FAIL", "detail": {"reason": "命中解析兜底文案"}}}, "hard"),
    # 三路残留任一 → hard（本地 grep / execute 自检 / 复扫）
    ({"g4": _G4(["成品残留原文 2 处: ['x']"])}, "hard"),
    ({"g4": _G4(["execute 自检报残留 1 处: ['x']"])}, "hard"),
    ({"g4": _G4(["复扫发现原文残留 3 处: ['x']"])}, "hard"),
    # 成品下载失败 / 载体损坏 / 复扫执行失败 → hard
    ({"g4": _G4(["成品下载失败"])}, "hard"),
    ({"g4": _G4(["载体完整性校验失败: {...}"])}, "hard"),
    ({"g4": _G4(["复扫执行失败，无法确认零残留: ValueError"])}, "hard"),
    # 化名对照/召回不足（无残留）→ soft
    ({"g3": {"status": "FAIL", "detail": {"reason": "召回 40% < 80%"}},
      "g4": _G4(["化名对照缺 1 项"])}, "soft"),
])
def test_classify_fail(gates_map, expect):
    from run_format_matrix import CellRunner
    assert CellRunner._classify_fail(gates_map) == expect


def test_grep_residual_normalization():
    product = "委托人：张 三，身份证号：110101199001011234。"
    assert gates.grep_residual(product, ["张三"]) == ["张三"]
    assert gates.grep_residual("成品干净", ["张三"]) == []


# ---------- 三档聚合 ----------

def _cell(mode, status, fail_class=None):
    return {"mode": mode, "status": status, "fail_class": fail_class}


@pytest.mark.parametrize("cells,expect", [
    ([_cell("mask", "PASS"), _cell("pseudonym", "PASS")], "承诺支持"),
    ([_cell("mask", "FAIL", "hard"), _cell("pseudonym", "SKIP")], "前端禁用"),
    ([_cell("mask", "PASS"), _cell("pseudonym", "FAIL", "soft")], "实验性"),
    ([_cell("mask", "FAIL", "soft")], "实验性"),
    ([_cell("mask", "FAIL", "hard")], "前端禁用"),
    ([_cell("mask", "SKIP"), _cell("pseudonym", "SKIP")], "无有效格子"),
    # ERROR 未裁决 ≠ 通过：不得给承诺支持绿灯（评审 Important）
    ([_cell("mask", "PASS"), _cell("pseudonym", "ERROR")], "待定（含ERROR未重跑）"),
    ([_cell("mask", "PASS"), _cell("pseudonym", "SKIP"), _cell("mask2", "ERROR")], "待定（含ERROR未重跑）"),
])
def test_aggregate_tiers(cells, expect):
    assert gates.aggregate_format(cells) == expect


# ---------- 离线编排测试（stub API，不打网；抓接线类 bug）----------

class _R:
    def __init__(self, status_code=200, payload=None, text="", content=b"CLEAN"):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.content = content

    def json(self):
        return self._payload


def _make_stub_api():
    """最小桩 EvalApi：doc parse 走兜底，其余全通（供编排类测试复用）。"""
    GT_FLAT = sum(GT_ENTITIES.values(), [])

    class StubClient:
        def __init__(self, outer):
            self.outer = outer

        def get(self, url, params=None):
            if "/parse" in url:
                if "fmt_doc" in url:  # 仅 .doc 的 parse 走兜底（按 file_id 路由，无顺序耦合）
                    return _R(200, {"content": "[无法解析 .doc 文件，请将文件另存为 .docx 格式后重试]"})
                return _R(200, {"content": "委托人：" + GT_FLAT[0]})
            if "/download" in url:
                return _R(200, {"file_id": "x"}, content=b"CLEAN")
            return _R(200, {})

        def post(self, url, json=None):
            if "/files/upload" in url:  # 异常例：结构化 400
                return _R(400, {}, text='{"message":"文件过大，最大支持 50MB"}')
            if "/ner/hybrid" in url:
                ents = [{"id": f"e{i}", "text": t, "type": "PERSON", "start": 0, "end": 1}
                        for i, t in enumerate(GT_FLAT)]
                return _R(200, {"entities": ents, "recognition_failed": False})
            if "/redaction/execute" in url:
                return _R(200, {"output_file_id": "out-1",
                                "entity_map": {t: f"替{i}" for i, t in enumerate(GT_FLAT)},
                                "residual_entities": []})
            return _R(200, {})

        def delete(self, url):
            return _R(204, {})

    class StubApi:
        def __init__(self, *a, **kw):
            self.client = StubClient(self)
            self.deleted = []

        def upload(self, path):
            return f"fid-{path.name}"

        def vision(self, file_id, page, force=True, **kw):
            boxes = [{"id": f"b{i}", "x": 0.1, "y": 0.2 + i * 0.05, "width": 0.3,
                      "height": 0.04, "page": 1, "type": "PERSON", "text": t, "selected": True}
                     for i, t in enumerate(GT_FLAT)]
            return {"bounding_boxes": boxes, "pipeline_status": {}}

        def parse_and_hybrid_ner(self, file_id):
            return {"姓名": list(GT_ENTITIES["姓名"])}, 0.1

        def delete_file(self, fid):
            self.deleted.append(fid)
            return True

        def close(self):
            pass

    return StubApi()


def test_run_suite_offline_smoke(tmp_path, monkeypatch):
    """stub EvalApi 走通 run_suite 全链路：抓接线类 bug（UnboundLocal/AttributeError/
    fail_class 未归类）；不追求 stub 下业务全 PASS，聚焦结构完整性。"""
    import run_format_matrix as rfm

    api = _make_stub_api()
    data = rfm.run_suite(api, suite="smoke", workdir=tmp_path, cleanup=True)

    assert len(data["cells"]) == 6 and len(data["anomalies"]) == 2
    doc_mask = next(c for c in data["cells"] if c["cell_id"] == "doc×mask")
    assert doc_mask["status"] == "FAIL" and doc_mask["fail_class"] == "hard", \
        f"G2 兜底必须归类 hard（_classify_fail 接线存在且生效）: {doc_mask}"
    assert data["tiers"]["doc"] == "前端禁用"
    # 评审 Critical 锁：stub 下 rtf 复扫命中姓名 → 「复扫发现原文残留」必须归类 hard，
    # 三档结论必须是前端禁用（残留格式不得降级为实验性）
    rtf_mask = next(c for c in data["cells"] if c["cell_id"] == "rtf×mask")
    assert rtf_mask["status"] == "FAIL" and rtf_mask["fail_class"] == "hard", \
        f"复扫残留必须 hard: {rtf_mask['fail_class']} / {rtf_mask['gates'].get('g4', {}).get('detail')}"
    assert data["tiers"]["rtf"] == "前端禁用"
    doc_pseudo = next(c for c in data["cells"] if c["cell_id"] == "doc×pseudonym")
    assert doc_pseudo["status"] == "SKIP"
    wiring_errors = [c["error"] for c in data["cells"]
                     if c.get("error") and ("AttributeError" in c["error"]
                                            or "UnboundLocalError" in c["error"]
                                            or "TypeError" in c["error"]
                                            or "KeyError" in c["error"])]
    assert not wiring_errors, f"接线类 bug: {wiring_errors}"
    assert data["summary"]["cells_error"] == 0, \
        f"stub 环境不允许非接线 ERROR: {[c['error'] for c in data['cells'] if c['error']]}"


def test_run_suite_rescan_failure_is_hard_fail(tmp_path, monkeypatch):
    """复审 Critical 回归锁：复扫执行失败必须落到 G4 FAIL(hard)，不得因接线
    bug（未赋值变量等）逃逸成整格 ERROR——那样泄漏格式会变「待定」而非「前端禁用」。"""
    import run_format_matrix as rfm

    def _boom(*a, **kw):
        raise RuntimeError("rescan upload connection reset")

    monkeypatch.setattr(rfm.leak_check, "run_rescan", _boom)
    data = rfm.run_suite(_make_stub_api(), suite="smoke", workdir=tmp_path, cleanup=True)

    rtf_mask = next(c for c in data["cells"] if c["cell_id"] == "rtf×mask")
    assert rtf_mask["status"] == gates.STATUS_FAIL and rtf_mask["error"] is None, \
        f"复扫失败必须 FAIL 而非 ERROR: {rtf_mask}"
    assert rtf_mask["fail_class"] == "hard"
    problems = rtf_mask["gates"]["g4"]["detail"]["problems"]
    assert any("复扫执行失败" in p for p in problems), problems
    assert data["tiers"]["rtf"] == "前端禁用"
    assert data["summary"]["cells_error"] == 0


# ---------- 报告渲染快照 ----------

def test_render_md_snapshot():
    from format_report import render_md
    data = {
        "meta": {"base_url": "http://inst:8000", "suite": "smoke", "env": "ut",
                 "started_at": "2026-09-15T10:00:00", "finished_at": "2026-09-15T10:10:00"},
        "summary": {"cells_total": 2, "cells_pass": 1, "cells_fail": 1, "cells_skip": 0,
                    "cells_error": 0, "anomalies_pass": 1, "anomalies_total": 1, "tiers_pass": 0},
        "tiers": {"rtf": "前端禁用", "jpg": "承诺支持"},
        "cells": [
            {"cell_id": "jpg×mask", "format": "jpg", "mode": "mask", "status": "PASS",
             "g3_recall": 1.0, "wall_s": 12.3,
             "gates": {"g1": {"status": "PASS", "detail": {}},
                       "g2": {"status": "PASS", "detail": {"boxes": 5}},
                       "g3": {"status": "PASS", "detail": {"recall": 1.0}},
                       "g4": {"status": "PASS", "detail": {}}}},
            {"cell_id": "rtf×mask", "format": "rtf", "mode": "mask", "status": "FAIL",
             "fail_class": "hard", "g3_recall": 0.0, "wall_s": 3.0,
             "gates": {"g1": {"status": "PASS", "detail": {}},
                       "g2": {"status": "FAIL", "detail": {"reason": "命中解析兜底文案"}}}},
        ],
        "anomalies": [{"case_id": "oversize", "expect": "明确拒绝", "status": "PASS",
                       "observations": ["upload HTTP 413（Payload Too Large）"]}],
    }
    md = render_md(data)
    assert "一句话诊断" in md
    assert "建议前端禁用**（rtf）" in md
    assert "| rtf |" in md and "前端禁用" in md
    assert "| jpg |" in md and "承诺支持" in md
    assert "upload HTTP 413" in md
    assert "命中解析兜底文案" in md  # 失败明细保留证据


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
