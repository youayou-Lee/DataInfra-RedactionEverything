"""一键评测（Issue #37）：效果（P/R/F1 + 数字保真 + 宽松口径）与速度（分阶段耗时/吞吐）。

三层口径（README「指标口径」为权威说明）：
  - P/R/F1：实体串集合精确匹配；ner 层原串域，e2e 层去空白域（squash）；
  - 数字保真（一票否决）：身份证/护照/电话/银行卡在原串域逐字符分级（exact/near_miss/miss）；
  - 宽松口径：span 匹配但类型错（wrong_type）与类型混淆 Top——区分「类型分错」与「真漏检」。

两层：
  --level ner  NER 引擎层：合成语料直连 OpenAI 兼容端点（LLM NER 对比实验入口，
               指标与闸门全部复用 backend/scripts/eval/eval_ner_quality.py，口径唯一）；
  --level e2e  端到端层：manifest 驱动，走 backend 公开 API（pdf 逐页 vision；
               docx/txt 走 parse+hybrid NER，D7）。

用法示例见 eval/README.md。报告：<date>-<env-label>-<target-label>-<level>.{json,md}
环境标签必录（跨环境不比绝对值）。--baseline 对两层均可用（与上一版报告对比）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "backend" / "scripts" / "eval"))

import common_api  # noqa: E402
import eval_ner_quality as nerq  # noqa: E402
import indicator_meta  # noqa: E402

MANIFEST_PATH = _REPO_ROOT / "eval" / "datasets" / "manifest.json"
PRIVATE_MANIFEST_PATH = _REPO_ROOT / "eval" / "datasets" / "manifest.private.json"
NER_CORPUS_ID = "ner_corpus_10p"
DOC_LEVEL_CARRIERS = {"docx", "txt"}  # GT 单页聚合，与 vision 分页不可对齐 → 文档级


def git_rev() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, cwd=_REPO_ROOT, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * p / 100
    lower = math.floor(k)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (k - lower)


def load_manifest(suite: str, only: list[str] | None = None) -> list[dict]:
    """公开 manifest + 私有 manifest（真实案卷，存在时合并；铁律：私有数据/清单不入 GitHub）。"""
    files: list[dict] = []
    for path in (MANIFEST_PATH, PRIVATE_MANIFEST_PATH):
        if not path.exists():
            continue
        manifest = json.loads(path.read_text(encoding="utf-8"))
        files += [f for f in manifest["files"] if "e2e" in f["levels"]]
    if suite != "all":
        files = [f for f in files if f["source"] == suite]
    if only:
        files = [f for f in files if f["id"] in only]
        missing = set(only) - {f["id"] for f in files}
        if missing:
            raise SystemExit(f"--only 引用了不存在的条目: {sorted(missing)}")
    return files


# ---------------- NER 引擎层 ----------------

async def run_ner_level(args: argparse.Namespace) -> dict:
    corpus_path = _REPO_ROOT / "eval" / "datasets" / "synthetic" / f"{NER_CORPUS_ID}.jsonl"
    pages = [json.loads(line) for line in corpus_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    all_types = sorted({t for page in pages for t in page["entities"]})
    print(f"[ner] 语料 {len(pages)} 页，类型 {all_types}")

    import httpx
    records, warnings_all = [], []
    async with httpx.AsyncClient() as client:
        for page in pages:
            started = time.perf_counter()
            if args.grouping == "semantic":
                groups = nerq.group_types(all_types)
                results = await asyncio.gather(*[
                    nerq.call_ner(client, args.ner_base, args.model, page["text"], group,
                                  args.max_tokens, args.timeout)
                    for group in groups])
                merged: dict = {}
                for parsed, _, warns in results:
                    for etype, values in parsed.items():
                        if isinstance(values, list):
                            merged.setdefault(etype, []).extend(str(v) for v in values)
                    warnings_all += [f"page {page['page_id']}: {w}" for w in warns]
                pred = merged
            else:
                parsed, _, warns = await nerq.call_ner(client, args.ner_base, args.model,
                                                       page["text"], all_types,
                                                       args.max_tokens, args.timeout)
                pred = {t: [str(v) for v in vs] for t, vs in parsed.items() if isinstance(vs, list)}
                warnings_all += [f"page {page['page_id']}: {w}" for w in warns]
            latency = time.perf_counter() - started
            records.append({"page_id": page["page_id"], "gt": page["entities"], "pred": pred,
                            "latency_sec": latency})
            print(f"  page {page['page_id']}: {latency:.1f}s 预测类型 {len(pred)} 个")

    metrics = nerq.compute_metrics(records)
    metrics["records"] = records
    metrics["warnings"] = warnings_all
    metrics["config"] = {"ner_base": args.ner_base, "model": args.model, "grouping": args.grouping,
                         "corpus": str(corpus_path), "label": args.target_label}
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        if "per_file" in baseline:  # 层间误用（e2e 报告喂 ner 层）给明确报错而非裸栈
            raise SystemExit("--baseline 是 e2e 报告，ner 层需要 ner 层报告 JSON")
        metrics["baseline_label"] = baseline.get("config", {}).get("label", args.baseline)
        metrics["comparison"] = nerq.compare_with_baseline(metrics, baseline)
    return metrics


# ---------------- 端到端层 ----------------

def _squash_entities(entities: dict[str, list[str]]) -> dict[str, list[str]]:
    return {etype: [common_api.squash(v) for v in values] for etype, values in entities.items()}


def e2e_digital(gt_raw: dict[str, list[str]], pred_raw: dict[str, list[str]]) -> dict:
    """数字保真：原串域逐字符分级（exact/near_miss/miss），复用 nerq 口径。"""
    summary = {}
    for etype in nerq.DIGITAL_GATE_TYPES + nerq.REFERENCE_STRICT_TYPES:
        if etype not in gt_raw:
            continue
        graded = nerq.grade_digital(gt_raw.get(etype, []), pred_raw.get(etype, []))
        total = len(graded["exact"]) + len(graded["near_miss"]) + len(graded["miss"])
        summary[etype] = {"total_gt": total,
                          "exact_rate": len(graded["exact"]) / total if total else 1.0,
                          "exact": len(graded["exact"]), "near_miss": len(graded["near_miss"]),
                          "miss": len(graded["miss"]),
                          "near_miss_detail": graded["near_miss"][:20],
                          "miss_detail": graded["miss"][:20]}
    return summary


def loose_span_metrics(records: list[dict]) -> dict:
    """宽松口径（I4）：span 匹配（squash 域）但类型错——区分「类型分错」与「真漏检/误检」。"""
    gt_sq: dict[str, set[str]] = {}
    pred_sq: dict[str, set[str]] = {}
    for rec in records:
        for etype, values in rec["gt"].items():
            gt_sq.setdefault(etype, set()).update(values)
        for etype, values in rec["pred"].items():
            pred_sq.setdefault(etype, set()).update(values)
    all_gt_values = {v for vs in gt_sq.values() for v in vs}
    wrong_type = 0
    confusion: dict[str, int] = {}
    for ptype, values in pred_sq.items():
        for value in values:
            if value in all_gt_values and value not in gt_sq.get(ptype, set()):
                wrong_type += 1
                gtype = next(t for t, vs in gt_sq.items() if value in vs)
                confusion[f"{gtype}→{ptype}"] = confusion.get(f"{gtype}→{ptype}", 0) + 1
    return {"wrong_type": wrong_type,
            "type_confusion_top": dict(sorted(confusion.items(), key=lambda kv: -kv[1])[:10])}


def e2e_core_metrics(records: list[dict], gt_raw: dict[str, list[str]],
                     pred_raw: dict[str, list[str]]) -> dict:
    """双域组装的唯一入口（I6）：squash 域算 P/R，原串域覆盖 digital 与闸门，附宽松口径。

    文件级与总体聚合共用本函数——数字分级必须始终在原串域，squash 域的 compute_metrics
    结果中 digital 一律被覆盖，不得直接使用。
    """
    metrics = nerq.compute_metrics(records)
    metrics["digital"] = e2e_digital({k: sorted(set(v)) for k, v in gt_raw.items()},
                                     {k: sorted(set(v)) for k, v in pred_raw.items()})
    gate_ok, gate_failures = nerq.digital_gate_pass(metrics)
    metrics["digital_gate"] = {"pass": gate_ok, "failures": gate_failures}
    metrics["loose"] = loose_span_metrics(records)
    return metrics


def run_e2e_file(api: common_api.EvalApi, spec: dict, args: argparse.Namespace) -> dict:
    file_path = _REPO_ROOT / "eval" / "datasets" / spec["path"] if not Path(spec["path"]).is_absolute() \
        else Path(spec["path"])
    gt_path = _REPO_ROOT / "eval" / "datasets" / spec["gt"] if spec.get("gt") else None
    gt = json.loads(gt_path.read_text(encoding="utf-8")) if gt_path else None
    gt_pages = gt["pages"] if gt else [{"page": i, "entities": {}} for i in range(
        max(spec.get("pages", 1), 1))]
    perf_only = gt is None  # 真实私有子集 v1：无 GT → 只测速度/稳健性，不评效果（不虚构指标）

    # 加密边界样本：明确报错拒绝=通过（挂死/静默零框=失败）
    if spec["carrier"] == "encrypted_pdf":
        import httpx as _httpx
        started = time.perf_counter()
        try:
            file_id = api.upload(file_path)
        except _httpx.HTTPStatusError as exc:
            return {"file": {"id": spec["id"], "carrier": spec["carrier"],
                             "doc_type": spec["doc_type"], "density": spec["density"], "gt_entities": 0},
                    "robustness": {"outcome": "rejected",
                                   "detail": f"HTTP {exc.response.status_code}（{exc.response.text[:80]}）",
                                   "wall_s": round(time.perf_counter() - started, 3)},
                    "perf": None}
        try:  # 上传成功（不该发生）→ 视为稳健性 FAIL 样本照测
            pages = common_api.iter_pages_with_timing(api, file_id, max(len(gt_pages), 1))
        finally:
            api.delete_file(file_id)
        return {"file": {"id": spec["id"], "carrier": spec["carrier"], "doc_type": spec["doc_type"],
                         "density": spec["density"], "gt_entities": 0},
                "robustness": {"outcome": "accepted_should_reject", "detail": "加密卷被正常受理（未拒绝）",
                               "wall_s": round(time.perf_counter() - started, 3)},
                "perf": {"pages_total": len(pages), "warmup_pages": 0, "steady_pages": len(pages),
                         "wall_s": {"total": round(sum(p["wall_s"] for p in pages), 3)},
                         "throughput_pages_per_min": None, "duration_ms": {}, "pages_detail": pages}}

    file_id = api.upload(file_path)
    doc_level = spec["carrier"] in DOC_LEVEL_CARRIERS
    try:
        if doc_level:  # docx/txt：vision 不支持，走 parse + hybrid NER（设计文档 D7）
            pred_entities, wall = api.parse_and_hybrid_ner(file_id)
            pages = [{"page": 1, "warmup": False, "wall_s": wall, "duration_ms": {},
                      "pipeline_status": {}, "entities": pred_entities}]
        else:
            pages = common_api.iter_pages_with_timing(api, file_id, len(gt_pages),
                                                      warmup_pages=args.warmup_pages)
    finally:
        api.delete_file(file_id)

    steady = [p for p in pages if not p["warmup"]]
    walls = [p["wall_s"] for p in steady]
    empty_pages = sum(1 for p in steady if not any(p["entities"].values()))

    if perf_only:
        return {"file": {"id": spec["id"], "carrier": spec["carrier"], "doc_type": spec["doc_type"],
                         "density": spec["density"], "gt_entities": 0},
                "perf": {"pages_total": len(pages), "warmup_pages": args.warmup_pages,
                         "steady_pages": len(steady),
                         "wall_s": {"total": round(sum(p["wall_s"] for p in pages), 3),
                                    "mean": round(sum(walls) / len(walls), 3) if walls else 0,
                                    "p50": round(percentile(walls, 50), 3),
                                    "p95": round(percentile(walls, 95), 3)},
                         "throughput_pages_per_min": round(len(steady) * 60 / sum(walls), 3) if walls else None,
                         "duration_ms": {}, "pages_detail": pages},
                "robustness": {"outcome": "ok", "detail": f"{len(steady)} steady 页，空框页 {empty_pages}",
                               "wall_s": round(sum(p["wall_s"] for p in pages), 3),
                               "empty_pages": empty_pages}}

    records, gt_raw_all, pred_raw_all = [], {}, {}
    for i, gt_page in enumerate(gt_pages):
        pred_page = pages[i]["entities"] if i < len(pages) else {}
        for etype, values in gt_page["entities"].items():
            gt_raw_all.setdefault(etype, []).extend(values)
        for etype, values in pred_page.items():
            pred_raw_all.setdefault(etype, []).extend(values)
        if doc_level:
            continue
        records.append({"page_id": i, "gt": _squash_entities(gt_page["entities"]),
                        "pred": _squash_entities(pred_page),
                        "latency_sec": pages[i]["wall_s"]})
    if doc_level:
        records.append({"page_id": 0, "gt": _squash_entities(gt_raw_all),
                        "pred": _squash_entities(pred_raw_all),
                        "latency_sec": sum(p["wall_s"] for p in pages)})

    metrics = e2e_core_metrics(records, gt_raw_all, pred_raw_all)
    metrics["records"] = records
    metrics["_gt_raw"] = gt_raw_all   # 原串域（供总体聚合；落盘前剔除，不进报告）
    metrics["_pred_raw"] = pred_raw_all

    steady = [p for p in pages if not p["warmup"]]
    walls = [p["wall_s"] for p in steady]
    duration_agg: dict[str, dict] = {}
    for key in sorted({k for p in steady for k in p["duration_ms"]}):
        vals = [p["duration_ms"][key] for p in steady
                if isinstance(p["duration_ms"].get(key), (int, float))]
        if vals:
            duration_agg[key] = {"mean": round(sum(vals) / len(vals), 1),
                                 "p95": round(percentile(vals, 95), 1)}
    metrics["perf"] = {
        "pages_total": len(pages), "warmup_pages": args.warmup_pages,
        "steady_pages": len(steady),
        "wall_s": {"total": round(sum(p["wall_s"] for p in pages), 3),
                   "mean": round(sum(walls) / len(walls), 3) if walls else 0,
                   "p50": round(percentile(walls, 50), 3), "p95": round(percentile(walls, 95), 3)},
        # steady 为空（文件页数 ≤ warmup）时吞吐无意义，输出 null（渲染 n/a，评审 M3）
        "throughput_pages_per_min": round(len(steady) * 60 / sum(walls), 3) if walls else None,
        "duration_ms": duration_agg,
        "pages_detail": pages,
    }
    metrics["file"] = {"id": spec["id"], "carrier": spec["carrier"], "doc_type": spec["doc_type"],
                       "density": spec["density"], "gt_entities": sum(
                           len(vs) for p in gt_pages for vs in p["entities"].values())}

    # 逐页错误明细（诊断报告「错误明细」节；数字在原串域分级，实体在去空白域判漏检）
    error_rows: list[dict] = []
    for i, gt_page in enumerate(gt_pages):
        pred_page = pages[i]["entities"] if i < len(pages) else {}
        for etype in nerq.DIGITAL_GATE_TYPES:
            gt_vals = sorted(set(gt_page["entities"].get(etype, [])))
            if not gt_vals:
                continue
            graded = nerq.grade_digital(gt_vals, pred_page.get(etype, []))
            for v in graded["miss"]:
                error_rows.append({"file": spec["id"], "page": i + 1, "type": etype,
                                   "kind": "数字丢失", "expect": v, "actual": "—", "stage": "OCR/NER"})
            for v, w in graded["near_miss"]:
                error_rows.append({"file": spec["id"], "page": i + 1, "type": etype,
                                   "kind": "数字字符噪声", "expect": v, "actual": w, "stage": "OCR"})
        for etype, vals in gt_page["entities"].items():
            pred_sq = {common_api.squash(x) for x in pred_page.get(etype, [])}
            for v in sorted(set(vals)):
                if common_api.squash(v) not in pred_sq:
                    error_rows.append({"file": spec["id"], "page": i + 1, "type": etype,
                                       "kind": "漏检", "expect": v, "actual": "—", "stage": "NER/OCR"})
    metrics["errors"] = error_rows
    return metrics


def run_e2e_level(args: argparse.Namespace) -> dict:
    files = load_manifest(args.suite, [x.strip() for x in args.only.split(",")] if args.only else None)
    if not files:
        raise SystemExit(f"manifest 中无 --suite {args.suite} 的 e2e 条目")
    print(f"[e2e] suite={args.suite}，{len(files)} 个文件")
    api = common_api.EvalApi(args.api_base, args.api_user, args.api_pass, timeout=args.timeout)
    per_file, failed = [], []
    try:
        for spec in files:  # 单文件失败隔离（M5）：记录错误继续，不整批作废
            started = time.perf_counter()
            try:
                metrics = run_e2e_file(api, spec, args)
            except Exception as exc:  # noqa: BLE001 — 隔离记录后继续
                failed.append({"id": spec["id"], "error": f"{type(exc).__name__}: {exc}"})
                print(f"  {spec['id']}: ❌ {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            metrics["file"]["eval_wall_s"] = round(time.perf_counter() - started, 3)
            per_file.append(metrics)
            if "overall" in metrics:
                m = metrics["overall"]
                print(f"  {spec['id']}: P={m['precision']:.3f} R={m['recall']:.3f} "
                      f"F1={m['f1']:.3f} gate={'✅' if metrics['digital_gate']['pass'] else '❌'} "
                      f"wall={metrics['perf']['wall_s']['total']}s")
            else:  # 速度/稳健性条目（真实私有子集 gt=null）
                r = metrics.get("robustness") or {}
                print(f"  {spec['id']}: [{r.get('outcome', '?')}] {r.get('detail', '')[:60]}")
    finally:
        api.close()
    if not per_file:
        raise SystemExit("全部文件失败，无结果可报告")

    quality_files = [fm for fm in per_file if "overall" in fm]
    overall: dict = {}
    if quality_files:
        all_records = []
        for fm in quality_files:
            for rec in fm["records"]:
                all_records.append({"page_id": f"{fm['file']['id']}#{rec['page_id']}", "gt": rec["gt"],
                                    "pred": rec["pred"], "latency_sec": rec["latency_sec"]})
        # 总体 P/R/宽松口径：页级 records 聚合（nerq 权威口径：每页每类型去重后累加）；
        # 数字保真：逐文件桶求和（文件内去重、跨文件累加）——与 per_file 对账一致（评审 I-B：
        # 全局去重会折叠跨文件复用的同串，总体与分文件加总对不上，且与 nerq 分母口径分叉）。
        overall = e2e_core_metrics(all_records, {}, {})
        overall["digital"] = merge_file_digital(quality_files)
        gate_ok, gate_failures = nerq.digital_gate_pass(overall)
        overall["digital_gate"] = {"pass": gate_ok, "failures": gate_failures}
    result = {"per_file": per_file, "failed": failed, "overall": overall,
              "rejected": [fm["file"]["id"] for fm in per_file
                           if fm.get("robustness", {}).get("outcome") == "rejected"]}
    return result


def merge_file_digital(per_file: list[dict]) -> dict:
    """总体数字保真 = 逐文件桶求和；明细（元组格式）全局去重后截断（评审 I-A/I-B）。"""
    merged: dict[str, dict] = {}
    for fm in per_file:
        for etype, s in fm["digital"].items():
            bucket = merged.setdefault(
                etype, {"total_gt": 0, "exact": 0, "near_miss": 0, "miss": 0,
                        "near_miss_detail": [], "miss_detail": []})
            for key in ("total_gt", "exact", "near_miss", "miss"):
                bucket[key] += s[key]
            bucket["near_miss_detail"] += s["near_miss_detail"]
            bucket["miss_detail"] += s["miss_detail"]
    for s in merged.values():
        s["exact_rate"] = s["exact"] / s["total_gt"] if s["total_gt"] else 1.0
        s["near_miss_detail"] = list(dict.fromkeys(s["near_miss_detail"]))[:20]  # 元组可哈希
        s["miss_detail"] = list(dict.fromkeys(s["miss_detail"]))[:20]
    return merged


def build_e2e_baseline_comparison(metrics: dict, args: argparse.Namespace) -> dict:
    """e2e 层与上一版报告对比：总体 P/R/F1、数字聚合率、分类型 F1、速度基线（System Card 对比列）。"""
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    b_overall = baseline.get("overall") or {}
    if not b_overall.get("overall"):
        raise SystemExit(f"--baseline 不是有效的 e2e 报告: {args.baseline}")
    c_overall = metrics["overall"]

    def digital_rate(overall: dict) -> float | None:
        total = sum(s.get("total_gt", 0) for s in overall.get("digital", {}).values())
        exact = sum(s.get("exact", 0) for s in overall.get("digital", {}).values())
        return exact / total if total else None

    rows = {}
    for label, key in (("P", "precision"), ("R", "recall"), ("F1", "f1")):
        rows[label] = {"baseline": b_overall["overall"][key], "current": c_overall["overall"][key]}
    b_rate, c_rate = digital_rate(b_overall), digital_rate(c_overall)
    if b_rate is not None and c_rate is not None:
        rows["数字exact率"] = {"baseline": b_rate, "current": c_rate}
    return {"baseline_label": (baseline.get("env") or {}).get("target_label", args.baseline),
            "baseline_env": (baseline.get("env") or {}).get("env_label", "?"),
            "env_mismatch": (baseline.get("env") or {}).get("env_label") != args.env_label,
            "rows": rows,
            "per_type_f1": {t: v.get("f1") for t, v in (b_overall.get("per_type") or {}).items()},
            "perf_baseline": _perf_agg_safe([fm for fm in baseline.get("per_file", []) if fm.get("perf")])}


def main() -> int:
    parser = argparse.ArgumentParser(description="一键评测（Issue #37）")
    parser.add_argument("--level", choices=["ner", "e2e"], required=True)
    parser.add_argument("--suite", choices=["synthetic", "pseudonymized", "real", "all"],
                        default="synthetic", help="real=真实案卷私有子集（manifest.private.json）")
    parser.add_argument("--only", default=None,
                        help="e2e：只跑指定 id（逗号分隔，冒烟/调试用）")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--api-user", default="eval_user")
    parser.add_argument("--api-pass", default="EvalUser!2026")
    parser.add_argument("--ner-base", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model", default=None)
    parser.add_argument("--grouping", choices=["off", "semantic"], default="off")
    parser.add_argument("--with-perf", action="store_true",
                        help="e2e：JSON 报告保留逐页明细 pages_detail（默认裁剪瘦身）")
    parser.add_argument("--warmup-pages", type=int, default=1, help="e2e：前 N 页计为 warmup，不计入 steady")
    parser.add_argument("--target-label", required=True)
    parser.add_argument("--env-label", required=True, help="环境标签（必录，报告命名用）")
    parser.add_argument("--baseline", default=None, help="上一版报告 JSON（ner/e2e 均可对比）")
    parser.add_argument("--out", default=str(_REPO_ROOT / "eval" / "reports"))
    parser.add_argument("--from-json", default=None,
                        help="不跑评测，从已存 JSON 报告按当前报告版式重渲染 md（--level 仍需指定）")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--max-tokens", type=int, default=2048)
    args = parser.parse_args()

    if args.from_json:
        metrics = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        metrics["env"]["generated_at"] = metrics["env"].get("generated_at") or datetime.now().isoformat(timespec="seconds")
    elif args.level == "ner":
        metrics = asyncio.run(run_ner_level(args))
    else:
        metrics = run_e2e_level(args)
        if args.baseline and metrics.get("overall"):
            metrics["baseline_comparison"] = build_e2e_baseline_comparison(metrics, args)
    if not args.from_json:  # 重渲染保留原报告 env（同名回写，git/时间不失真）
        metrics["env"] = {"env_label": args.env_label, "target_label": args.target_label,
                          "level": args.level, "suite": args.suite, "git": git_rev(),
                          "generated_at": datetime.now().isoformat(timespec="seconds"),
                          "with_perf": bool(args.with_perf)}

    date_tag = str(metrics["env"].get("generated_at", datetime.now().isoformat(timespec="seconds")))[:10].replace("-", "")
    stem = f"{date_tag}-{args.env_label}-{args.target_label}-{args.level}"
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    slim = json.loads(json.dumps(metrics, ensure_ascii=False, default=str))
    if args.level == "e2e":
        for fm in slim.get("per_file", []):
            for internal in ("_gt_raw", "_pred_raw", "records"):
                fm.pop(internal, None)
            if not args.with_perf and fm.get("perf"):
                fm["perf"].pop("pages_detail", None)
        if slim.get("overall"):
            slim["overall"].pop("records", None)
    (out_dir / f"{stem}.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2,
                                                     default=str), encoding="utf-8")
    if args.level == "ner":
        md = render_ner_report(metrics, args)
    else:
        md = render_e2e_report(metrics, metrics["env"])
    (out_dir / f"{stem}.md").write_text(md, encoding="utf-8")
    print(f"OK -> {out_dir}/{stem}.{{json,md}}")

    if args.level == "e2e":
        if metrics.get("overall"):  # 效果+速度混合运行：数字闸门仍控制退出码
            gate = metrics["overall"]["digital_gate"]["pass"]
        else:  # 纯真实子集（速度/稳健性，无 GT）：有失败文件才算失败
            gate = not metrics.get("failed")
    elif metrics.get("comparison"):  # ner 层带基线：三闸门判定
        gate = metrics["comparison"]["gate_pass"]
    else:  # ner 层无基线：至少单测数字闸门
        gate = nerq.digital_gate_pass(metrics)[0]
    if gate is False:
        print("❌ 数字保真闸门不通过（见报告）", file=sys.stderr)
        return 1
    return 0


def env_header(args: argparse.Namespace) -> list[str]:
    return [
        f"- 环境标签：**{args.env_label}**（跨环境不比绝对值）",
        f"- 目标：{args.target_label}（{getattr(args, 'api_base', None) or getattr(args, 'ner_base', None)}）",
        f"- 时间：{datetime.now().isoformat(timespec='seconds')}，git：{git_rev()}",
    ]


def _perf_agg(per_file: list[dict]) -> dict:
    """跨文件速度聚合：全稳态页墙钟池 + 空框页计数（供管理者摘要）。"""
    walls, empty = [], 0
    for fm in per_file:
        for p in fm["perf"].get("pages_detail") or []:
            if p.get("warmup"):
                continue
            walls.append(p["wall_s"])
            if not any(p.get("entities") or {}):
                empty += 1
    total_steady = sum(fm["perf"].get("steady_pages", 0) or 0 for fm in per_file)
    return {"pages": total_steady,
            "p50": percentile(walls, 50), "p95": percentile(walls, 95),
            "throughput": (total_steady * 60 / sum(walls)) if walls else 0,
            "empty_pages": empty}


def render_ner_report(metrics: dict, args: argparse.Namespace) -> str:
    """NER 引擎层报告（Obsidian 结构）：一句话结论 → 重点 → 全部明细折叠。"""
    env = {"env_label": args.env_label, "target_label": args.target_label,
           "level": "ner", "git": git_rev(), "generated_at": datetime.now().isoformat(timespec="seconds")}
    o = metrics["overall"]
    lines = _frontmatter(env)
    lines += ["", f"# NER 引擎层评测：{args.target_label}", ""]
    abstract = [f"漏检：{indicator_meta._plain_miss(o['recall'])}；误检 {(1 - o['precision']) * 100:.0f}%。",
                f"数字保真：{indicator_meta._plain_digital(metrics.get('digital', {}))[0]}。",
                f"单次识别约 {metrics.get('latency_sec', {}).get('mean', 0):.1f} 秒。"]
    lines += _callout("abstract", "一句话结论", abstract)
    lines.append("")  # callout 间空行
    findings = indicator_meta.build_ner_findings(metrics) if hasattr(indicator_meta, "build_ner_findings") else []
    for f in findings:
        lines += _callout(f["type"], f["title"], f["lines"])
        lines.append("")
    detail = nerq.render_markdown(metrics).splitlines()
    lines += ["", "> [!example]- 全部明细（分类型 / 数字保真逐条 / 告警）"]
    lines += [f"> {line}" if line else ">" for line in detail]
    dict_rows = [[n, d] for n, d in indicator_meta.INDICATOR_DICTIONARY]
    lines += [""] + _table_in_callout("question", "指标字典", ["指标", "定义与用途"], dict_rows)
    md = "\n".join(lines) + "\n"
    validate_callout_separation(md)
    return md


def validate_callout_separation(md: str) -> None:
    """Obsidian 两个 callout 之间必须有空行，否则合并成一个块（渲染回归守卫）。"""
    prev_quote = False
    for i, line in enumerate(md.splitlines()):
        is_quote = line.startswith(">")
        if is_quote and prev_quote is False and line.startswith("> [!") and i > 0:
            pass  # 新 callout 的首行（前面是空行或普通行）= 正常
        if is_quote and prev_quote and line.startswith("> [!"):
            raise AssertionError(f"第 {i + 1} 行 callout 与上一个引用块粘连（缺空行）: {line[:60]}")
        prev_quote = is_quote


def _fm_escape(value) -> str:
    return str(value).replace('"', "'")


def _callout(ctype: str, title: str, lines: list[str], collapsed: bool = False) -> list[str]:
    """Obsidian callout；collapsed=True 时默认折叠（标题尾加 -）。"""
    marker = "-" if collapsed else ""
    out = [f"> [!{ctype}]{marker} {title}"]
    out += [f"> {line}" for line in lines]
    return out


def _table_in_callout(ctype: str, title: str, header: list[str], rows: list[list[str]]) -> list[str]:
    """表格放进可折叠 callout（明细默认收起，点开才展开）。"""
    lines = [f"> [!{ctype}]- {title}", ">"]
    lines.append("> | " + " | ".join(header) + " |")
    lines.append("> |" + "---|" * len(header))
    for row in rows:
        lines.append("> | " + " | ".join(str(c) for c in row) + " |")
    return lines


def _perf_agg_safe(per_file: list[dict]) -> dict:
    """跨文件速度聚合；pages_detail 被瘦身掉时退化为按文件 p50/p95 加权近似（重渲染旧 JSON 用）。"""
    walls, empty = [], 0
    total_steady = 0
    p50_w = p95_w = 0.0
    for fm in per_file:
        pf = fm.get("perf") or {}
        steady = pf.get("steady_pages") or 0
        total_steady += steady
        detail = pf.get("pages_detail")
        if detail:
            for pg in detail:
                if pg.get("warmup"):
                    continue
                walls.append(pg["wall_s"])
                if not any(pg.get("entities") or {}):
                    empty += 1
        elif steady:
            p50_w += (pf.get("wall_s", {}).get("p50") or 0) * steady
            p95_w += (pf.get("wall_s", {}).get("p95") or 0) * steady
    throughput = (total_steady * 60 / sum(walls)) if walls else 0
    if not walls and total_steady:
        # 逐页明细被瘦身时，用 Σ稳态页/Σ文件总耗时 近似吞吐（重渲染旧 JSON 场景）
        total_wall = sum((fm.get("perf") or {}).get("wall_s", {}).get("total") or 0
                         for fm in per_file if fm.get("perf"))
        throughput = total_steady * 60 / total_wall if total_wall else 0
    if walls:
        p50, p95 = percentile(walls, 50), percentile(walls, 95)
    elif total_steady:
        p50, p95 = p50_w / total_steady, p95_w / total_steady
    else:
        p50 = p95 = 0.0
    return {"pages": total_steady, "p50": p50, "p95": p95,
            "throughput": throughput, "empty_pages": empty}


def _frontmatter(env: dict) -> list[str]:
    kind = "NER 引擎层评测" if env.get("level") == "ner" else "端到端评测"
    return ["---",
            f"title: {kind}：{_fm_escape(env.get('target_label', '?'))}",
            f"date: {str(env.get('generated_at', ''))[:10]}",
            "tags:",
            "  - 评测报告",
            "  - issue-37",
            f"  - {env.get('level', 'e2e')}",
            f"env: {_fm_escape(env.get('env_label', '?'))}",
            f"target: {_fm_escape(env.get('target_label', '?'))}",
            f"git: {_fm_escape(env.get('git', 'unknown'))}",
            "---"]


def render_e2e_markdown(result: dict, args: argparse.Namespace) -> str:
    env = {"env_label": args.env_label, "target_label": args.target_label,
           "level": "e2e", "git": git_rev(), "generated_at": datetime.now().isoformat(timespec="seconds")}
    return render_e2e_report(result, env)


def render_e2e_report(result: dict, env: dict) -> str:
    """诊断报告（Issue #37 v4）：结论先行 + SLO 预算隐喻 + System Card 对比列。

    骨架：一句话诊断 → 健康度速览（四信号+速度预算）→ 效果 → 速度 → 稳健性
    → 跨版本对比 → 错误明细（折叠）→ 结论与行动。
    """
    overall = result.get("overall") or {}
    per_file = result.get("per_file") or []
    perf_agg = _perf_agg_safe([fm for fm in per_file if fm.get("perf")])
    rejected = [fm["file"]["id"] for fm in per_file
                if fm.get("robustness", {}).get("outcome") == "rejected"]
    anomalies = [fm["file"]["id"] for fm in per_file
                 if fm.get("robustness", {}).get("outcome") not in ("ok", "rejected", None)]
    failed = result.get("failed") or []
    cmp = result.get("baseline_comparison")

    lines = _frontmatter(env)
    if cmp:
        lines.insert(-1, f"baseline_run_id: {_fm_escape(cmp['baseline_label'])}")
    lines += ["", f"# 文档脱敏系统评测报告 — {env.get('target_label', '?')}", ""]

    # ── 一句话诊断（结论先行：状态+哪个指标+严重度+决策）──
    verdict = indicator_meta.build_verdict(overall, perf_agg, anomalies, len(failed))
    diag = [verdict["verdict"]]
    b = indicator_meta.digital_budget(overall.get("digital", {})) if overall else None
    if b:
        diag.append(f"数字保真预算消耗 =={b['consumed_pct']}%==（{b['exact']}/{b['total']} 全对，"
                    f"其中 near_miss {b['near']}、真错 {b['miss']}）"
                    + (f" ｜ 基线对比：{cmp['rows']['数字exact率']['current'] - cmp['rows']['数字exact率']['baseline']:+.1%}"
                       if cmp and "数字exact率" in cmp["rows"] else ""))
    if overall and overall.get("overall"):
        diag.append(f"实体 F1 {overall['overall']['f1']:.3f}"
                    + (f"（基线 {cmp['rows']['F1']['baseline']:.3f}，{cmp['rows']['F1']['current'] - cmp['rows']['F1']['baseline']:+.3f}）"
                       if cmp and "F1" in cmp["rows"] else "（无基线对比）"))
        diag.append(f"实体召回：{indicator_meta._plain_miss(overall['overall'].get('recall'))}")
    if perf_agg and perf_agg.get("p50"):
        diag.append(f"延迟 p95 {perf_agg['p95']:.1f}s（承诺线 15s）｜吞吐 {perf_agg.get('throughput', 0):.1f} 页/分钟"
                    + (f"（基线 p95 {cmp['perf_baseline'].get('p95', 0):.1f}s）" if cmp else ""))
    lines += _callout("abstract", "一句话诊断", diag)
    lines.append("")

    # ── 0. 健康度速览（四信号 + 速度预算）──
    lines += ["## 0. 健康度速览（给管理者）", ""]
    if b:
        lines += _callout("danger" if b["wrong"] else "success",
                          f"红线指标：数字保真率 —— 预算消耗 {b['consumed_pct']}%（目标 0%）",
                          ["身份证/电话/银行卡/护照 ==逐字符全对== 是发布红线：错一位 = 该脱的没脱干净。",
                           "任一错误都计入预算消耗；==消耗必须为 0%== 才可发布。"])
        lines.append("")
    def _delta(cur, base, fmt="{:+.3f}", good_when_up=True):
        if base is None:
            return "—"
        d = cur - base
        mark = "📈" if (d > 0) == good_when_up and d != 0 else ("📉" if d != 0 else "➖")
        return f"{base:.3f}（{fmt.format(d)} {mark}）" if abs(d) > 1e-9 else f"{base:.3f}（持平）"
    f1_cur = overall.get("overall", {}).get("f1") if overall else None
    f1_base = cmp["rows"]["F1"]["baseline"] if cmp and "F1" in cmp["rows"] else None
    sig_rows = []
    if b:
        sig_rows.append(["数字保真率", f"{b['exact'] / b['total'] * 100:.1f}%", "100%", "🔴" if b["wrong"] else "🟢",
                         _delta(b["exact"] / b["total"], cmp["rows"].get("数字exact率", {}).get("baseline"), "{:+.1%}") if cmp else "—"])
    if f1_cur is not None:
        sig_rows.append(["实体 F1", f"{f1_cur:.3f}", "≥ 0.95（参考）",
                         "🟢" if f1_cur >= 0.95 else "🟡", _delta(f1_cur, f1_base)])
    if perf_agg and perf_agg.get("p50"):
        sig_rows.append(["延迟 p95", f"{perf_agg['p95']:.1f}s", "≤ 15s（承诺线）",
                         "🟢" if perf_agg["p95"] <= 15 else "🟡",
                         _delta(perf_agg["p95"], cmp["perf_baseline"].get("p95") if cmp else None, "{:+.1f}s", good_when_up=False)])
        sig_rows.append(["吞吐", f"{perf_agg.get('throughput', 0):.1f} 页/分钟", "越高越好", "—",
                         _delta(perf_agg.get("throughput", 0), cmp["perf_baseline"].get("throughput") if cmp else None, "{:+.1f}")])
    if sig_rows:
        lines += _table_in_callout("example", "四信号（当前 / 目标 / 状态 / 基线对比）",
                                   ["信号", "当前", "目标", "状态", "基线对比"], sig_rows)
        lines.append("")
    duration_files = [fm for fm in per_file if fm.get("perf") and fm["perf"].get("duration_ms")]
    stage_tot: dict[str, float] = {}
    stages: list = []
    if duration_files:
        for fm in duration_files:
            for k, v in fm["perf"]["duration_ms"].items():
                if k in ("total", "request_total_ms") or not v.get("mean"):
                    continue
                stage_tot[k] = stage_tot.get(k, 0) + v.get("mean", 0)
        grand = sum(stage_tot.values()) or 1
        stages = sorted(stage_tot.items(), key=lambda kv: -kv[1])
        budget_lines = [f"- **最大瓶颈：{stages[0][0]}**（占已埋点时间 {stages[0][1] / grand * 100:.0f}%）"
                        if stages else "- 各阶段无埋点数据"]
        for k, v in stages[1:3]:
            budget_lines.append(f"- {k}：占 {v / grand * 100:.0f}%")
        lines += _callout("tip", "速度预算（阶段耗时占比）", budget_lines)
        lines.append("")

    # ── 1. 效果评测（有 GT 时）──
    if overall and overall.get("overall"):
        lines += ["## 1. 效果评测", ""]
        per_type_rows = []
        for t, v in overall["per_type"].items():
            base_f1 = (cmp or {}).get("per_type_f1", {}).get(t)
            per_type_rows.append([t, f"{v['precision']:.4f}", f"{v['recall']:.4f}", f"{v['f1']:.4f}",
                                  _delta(v["f1"], base_f1)])
        lines += _table_in_callout("example", "分类型 P/R/F1（含基线对比）",
                                   ["类型", "P", "R", "F1", "基线 F1（Δ）"], per_type_rows)
        lines.append("")
        lines += _callout("note", "指标翻译（不需要背定义）",
                          ["**精确率 P**：系统标记为敏感的内容里，多少是真的（「别把无关内容也脱了」）",
                           "**召回率 R**：真正的敏感内容里，系统抓到了多少（「别漏了真正的敏感信息」）",
                           "**near_miss**：数字只差空格/连字符，OCR 噪声，可自动修复；**miss**：真错真丢，要人查"])
        lines.append("")
        digital_rows = []
        for etype in nerq.DIGITAL_GATE_TYPES + nerq.REFERENCE_STRICT_TYPES:
            s_ = overall["digital"].get(etype)
            if s_:
                ref = etype in nerq.REFERENCE_STRICT_TYPES
                digital_rows.append([etype + ("（参考，不入红线）" if ref else ""),
                                     f"{s_['exact_rate'] * 100:.1f}%",
                                     "越接近 100% 越好" if ref else "100%",
                                     ("🟢" if s_["exact_rate"] >= 0.8 else "🟡") if ref
                                     else ("🔴" if s_["exact_rate"] < 1 else "🟢")])
        if digital_rows:
            lines += _table_in_callout("example", "数字保真率分类型（红线区）",
                                       ["类型", "逐字符正确率", "目标", "状态"], digital_rows)
            lines.append("")

    # ── 2. 速度评测 ──
    if duration_files or (perf_agg and perf_agg.get("pages")):
        lines += ["## 2. 速度评测", ""]
        if perf_agg and perf_agg.get("p50"):
            sp_rows = [["单页耗时 p50", f"{perf_agg['p50']:.1f}s",
                        f"{cmp['perf_baseline'].get('p50', 0):.1f}s" if cmp else "—",
                        _delta(perf_agg["p50"], cmp["perf_baseline"].get("p50") if cmp else None, "{:+.1f}s", False)],
                       ["单页耗时 p95", f"{perf_agg['p95']:.1f}s",
                        f"{cmp['perf_baseline'].get('p95', 0):.1f}s" if cmp else "—",
                        _delta(perf_agg["p95"], cmp["perf_baseline"].get("p95") if cmp else None, "{:+.1f}s", False)],
                       ["吞吐", f"{perf_agg.get('throughput', 0):.1f} 页/分钟",
                        f"{cmp['perf_baseline'].get('throughput', 0):.1f}" if cmp else "—",
                        _delta(perf_agg.get("throughput", 0), cmp["perf_baseline"].get("throughput") if cmp else None, "{:+.1f}")]]
            lines += _table_in_callout("example", "整体延迟与吞吐", ["指标", "当前", "基线", "Δ"], sp_rows)
            lines.append("")
        if stage_tot:
            agg_rows = []
            for k, total_mean in stages:
                vals = [fm["perf"]["duration_ms"][k] for fm in duration_files
                        if k in fm["perf"]["duration_ms"]]
                agg_rows.append([k, f"{total_mean / len(vals):.0f}",
                                 f"{total_mean / grand * 100:.0f}%",
                                 f"{max(v.get('p95', 0) for v in vals):.0f}"])
            lines += _table_in_callout("example", "阶段耗时分解（跨文件聚合，每页时间花在哪）",
                                       ["阶段", "mean ms", "占比", "最差 p95 ms"], agg_rows)
            lines.append("")

    # ── 3. 稳健性（真实案卷 / 边界样本）──
    robust_files = [fm for fm in per_file if fm.get("robustness")]
    if robust_files:
        lines += ["## 3. 稳健性（真实案卷，无标注不评效果）", ""]
        lines += _callout("warning", "此数据集无 ground truth",
                          ["真实案卷没有逐字符标注，==不计算保真率与 P/R==；只用于速度与稳定性验证。"])
        lines.append("")
        rob_rows = []
        for fm in robust_files:
            pf, r = fm.get("perf") or {}, fm["robustness"]
            ws = pf.get("wall_s") or {}
            rob_rows.append([fm["file"]["id"], pf.get("steady_pages", "-"),
                             f"{ws.get('p50', '-')}s" if ws.get("p50") else "-",
                             r.get("empty_pages", "-"), r["outcome"]])
        lines += _table_in_callout("example", "真实子集逐文件", ["文件", "稳态页", "p50", "空框页", "结果"], rob_rows)
        lines.append("")

    # ── 4. 跨版本对比 ──
    if cmp:
        cmp_rows = [[key, f"{row['baseline']:.4f}", f"{row['current']:.4f}",
                     f"{row['current'] - row['baseline']:+.4f}"] for key, row in cmp["rows"].items()]
        lines += ["## 4. 跨版本对比", ""]
        lines += _table_in_callout(
            "info", f"vs 基线 {cmp['baseline_label']}"
            + ("（⚠️ 环境不同：%s vs %s，只看相对值）" % (cmp["baseline_env"], env.get("env_label"))
               if cmp["env_mismatch"] else ""),
            ["指标", "基线", "本次", "Δ"], cmp_rows)
        lines.append("")

    # ── 5. 错误明细（工程师下钻）──
    error_rows = [e for fm in per_file for e in fm.get("errors") or []]
    if error_rows:
        show = error_rows[:50]
        lines += ["## 5. 错误明细（工程师下钻）", ""]
        lines += _table_in_callout(
            "bug", f"错误列表 {len(error_rows)} 条（显示前 {len(show)}，全量见 JSON）"
                   f"——数字噪声 {sum(1 for e in error_rows if e['kind'] == '数字字符噪声')}"
                   f"、数字丢失 {sum(1 for e in error_rows if e['kind'] == '数字丢失')}"
                   f"、漏检 {sum(1 for e in error_rows if e['kind'] == '漏检')}",
            ["文件", "页", "类型", "错误", "期望", "实际", "疑似阶段"],
            [[e["file"], e["page"], e["type"], e["kind"], e["expect"], e["actual"], e["stage"]] for e in show])
        lines.append("")
    if failed:
        lines += _callout("failure", f"评测失败文件（{len(failed)}）",
                          [f"{x['id']}: {x['error']}" for x in failed])
        lines.append("")

    # ── 6. 结论与行动 ──
    lines += ["## 6. 结论与行动", ""]
    lines += _callout("success" if verdict["pass"] else "danger",
                      verdict["verdict"],
                      ["**下一步：**"] + [f"- [ ] {a}" for a in verdict["actions"]])
    lines.append("")

    md = "\n".join(lines) + "\n"
    validate_callout_separation(md)
    return md


if __name__ == "__main__":
    sys.exit(main())
