"""一键评测（Issue #37）：效果（P/R/F1 + 数字保真）与速度（分阶段耗时/吞吐）。

两层：
  --level ner  NER 引擎层：合成语料直连 OpenAI 兼容端点（LLM NER 对比实验入口，
               指标与闸门全部复用 backend/scripts/eval/eval_ner_quality.py，口径唯一）；
  --level e2e  端到端层：manifest 驱动，走 backend 公开 API（login→upload→逐页 vision），
               实体串对齐（P/R 在去空白域；数字保真在原串域逐字符分级——设计文档 D2/D3）。

用法示例见 eval/README.md。报告：<date>-<env-label>-<target-label>-<level>.{json,md}
环境标签必录（跨环境不比绝对值）。
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
        if doc_level:  # 文档级聚合为单 record
            for etype, values in gt_page["entities"].items():
                gt_raw_all.setdefault(etype, []).extend(values)
            for etype, values in pred_page.items():
                pred_raw_all.setdefault(etype, []).extend(values)
            continue
        records.append({"page_id": i, "gt": _squash_entities(gt_page["entities"]),
                        "pred": _squash_entities(pred_page),
                        "latency_sec": pages[i]["wall_s"]})
        for etype, values in gt_page["entities"].items():
            gt_raw_all.setdefault(etype, []).extend(values)
        for etype, values in pred_page.items():
            pred_raw_all.setdefault(etype, []).extend(values)
    if doc_level:
        records.append({"page_id": 0, "gt": _squash_entities(gt_raw_all),
                        "pred": _squash_entities(pred_raw_all),
                        "latency_sec": sum(p["wall_s"] for p in pages)})

    steady = [p for p in pages if not p["warmup"]]
    walls = [p["wall_s"] for p in steady]
    duration_agg: dict[str, dict] = {}
    for key in sorted({k for p in steady for k in p["duration_ms"]}):
        vals = [p["duration_ms"][key] for p in steady
                if isinstance(p["duration_ms"].get(key), (int, float))]
        if vals:
            duration_agg[key] = {"mean": round(sum(vals) / len(vals), 1),
                                 "p95": round(percentile(vals, 95), 1)}
    metrics = nerq.compute_metrics(records)
    metrics["records"] = records
    metrics["digital"] = e2e_digital({k: sorted(set(v)) for k, v in gt_raw_all.items()},
                                     {k: sorted(set(v)) for k, v in pred_raw_all.items()})
    gate_ok, gate_failures = nerq.digital_gate_pass(metrics)
    metrics["digital_gate"] = {"pass": gate_ok, "failures": gate_failures}

    metrics["perf"] = {
        "pages_total": len(pages), "warmup_pages": args.warmup_pages,
        "wall_s": {"total": round(sum(p["wall_s"] for p in pages), 3),
                   "mean": round(sum(walls) / len(walls), 3) if walls else 0,
                   "p50": round(percentile(walls, 50), 3), "p95": round(percentile(walls, 95), 3)},
        "throughput_pages_per_min": round(len(steady) * 60 / sum(walls), 3) if walls else 0,
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
    per_file = []
    try:
        for spec in files:
            started = time.perf_counter()
            metrics = run_e2e_file(api, spec, args)
            metrics["file"]["eval_wall_s"] = round(time.perf_counter() - started, 3)
            per_file.append(metrics)
            m = metrics["overall"]
            print(f"  {spec['id']}: P={m['precision']:.3f} R={m['recall']:.3f} "
                  f"F1={m['f1']:.3f} gate={'✅' if metrics['digital_gate']['pass'] else '❌'} "
                  f"wall={metrics['perf']['wall_s']['total']}s")
    finally:
        api.close()

    all_records = []
    for fm in per_file:
        for rec in fm["records"]:
            all_records.append({"page_id": f"{fm['file']['id']}#{rec['page_id']}", "gt": rec["gt"],
                                "pred": rec["pred"], "latency_sec": rec["latency_sec"]})
    overall = nerq.compute_metrics(all_records)
    digital_total = {}
    for fm in per_file:
        for etype, s in fm["digital"].items():
            bucket = digital_total.setdefault(
                etype, {"total_gt": 0, "exact": 0, "near_miss": 0, "miss": 0,
                        "near_miss_detail": [], "miss_detail": []})
            for key in ("total_gt", "exact", "near_miss", "miss"):
                bucket[key] += s[key]
            bucket["near_miss_detail"] += s["near_miss_detail"][:20]
            bucket["miss_detail"] += s["miss_detail"][:20]
    for s in digital_total.values():
        s["exact_rate"] = s["exact"] / s["total_gt"] if s["total_gt"] else 1.0
    overall["digital"] = digital_total
    gate_ok, gate_failures = nerq.digital_gate_pass(overall)
    overall["digital_gate"] = {"pass": gate_ok, "failures": gate_failures}
    return {"per_file": [{k: v for k, v in fm.items() if k != "records"} for fm in per_file],
            "overall": overall}


# ---------------- 报告 ----------------

def env_header(args: argparse.Namespace, level: str) -> list[str]:
    return [
        f"- 环境标签：**{args.env_label}**（跨环境不比绝对值）",
        f"- 目标：{args.target_label}（{getattr(args, 'api_base', None) or getattr(args, 'ner_base', None)}）",
        f"- 时间：{datetime.now().isoformat(timespec='seconds')}，git：{git_rev()}",
    ]


def render_e2e_markdown(result: dict, args: argparse.Namespace) -> str:
    overall = result["overall"]
    lines = [f"# 端到端评测（Issue #37）：{args.target_label}", ""] + env_header(args, "e2e") + [
        "",
        "## 汇总（全部文件 micro 聚合）",
        "",
        f"- P={overall['overall']['precision']:.4f} R={overall['overall']['recall']:.4f} "
        f"F1={overall['overall']['f1']:.4f}（tp {overall['overall']['tp']} / fp {overall['overall']['fp']} "
        f"/ fn {overall['overall']['fn']}）",
        f"- 数字保真闸门：{'✅ 通过' if overall['digital_gate']['pass'] else '❌ FAIL'}",
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
        lines.append(f"| {f['id']} | {f['carrier']} | {f['gt_entities']} | {m['precision']:.4f} "
                     f"| {m['recall']:.4f} | {m['f1']:.4f} | {'✅' if fm['digital_gate']['pass'] else '❌'} "
                     f"| {fm['perf']['wall_s']['total']} | {fm['perf']['throughput_pages_per_min']} |")
    lines += ["", "## 速度分解（steady 页，duration_ms 埋点）", "",
              "| 文件 | 段 | mean ms | p95 ms |", "|---|---|---|---|"]
    for fm in result["per_file"]:
        for key, s in fm["perf"]["duration_ms"].items():
            lines.append(f"| {fm['file']['id']} | {key} | {s['mean']} | {s['p95']} |")
    return "\n".join(lines) + "\n"


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
    parser.add_argument("--with-perf", action="store_true", help="e2e：附速度分解（默认已收集，此开关控制报告详略）")
    parser.add_argument("--warmup-pages", type=int, default=1, help="e2e：前 N 页计为 warmup，不计入 steady")
    parser.add_argument("--target-label", required=True)
    parser.add_argument("--env-label", required=True, help="环境标签（必录，报告命名用）")
    parser.add_argument("--baseline", default=None, help="ner 层：基线 result.json，输出闸门判定")
    parser.add_argument("--out", default=str(_REPO_ROOT / "eval" / "reports"))
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--max-tokens", type=int, default=2048)
    args = parser.parse_args()

    if args.level == "ner":
        metrics = asyncio.run(run_ner_level(args))
    else:
        metrics = run_e2e_level(args)
    metrics["env"] = {"env_label": args.env_label, "target_label": args.target_label,
                      "level": args.level, "suite": args.suite, "git": git_rev(),
                      "generated_at": datetime.now().isoformat(timespec="seconds"),
                      "with_perf": bool(args.with_perf)}

    date_tag = datetime.now().strftime("%Y%m%d")
    stem = f"{date_tag}-{args.env_label}-{args.target_label}-{args.level}"
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stem}.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2,
                                                     default=str), encoding="utf-8")
    if args.level == "ner":
        md = nerq.render_markdown(metrics) + "\n## 环境\n\n" + "\n".join(env_header(args, "ner")) + "\n"
    else:
        md = render_e2e_markdown(metrics, args)
    (out_dir / f"{stem}.md").write_text(md, encoding="utf-8")
    print(f"OK -> {out_dir}/{stem}.{{json,md}}")

    gate = metrics.get("digital_gate") or (metrics.get("comparison") or {}).get("gate_pass")
    if gate is False:
        print("❌ 数字保真闸门不通过（见报告）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
