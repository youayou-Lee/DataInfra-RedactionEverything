"""Issue #46 上传格式支持面端到端实测——格式 × 处理方式矩阵编排。

用法（设计文档 §6）：
  python eval/scripts/run_format_matrix.py --base-url http://<实例>:<port> --smoke
  python eval/scripts/run_format_matrix.py --base-url http://<实例>:<port> --suite full

流程（镜像 playground 生产链路）：文本类 = 上传 → parse(G2) → hybrid NER(G3，
原始实体含 start/end) → execute(G4)；扫描 PDF/图片 = 上传 → vision 检测(G2/G3) →
execute 按 bounding_boxes 打码(G4)。判定关卡见 format_gates.py（纯函数）；
成品复扫复用 leak_check.run_rescan。样张与 GT 在 eval/datasets/formats/（全合成虚构）。

测试文件用完即删（--cleanup 默认开）；报告落 eval/reports/<ts>-<env>-format-matrix.{json,md}。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common_api  # noqa: E402
import format_gates as gates  # noqa: E402
import leak_check  # noqa: E402
from format_report import render_md  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
FORMATS_DIR = _REPO_ROOT / "eval" / "datasets" / "formats"
REPORTS_DIR = _REPO_ROOT / "eval" / "reports"

# 矩阵（设计文档 §2）：format_id → 样张 / 路径类别 / 处理方式 / 分组
MATRIX: dict[str, dict] = {
    "txt":         {"sample": "fmt_txt.txt",         "kind": "text",        "modes": ["mask", "pseudonym"], "group": "基线"},
    "docx":        {"sample": "fmt_docx.docx",       "kind": "text",        "modes": ["mask", "pseudonym"], "group": "基线"},
    "text_pdf":    {"sample": "fmt_text_pdf.pdf",    "kind": "pdf_text",    "modes": ["mask", "pseudonym"], "group": "基线"},
    "scanned_pdf": {"sample": "fmt_scanned_pdf.pdf", "kind": "pdf_scanned", "modes": ["mask"], "group": "基线"},
    "md":          {"sample": "fmt_md.md",           "kind": "text",        "modes": ["mask", "pseudonym"], "group": "文本类"},
    "html":        {"sample": "fmt_html.html",       "kind": "text",        "modes": ["mask", "pseudonym"], "group": "文本类"},
    "htm":         {"sample": "fmt_htm.htm",         "kind": "text",        "modes": ["mask", "pseudonym"], "group": "文本类"},
    "rtf":         {"sample": "fmt_rtf.rtf",         "kind": "text",        "modes": ["mask", "pseudonym"], "group": "文本类（P1 风险：\\uN 转义）"},
    "doc":         {"sample": "fmt_doc.doc",         "kind": "text",        "modes": ["mask", "pseudonym"], "group": "文本类（P1 风险：LibreOffice 依赖）"},
    "jpg":         {"sample": "fmt_jpg.jpg",         "kind": "image",       "modes": ["mask"], "group": "图片类"},
    "jpeg":        {"sample": "fmt_jpeg.jpeg",       "kind": "image",       "modes": ["mask"], "group": "图片类"},
    "png":         {"sample": "fmt_png.png",         "kind": "image",       "modes": ["mask"], "group": "图片类"},
    "bmp":         {"sample": "fmt_bmp.bmp",         "kind": "image",       "modes": ["mask"], "group": "图片类（P2 风险）"},
    "gif":         {"sample": "fmt_gif.gif",         "kind": "image",       "modes": ["mask"], "group": "图片类（P2 风险）"},
    "webp":        {"sample": "fmt_webp.webp",       "kind": "image",       "modes": ["mask"], "group": "图片类（P2 风险）"},
    "tif":         {"sample": "fmt_tif.tif",         "kind": "image",       "modes": ["mask"], "group": "图片类（P2 风险）"},
    "tiff":        {"sample": "fmt_tiff.tiff",       "kind": "image",       "modes": ["mask"], "group": "图片类（P2 风险）"},
}

SMOKE_FORMATS = ["doc", "rtf", "jpg", "png"]
SMOKE_ANOMALIES = ["fake_ext_pdf", "oversize"]

# 成品落盘扩展名（execute 产物与源文件同载体）
PRODUCT_SUFFIX = {
    "txt": ".txt", "md": ".md", "html": ".html", "htm": ".htm", "rtf": ".rtf",
    "docx": ".docx", "doc": ".doc", "text_pdf": ".pdf", "scanned_pdf": ".pdf",
    "jpg": ".jpg", "jpeg": ".jpeg", "png": ".png", "bmp": ".bmp",
    "gif": ".gif", "webp": ".webp", "tif": ".tif", "tiff": ".tiff",
}


def _execute_config(mode: str) -> dict:
    config = {"replacement_mode": "structured" if mode == "mask" else "pseudonym",
              "entity_types": [], "custom_replacements": {}}
    if mode == "mask":
        config["image_redaction_method"] = "mosaic"
    return config


class CellRunner:
    """单格执行器：G1→G4 逐关短路，测试文件用完即删。"""

    def __init__(self, api: common_api.EvalApi, gt: dict, workdir: Path, cleanup: bool = True):
        self.api = api
        self.gt_entities: dict[str, list[str]] = gt["payload"]["entities"]
        self.expect_size = tuple(gt["canvas"]["size"])
        self.workdir = workdir
        self.cleanup = cleanup
        self._touched: list[str] = []

    # ---- 对外入口 ----

    def run_cell(self, format_id: str, mode: str) -> dict:
        spec = MATRIX[format_id]
        sample = FORMATS_DIR / spec["sample"]
        cell: dict = {"cell_id": f"{format_id}×{mode}", "format": format_id, "mode": mode,
                      "group": spec["group"], "status": gates.STATUS_ERROR, "fail_class": None,
                      "gates": {}, "g3_recall": None, "wall_s": 0.0, "error": None}
        t0 = time.perf_counter()
        try:
            gr = self._run_gates(sample, format_id, mode, cell)
            cell["gates"] = gr
            failed = [g for g, r in gr.items() if r["status"] == gates.STATUS_FAIL]
            if failed:
                cell["status"] = gates.STATUS_FAIL
                cell["fail_class"] = self._classify_fail(gr)
            else:
                cell["status"] = gates.STATUS_PASS
        except Exception as exc:  # 脚本侧异常（网络/超时）：ERROR，不算格式 FAIL
            cell["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        finally:
            cell["wall_s"] = round(time.perf_counter() - t0, 2)
            self._cleanup()
        return cell

    def run_anomaly(self, case_id: str, artifact: Path, expect: str) -> dict:
        """异常路径：结构化拒绝/提示=PASS；5xx 或静默空产物=FAIL。"""
        rec: dict = {"case_id": case_id, "expect": expect, "status": gates.STATUS_ERROR,
                     "observations": [], "error": None}
        file_id = None
        try:
            try:
                with artifact.open("rb") as f:
                    r = self.api.client.post(
                        "/api/v1/files/upload",
                        files={"file": (artifact.name, f, "application/octet-stream")})
                ok = r.status_code in (200, 201)
                if ok:
                    file_id = r.json().get("file_id")
                rec["observations"].append(f"upload HTTP {r.status_code}"
                                           + ("" if ok else f"（{r.text[:120]}）"))
            except Exception as exc:
                rec["observations"].append(f"upload 异常: {str(exc)[:150]}")
                r = None

            if r is not None and r.status_code >= 500:
                rec["status"] = gates.STATUS_FAIL
            elif r is not None and r.status_code >= 400:
                rec["status"] = gates.STATUS_PASS  # 4xx 结构化拒绝
            elif file_id:
                # 上传成功：看解析/视觉是否给出结构化提示（兜底文案/显式错误）
                suffix = artifact.suffix.lower()
                if suffix in (".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"):
                    try:
                        resp = self.api.vision(file_id, 1)
                        texts = _vision_texts(resp)
                        rec["observations"].append(f"vision 200，检出 {len(texts)} 框")
                        rec["status"] = gates.STATUS_FAIL  # 损坏文件静默识别成功
                    except RuntimeError as exc:
                        rec["observations"].append(f"vision 结构化错误: {str(exc)[:150]}")
                        rec["status"] = gates.STATUS_PASS
                else:
                    pr = self.api.client.get(f"/api/v1/files/{file_id}/parse")
                    content = str(pr.json().get("content") or "") if pr.status_code == 200 else ""
                    rec["observations"].append(f"parse HTTP {pr.status_code}，content {len(content)} 字")
                    if pr.status_code >= 500:
                        rec["status"] = gates.STATUS_FAIL
                    elif pr.status_code >= 400 or any(m in content for m in gates.FALLBACK_MARKERS):
                        rec["status"] = gates.STATUS_PASS
                    elif content.strip():
                        rec["status"] = gates.STATUS_PASS  # 内容可解出（伪扩展名实际可处理）
                    else:
                        rec["status"] = gates.STATUS_FAIL  # 静默空
            else:
                rec["status"] = gates.STATUS_FAIL
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        finally:
            if file_id:
                self._touched.append(file_id)
            self._cleanup()
        return rec

    # ---- 关卡 ----

    @staticmethod
    def _classify_fail(gr: dict) -> str:
        """hard = 5xx/解析崩溃/成品损坏/原文残留/解析兜底；其余（化名/召回不足）= soft。"""
        g2 = gr.get("g2", {})
        if g2.get("status") == gates.STATUS_FAIL:
            return "hard"
        g4_detail = gr.get("g4", {}).get("detail", {})
        for problem in g4_detail.get("problems", []):
            if problem.startswith(("成品下载", "成品残留", "载体完整性")):
                return "hard"
        return "soft"

    def _run_gates(self, sample: Path, format_id: str, mode: str, cell: dict) -> dict:
        kind = MATRIX[format_id]["kind"]
        gr: dict[str, dict] = {}

        file_id, gr["g1"] = self._g1(sample)
        if gr["g1"]["status"] != gates.STATUS_PASS:
            return gr

        boxes: list[dict] = []
        if kind in ("text", "pdf_text"):
            gr["g2"], raw_entities = self._g2_parse(file_id)
        else:
            gr["g2"], raw_entities, boxes = self._g2_vision(file_id)
        if gr["g2"]["status"] != gates.STATUS_PASS:
            return gr

        if kind in ("text", "pdf_text"):
            recognized = _group_raw(raw_entities)
        else:
            recognized = {"检出": _vision_texts_from_boxes(boxes)}
        g3 = gates.g3_recognition(recognized, self.gt_entities)
        gr["g3"] = g3
        cell["g3_recall"] = g3.get("detail", {}).get("recall")

        gr["g4"] = self._g4(file_id, format_id, mode, kind, raw_entities, boxes)
        return gr

    def _g1(self, sample: Path) -> tuple[str | None, dict]:
        try:
            file_id = self.api.upload(sample)
            self._touched.append(file_id)
            return file_id, gates.g1_upload(True, file_id)
        except Exception as exc:
            detail = {"reason": f"上传异常: {str(exc)[:200]}"}
            return None, {"status": gates.STATUS_FAIL, "detail": detail}

    def _g2_parse(self, file_id: str) -> tuple[dict, list[dict]]:
        r = self.api.client.get(f"/api/v1/files/{file_id}/parse")
        if r.status_code != 200:
            return gates.gate_result(gates.STATUS_FAIL, {"reason": f"parse HTTP {r.status_code}: {r.text[:150]}"}), []
        content = str(r.json().get("content") or "")
        g2 = gates.g2_parse(content)
        if g2["status"] != gates.STATUS_PASS:
            return g2, []
        nr = self.api.client.post(f"/api/v1/files/{file_id}/ner/hybrid", json={})
        if nr.status_code != 200:
            return gates.gate_result(gates.STATUS_FAIL, {"reason": f"ner/hybrid HTTP {nr.status_code}: {nr.text[:150]}"}), []
        body = nr.json()
        if body.get("recognition_failed"):
            return gates.gate_result(gates.STATUS_FAIL, {"reason": f"recognition_failed: {str(body.get('error'))[:150]}"}), []
        return g2, body.get("entities") or []

    def _g2_vision(self, file_id: str) -> tuple[dict, list[dict], list[dict]]:
        resp = self.api.vision(file_id, 1)
        boxes = resp.get("bounding_boxes") or []
        if not boxes:
            return gates.gate_result(gates.STATUS_FAIL, {"reason": "vision 零检出（bounding_boxes 空）"}), [], []
        return gates.gate_result(gates.STATUS_PASS, {"boxes": len(boxes)}), [], boxes

    def _g4(self, file_id: str, format_id: str, mode: str, kind: str,
            raw_entities: list[dict], boxes: list[dict]) -> dict:
        # execute：文本类带原始实体（start/end 保真），视觉类带 bounding_boxes
        entities_payload = [
            {"id": str(e.get("id") or f"e{i}"), "text": str(e.get("text") or ""),
             "type": str(e.get("type") or ""), "start": int(e.get("start") or 0),
             "end": int(e.get("end") or 0), "selected": True}
            for i, e in enumerate(raw_entities)]
        exec_payload = {"file_id": file_id, "entities": entities_payload,
                        "bounding_boxes": boxes or [],
                        "config": _execute_config(mode)}
        r = self.api.client.post("/api/v1/redaction/execute", json=exec_payload)
        if r.status_code != 200:
            return gates.gate_result(gates.STATUS_FAIL, {"problems": [f"execute HTTP {r.status_code}: {r.text[:150]}"]})
        body = r.json()
        entity_map = body.get("entity_map") or {}
        residual_entities = body.get("residual_entities") or []
        if residual_entities:
            return gates.gate_result(gates.STATUS_FAIL, {"problems": [
                f"execute 自检报残留 {len(residual_entities)} 处: {residual_entities[:3]}"]})

        # 下载成品
        out_path = self.workdir / f"product_{format_id}_{mode}{PRODUCT_SUFFIX[format_id]}"
        dr = self.api.client.get(f"/api/v1/files/{file_id}/download", params={"redacted": "true"})
        if dr.status_code != 200 or not dr.content:
            return gates.gate_result(gates.STATUS_FAIL, {"problems": [f"成品下载 HTTP {dr.status_code}"]})
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(dr.content)
        if file_id not in self._touched:
            self._touched.append(file_id)
        output_file_id = str(body.get("output_file_id") or "")
        if output_file_id:
            self._touched.append(output_file_id)

        problems: list[str] = []
        originals = [v for values in self.gt_entities.values() for v in values]

        # 载体完整性
        integrity = self._integrity(out_path, format_id)
        if not integrity.get("ok"):
            problems.append(f"载体完整性校验失败: {json.dumps(integrity, ensure_ascii=False)[:200]}")

        # 本地归一 grep 残留（二进制载体解不出文本时跳过，复扫兜底）
        product_text: str | None
        try:
            product_text = leak_check.extract_text(out_path)
        except ValueError:
            product_text = None
        if product_text is not None:
            residual = gates.grep_residual(product_text, originals)
            if residual:
                problems.append(f"成品残留原文 {len(residual)} 处: {residual[:3]}")

        # 化名格：对照自证 + 替换词确实写入成品
        pseudo_check: dict | None = None
        if mode == "pseudonym":
            sent_originals = [e["text"] for e in entities_payload if e["text"]]
            missing = [o for o in sent_originals if not entity_map.get(o)]
            replaced_in_product = []
            if product_text is not None:
                replaced_in_product = [o for o, alias in entity_map.items()
                                       if alias and alias not in product_text]
            pseudo_check = {"ok": not missing and not replaced_in_product,
                            "missing": missing, "not_in_product": replaced_in_product,
                            "mapped": len(entity_map)}
            if missing:
                problems.append(f"化名对照缺 {len(missing)} 项: {missing[:3]}")
            if replaced_in_product:
                problems.append(f"对照表化名 {len(replaced_in_product)} 项未写入成品: {replaced_in_product[:3]}")

        # API 复扫（设计文档 G4 口径）：化名成品扫原文；打码成品扫 GT 原文
        mapping_rows = [{"原文": o, "类型": "", "化名": entity_map.get(o, "")} for o in originals]
        rescan = leak_check.run_rescan(self.api, out_path, mapping_rows)
        if not rescan.get("clean"):
            findings = [f.get("原文") for f in rescan.get("findings", [])][:3]
            problems.append(f"复扫发现原文残留 {len(rescan.get('findings', []))} 处: {findings}")

        detail = {"integrity": integrity, "pseudonym": pseudo_check,
                  "rescan_pages": rescan.get("pages"), "rescan_findings": rescan.get("findings", [])}
        if problems:
            return gates.gate_result(gates.STATUS_FAIL, detail | {"problems": problems})
        return gates.gate_result(gates.STATUS_PASS, detail)

    def _integrity(self, path: Path, format_id: str) -> dict:
        kind = MATRIX[format_id]["kind"]
        try:
            if format_id == "docx":
                return gates.check_docx_integrity(path)
            if kind in ("pdf_text", "pdf_scanned"):
                return gates.check_pdf_integrity(path, expect_pages=1)
            if kind == "image":
                return gates.check_image_integrity(path, expect_size=self.expect_size)
            return {"ok": True, "note": f"{format_id} 无载体完整性校验（文本族）"}
        except Exception as exc:  # 成品损坏/解不开 = 完整性 FAIL，不是脚本 ERROR
            return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}

    # ---- 清理 ----

    def _cleanup(self) -> None:
        if not self.cleanup:
            return
        for fid in dict.fromkeys(self._touched):
            try:
                self.api.delete_file(fid)
            except Exception as exc:  # 网络抖动不杀跑批；合成数据残留无害，末尾统一清点
                print(f"    [清理] file {fid} 删除失败（尽力而为）: {str(exc)[:120]}")
        self._touched = []


def _group_raw(raw_entities: list[dict]) -> dict[str, list[str]]:
    """ner/hybrid 原始实体 → {类型中文名: [texts]}（common_api 同口径）。"""
    grouped: dict[str, list[str]] = {}
    for e in raw_entities:
        text = str(e.get("text") or "").strip()
        if not text:
            continue
        type_id = str(e.get("type") or "")
        grouped.setdefault(common_api.TYPE_ID_TO_NAME.get(type_id, type_id), []).append(text)
    return grouped


def _vision_texts(resp: dict) -> list[str]:
    return [str(b.get("text") or "").strip() for b in resp.get("bounding_boxes") or []
            if str(b.get("text") or "").strip()]


def _vision_texts_from_boxes(boxes: list[dict]) -> list[str]:
    return [str(b.get("text") or "").strip() for b in boxes if str(b.get("text") or "").strip()]


def _build_anomalies(workdir: Path) -> dict[str, tuple[Path, str]]:
    """异常路径样件（设计文档 §2）：截断 docx / 伪扩展名 / 截断 png / 超 50MB / 空文件。"""
    docx = (FORMATS_DIR / "fmt_docx.docx").read_bytes()
    png = (FORMATS_DIR / "fmt_png.png").read_bytes()
    cases = {
        "truncated_docx": (workdir / "anom_truncated.docx", "截断 docx → 结构化错误"),
        "fake_ext_pdf": (workdir / "anom_fake.pdf", "txt 内容伪造 .pdf 扩展名"),
        "truncated_png": (workdir / "anom_truncated.png", "截断 png → 结构化错误"),
        "empty_txt": (workdir / "anom_empty.txt", "0 字节 txt"),
    }
    for cid, (path, _) in cases.items():
        path.parent.mkdir(parents=True, exist_ok=True)
    (workdir / "anom_truncated.docx").write_bytes(docx[: len(docx) // 2])
    (workdir / "anom_fake.pdf").write_bytes((FORMATS_DIR / "fmt_txt.txt").read_bytes())
    (workdir / "anom_truncated.png").write_bytes(png[: len(png) // 2])
    (workdir / "anom_empty.txt").write_bytes(b"")
    oversize = workdir / "anom_oversize.txt"
    if not oversize.exists() or oversize.stat().st_size != 51 * 1024 * 1024:
        oversize.write_bytes(b"\x00" * (51 * 1024 * 1024))
    cases["oversize"] = (oversize, "51MB 超 50MB 上限 → 明确拒绝")
    return cases


def run_suite(api: common_api.EvalApi, *, suite: str, workdir: Path, cleanup: bool,
              formats_filter: list[str] | None = None,
              partial_out: Path | None = None) -> dict:
    gt = json.loads((FORMATS_DIR / "gt.json").read_text(encoding="utf-8"))
    runner = CellRunner(api, gt, workdir, cleanup=cleanup)

    def _persist(cells: list[dict], anomalies: list[dict]) -> None:
        """逐格落盘：隧道抖动/意外中断时不丢已完成格子。"""
        if partial_out is None:
            return
        tiers_now = {f: gates.aggregate_format(cs) for f, cs in _group_by_format(cells).items()}
        partial_out.write_text(json.dumps({
            "suite": suite, "cells": cells, "anomalies": anomalies,
            "tiers": tiers_now, "partial": True,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    formats = list(MATRIX) if suite == "full" else SMOKE_FORMATS
    if formats_filter:
        unknown = [f for f in formats_filter if f not in MATRIX]
        if unknown:
            raise ValueError(f"未知格式: {unknown}（可选: {list(MATRIX)}）")
        formats = formats_filter
    anomaly_ids = list(_build_anomalies(workdir)) if suite == "full" else SMOKE_ANOMALIES
    cells: list[dict] = []
    anomalies: list[dict] = []
    for format_id in formats:
        spec = MATRIX[format_id]
        prev_status: dict[str, str] = {}
        for mode in spec["modes"]:
            # SKIP 规则：同格式打码格 G1/G2 挂 → 化名格 SKIP（上游不可用）
            upstream_broken = prev_status.get("g1") == gates.STATUS_FAIL or \
                prev_status.get("g2") == gates.STATUS_FAIL
            if upstream_broken and mode == "pseudonym":
                cells.append({"cell_id": f"{format_id}×{mode}", "format": format_id, "mode": mode,
                              "group": spec["group"], "status": gates.STATUS_SKIP,
                              "fail_class": None, "gates": {}, "g3_recall": None,
                              "wall_s": 0.0, "error": None,
                              "note": "同格式打码格 G1/G2 FAIL，上游不可用"})
                print(f"  [格] {format_id}×{mode}: SKIP（上游不可用）")
                continue
            cell = runner.run_cell(format_id, mode)
            prev_status = {g: r["status"] for g, r in cell["gates"].items()}
            cells.append(cell)
            _persist(cells, anomalies)
            _print_cell(cell)

    anomalies = []
    cases = _build_anomalies(workdir)
    if not formats_filter:  # --formats 重跑单格式时不重复异常例
        for cid in (anomaly_ids if suite == "full" else SMOKE_ANOMALIES):
            if cid not in cases:
                continue
            path, expect = cases[cid]
            rec = runner.run_anomaly(cid, path, expect)
            anomalies.append(rec)
            _persist(cells, anomalies)
            print(f"  [异常] {cid}: {rec['status']}"
                  + (f"（{rec['observations'][-1]}）" if rec["observations"] else ""))

    tiers = {}
    for format_id, cells_of in _group_by_format(cells).items():
        tiers[format_id] = gates.aggregate_format(cells_of)

    return {
        "suite": suite, "cells": cells, "anomalies": anomalies, "tiers": tiers,
        "summary": _summary(cells, anomalies, tiers),
    }


def _group_by_format(cells: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for cell in cells:
        grouped.setdefault(cell["format"], []).append(cell)
    return grouped


def _summary(cells: list[dict], anomalies: list[dict], tiers: dict) -> dict:
    passed = sum(1 for c in cells if c["status"] == gates.STATUS_PASS)
    failed = sum(1 for c in cells if c["status"] == gates.STATUS_FAIL)
    return {
        "cells_total": len(cells), "cells_pass": passed, "cells_fail": failed,
        "cells_skip": sum(1 for c in cells if c["status"] == gates.STATUS_SKIP),
        "cells_error": sum(1 for c in cells if c["status"] == gates.STATUS_ERROR),
        "anomalies_pass": sum(1 for a in anomalies if a["status"] == gates.STATUS_PASS),
        "anomalies_total": len(anomalies),
        "tiers_pass": sum(1 for t in tiers.values() if t == "承诺支持"),
    }


def _print_cell(cell: dict) -> None:
    line = f"  [格] {cell['cell_id']}: {cell['status']}"
    if cell.get("g3_recall") is not None:
        line += f"（召回 {cell['g3_recall']:.0%}）"
    line += f" {cell['wall_s']}s"
    fails = [g for g, r in cell["gates"].items() if r["status"] == gates.STATUS_FAIL]
    if fails:
        line += f" ← FAIL@{','.join(fails)}"
    print(line)


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue #46 格式矩阵实测（scnet-main）")
    parser.add_argument("--base-url", required=True, help="实例 backend 地址（http://host:port）")
    parser.add_argument("--user", default="apitester")
    parser.add_argument("--password", default="", help="缺省读 EVAL46_PASSWORD 环境变量")
    parser.add_argument("--suite", choices=["smoke", "full"], default="smoke")
    parser.add_argument("--env", default="scnet-main", help="报告文件名环境段")
    parser.add_argument("--workdir", default=None, help="成品/异常样件目录（默认 eval/.format-matrix-tmp）")
    parser.add_argument("--formats", default=None, help="逗号分隔格式过滤（如 rtf,doc），重跑用")
    parser.add_argument("--keep-files", action="store_true", help="保留实例测试文件（默认用完即删）")
    args = parser.parse_args()

    import os
    password = args.password or os.environ.get("EVAL46_PASSWORD")
    if not password:
        parser.error("需要 --password 或 EVAL46_PASSWORD")

    workdir = Path(args.workdir) if args.workdir else _REPO_ROOT / "eval" / ".format-matrix-tmp"
    workdir.mkdir(parents=True, exist_ok=True)

    api = common_api.EvalApi(args.base_url, args.user, password)
    started = datetime.now()
    print(f"== Issue #46 格式矩阵 suite={args.suite} -> {args.base_url} ==")
    try:
        data = run_suite(api, suite=args.suite, workdir=workdir, cleanup=not args.keep_files,
                         formats_filter=args.formats.split(",") if args.formats else None,
                         partial_out=workdir / "partial-matrix.json")
    finally:
        api.close()

    ts = started.strftime("%Y%m%d-%H%M%S")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORTS_DIR / f"{ts}-{args.env}-format-matrix.json"
    data["meta"] = {"base_url": args.base_url, "suite": args.suite, "env": args.env,
                    "started_at": started.isoformat(timespec="seconds"),
                    "finished_at": datetime.now().isoformat(timespec="seconds")}
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    partial = workdir / "partial-matrix.json"
    if partial.exists():  # 跑批完整结束，逐格落盘的中间态不再需要
        partial.unlink()
    md_path = REPORTS_DIR / f"{ts}-{args.env}-format-matrix.md"
    md_path.write_text(render_md(data), encoding="utf-8")
    print(f"== 报告：{json_path.name} / {md_path.name} ==")
    print(json.dumps(data["summary"], ensure_ascii=False))
    return 0 if data["summary"]["cells_error"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
