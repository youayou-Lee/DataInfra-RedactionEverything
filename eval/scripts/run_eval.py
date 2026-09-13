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

MANIFEST_PATH = _REPO_ROOT / "eval" / "datasets" / "manifest.json"
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
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    files = [f for f in manifest["files"] if "e2e" in f["levels"]]
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
    file_path = _REPO_ROOT / "eval" / "datasets" / spec["path"]
    gt_path = _REPO_ROOT / "eval" / "datasets" / spec["gt"]
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    gt_pages = gt["pages"]
    doc_level = spec["carrier"] in DOC_LEVEL_CARRIERS

    file_id = api.upload(file_path)
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
            m = metrics["overall"]
            print(f"  {spec['id']}: P={m['precision']:.3f} R={m['recall']:.3f} "
                  f"F1={m['f1']:.3f} gate={'✅' if metrics['digital_gate']['pass'] else '❌'} "
                  f"wall={metrics['perf']['wall_s']['total']}s")
    finally:
        api.close()
    if not per_file:
        raise SystemExit("全部文件失败，无结果可报告")

    all_records = []
    for fm in per_file:
        for rec in fm["records"]:
            all_records.append({"page_id": f"{fm['file']['id']}#{rec['page_id']}", "gt": rec["gt"],
                                "pred": rec["pred"], "latency_sec": rec["latency_sec"]})
    # 总体 P/R/宽松口径：页级 records 聚合（nerq 权威口径：每页每类型去重后累加）；
    # 数字保真：逐文件桶求和（文件内去重、跨文件累加）——与 per_file 对账一致（评审 I-B：
    # 全局去重会折叠跨文件复用的同串，总体与分文件加总对不上，且与 nerq 分母口径分叉）。
    overall = e2e_core_metrics(all_records, {}, {})
    overall["digital"] = merge_file_digital(per_file)
    gate_ok, gate_failures = nerq.digital_gate_pass(overall)
    overall["digital_gate"] = {"pass": gate_ok, "failures": gate_failures}
    return {"per_file": per_file, "failed": failed, "overall": overall}


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
    """e2e 层与上一版报告对比（I5）：总体 P/R/F1 + 数字 exact 聚合率。"""
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    b_overall = baseline.get("overall") or {}
    if not b_overall.get("overall"):
        raise SystemExit(f"--baseline 不是有效的 e2e 报告: {args.baseline}")
    c_overall = metrics["overall"]

    def digital_rate(overall: dict) -> float | None:
        total = sum(s["total_gt"] for s in overall.get("digital", {}).values())
        exact = sum(s["exact"] for s in overall.get("digital", {}).values())
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
            "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="一键评测（Issue #37）")
    parser.add_argument("--level", choices=["ner", "e2e"], required=True)
    parser.add_argument("--suite", choices=["synthetic", "pseudonymized", "all"], default="synthetic")
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
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--max-tokens", type=int, default=2048)
    args = parser.parse_args()

    if args.level == "ner":
        metrics = asyncio.run(run_ner_level(args))
    else:
        metrics = run_e2e_level(args)
        if args.baseline:
            metrics["baseline_comparison"] = build_e2e_baseline_comparison(metrics, args)
    metrics["env"] = {"env_label": args.env_label, "target_label": args.target_label,
                      "level": args.level, "suite": args.suite, "git": git_rev(),
                      "generated_at": datetime.now().isoformat(timespec="seconds"),
                      "with_perf": bool(args.with_perf)}

    date_tag = datetime.now().strftime("%Y%m%d")
    stem = f"{date_tag}-{args.env_label}-{args.target_label}-{args.level}"
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    slim = json.loads(json.dumps(metrics, ensure_ascii=False, default=str))
    if args.level == "e2e":
        for fm in slim.get("per_file", []):
            for internal in ("_gt_raw", "_pred_raw", "records"):
                fm.pop(internal, None)
            if not args.with_perf:
                fm["perf"].pop("pages_detail", None)
        slim["overall"].pop("records", None)
    (out_dir / f"{stem}.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2,
                                                     default=str), encoding="utf-8")
    if args.level == "ner":
        md = nerq.render_markdown(metrics) + "\n## 环境\n\n" + "\n".join(env_header(args)) + "\n"
    else:
        md = render_e2e_markdown(metrics, args)
    (out_dir / f"{stem}.md").write_text(md, encoding="utf-8")
    print(f"OK -> {out_dir}/{stem}.{{json,md}}")

    if args.level == "e2e":  # 一票否决闸门控制退出码（评审 I-C：e2e 键在 overall 下）
        gate = metrics["overall"]["digital_gate"]["pass"]
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


def render_e2e_markdown(result: dict, args: argparse.Namespace) -> str:
    overall = result["overall"]
    lines = [f"# 端到端评测（Issue #37）：{args.target_label}", ""] + env_header(args) + [
        "",
        "## 汇总（全部文件 micro 聚合）",
        "",
        f"- P={overall['overall']['precision']:.4f} R={overall['overall']['recall']:.4f} "
        f"F1={overall['overall']['f1']:.4f}（tp {overall['overall']['tp']} / fp {overall['overall']['fp']} "
        f"/ fn {overall['overall']['fn']}）",
        f"- 数字保真闸门：{'✅ 通过' if overall['digital_gate']['pass'] else '❌ FAIL'}",
        f"- 宽松口径（span 对、类型错）：{overall['loose']['wrong_type']} 条"
        + ("" if not overall['loose']['type_confusion_top'] else
           "，混淆 Top：" + "、".join(f"{k}×{v}" for k, v in overall['loose']['type_confusion_top'].items())),
        "",
        "| 类型 | P | R | F1 | tp | fp | fn |", "|---|---|---|---|---|---|---|",
    ]
    for etype, s in overall["per_type"].items():
        lines.append(f"| {etype} | {s['precision']:.4f} | {s['recall']:.4f} | {s['f1']:.4f} "
                     f"| {s['tp']} | {s['fp']} | {s['fn']} |")
    lines += ["", "## 数字实体逐字保真（一票否决区）", "",
              "| 类型 | exact | near_miss | miss | exact_rate |", "|---|---|---|---|---|"]
    for etype in nerq.DIGITAL_GATE_TYPES + nerq.REFERENCE_STRICT_TYPES:
        s = overall["digital"].get(etype)
        if s:
            lines.append(f"| {etype} | {s['exact']} | {s['near_miss']} | {s['miss']} | {s['exact_rate']:.4f} |")
            for v, w in s.get("near_miss_detail") or []:
                lines.append(f"  - near_miss: GT={v!r} PRED={w!r}")
            for v in s.get("miss_detail") or []:
                lines.append(f"  - miss: {v!r}")
    lines += ["", "## 分文件", "",
              "| 文件 | 载体 | GT 实体 | P | R | F1 | 数字闸门 | 总耗时 s | 页/分钟 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for fm in result["per_file"]:
        f, m = fm["file"], fm["overall"]
        tput = fm["perf"]["throughput_pages_per_min"]
        lines.append(f"| {f['id']} | {f['carrier']} | {f['gt_entities']} | {m['precision']:.4f} "
                     f"| {m['recall']:.4f} | {m['f1']:.4f} | {'✅' if fm['digital_gate']['pass'] else '❌'} "
                     f"| {fm['perf']['wall_s']['total']} | {tput if tput is not None else 'n/a'} |")
    if result.get("failed"):
        lines += ["", "## 失败文件（隔离记录）", ""] + [
            f"- {x['id']}: {x['error']}" for x in result["failed"]]
    lines += ["", "## 速度分解（steady 页，duration_ms 埋点）", "",
              "| 文件 | 段 | mean ms | p95 ms |", "|---|---|---|---|"]
    for fm in result["per_file"]:
        for key, s in fm["perf"]["duration_ms"].items():
            lines.append(f"| {fm['file']['id']} | {key} | {s['mean']} | {s['p95']} |")
    comparison = result.get("baseline_comparison")
    if comparison:
        lines += ["", f"## 与基线对比（{comparison['baseline_label']}）", ""]
        if comparison["env_mismatch"]:
            lines.append(f"⚠️ 环境标签不同（{comparison['baseline_env']} vs {args.env_label}），"
                         "跨环境只看相对值。")
        lines += ["| 指标 | 基线 | 本次 | Δ |", "|---|---|---|---|"]
        for key, row in comparison["rows"].items():
            lines.append(f"| {key} | {row['baseline']:.4f} | {row['current']:.4f} | "
                         f"{row['current'] - row['baseline']:+.4f} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
