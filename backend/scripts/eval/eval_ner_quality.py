"""NER 质量评测（Issue #23 质量闸门工具）：合成 GT 语料 → 直连 NER 服务 → P/R + 数字实体逐字保真率。

用法：
  # 1. 造语料（一次即可）
  python make_ner_gt_corpus.py --out ner_gt_corpus.jsonl --pages 10

  # 2. 基线（现状：整页 9 类型单请求，HaS bf16 transformers）
  python eval_ner_quality.py --corpus ner_gt_corpus.jsonl --ner-base http://127.0.0.1:8080/v1 \
      --label baseline-single --grouping off --out eval_ner_out

  # 3. 拆分组 A/B（E4-A：同模型同引擎，仅拆 3 组并发，隔离「拆分对质量的影响」）
  python eval_ner_quality.py ... --label baseline-grouped --grouping semantic

  # 4. 候选（换引擎/换模型/量化）+ 对比基线与闸门判定
  python eval_ner_quality.py ... --label qwen3b-q4 --baseline eval_ner_out/baseline-single.json

闸门（Issue #23 / 设计文档 §4.2，一票否决）：
  - 数字实体（身份证号/护照号/电话/银行卡号）逐字符精确匹配率 = 100%；
  - 实体级召回 ≥ 基线 −1pp，精确率不低于基线。
口径：每页每类型实体串去重后按集合精确匹配；数字实体分 exact / near_miss（仅空格连字符
大小写差异）/ miss 三级，闸门只认 exact。邮箱按同法分级，作为参考指标一并输出。
"""

import argparse
import asyncio
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

# 与设计文档 §2.1 语义分组一致（has_service 改造落地后两处须同步）
GROUP_G1 = ["姓名", "机构名称"]
GROUP_G2 = ["身份证号", "护照号", "电话", "银行卡号", "邮箱"]
GROUP_G3 = ["地址", "日期"]
DIGITAL_GATE_TYPES = ["身份证号", "护照号", "电话", "银行卡号"]  # 一票否决区
REFERENCE_STRICT_TYPES = ["邮箱"]  # 参考指标（账号类非纯数字）

_MODEL_TEMPERATURE = 0.0  # has_client.py:38
_MODEL_TOP_P = 0.6  # has_client.py:40


def build_ner_prompt(text: str, types: list[str], guidance: str = "") -> str:
    """逐字对齐 backend/app/services/has_client.py:494-501 的模型卡模板（改动须两处同步）。

    guidance 为空时中间留一个空行，与 has_client 的 {guidance_block} 行为一致。
    """
    types_str = json.dumps(types, ensure_ascii=False, separators=(",", ":"))
    guidance_block = f"\nType guidance:{guidance}" if guidance else ""
    return f"""Recognize the following entity types in the text.
Specified types:{types_str}
{guidance_block}
Return strict JSON only. Include only entity types that have matches in the text.
Never output empty arrays. Do not return requested types with no matches. Do not explain.
If nothing matches, return {{}}.
<text>{text}</text>"""


def parse_model_json(content: str) -> dict:
    """解析模型输出：围栏剥离 + 子串提取，与 has_client._try_parse_json_object 同序（:203）。"""
    raw = str(content or "").strip()
    if not raw:
        return {}
    fenced = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
    fenced = re.sub(r"\s*```$", "", fenced).strip()
    start, end = fenced.find("{"), fenced.rfind("}")
    candidates = [("direct", raw)]
    if fenced != raw:
        candidates.append(("fenced", fenced))
    if start >= 0 and end > start:
        candidates.append(("substring", fenced[start:end + 1]))
    for _, candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def group_types(types: list[str]) -> list[list[str]]:
    """语义分组（设计文档 §2.1）：G1 人员/组织、G2 标识号码、G3 时空；未映射类型进兜底批。"""
    remaining = list(types)
    groups = []
    for fixed in (GROUP_G1, GROUP_G2, GROUP_G3):
        hit = [t for t in remaining if t in fixed]
        if hit:
            groups.append(hit)
            remaining = [t for t in remaining if t not in fixed]
    if remaining:
        groups.append(remaining)
    return groups


def _normalize(value: str) -> str:
    return re.sub(r"[\s\-—_]", "", str(value)).upper()


def grade_digital(gt_values: list[str], pred_values: list[str]) -> dict:
    """数字实体分级：exact（逐字符一致）/ near_miss（仅空格连字符大小写差异）/ miss。"""
    pred_set = set(pred_values)
    pred_norm = {_normalize(v): v for v in pred_values}
    exact, near_miss, miss = [], [], []
    for v in sorted(set(gt_values)):
        if v in pred_set:
            exact.append(v)
        elif _normalize(v) in pred_norm:
            near_miss.append((v, pred_norm[_normalize(v)]))
        else:
            miss.append(v)
    return {"exact": exact, "near_miss": near_miss, "miss": miss}


def compute_metrics(records: list[dict]) -> dict:
    """records: [{"page_id", "gt": {类型: [实体]}, "pred": {类型: [实体]}, "latency_sec"}]。"""
    per_type: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    digital: dict[str, dict] = {}
    for record in records:
        gt, pred = record["gt"], record["pred"]
        for etype in sorted(set(gt) | set(pred)):
            gt_set = set(gt.get(etype, []))
            pred_set = set(pred.get(etype, []))
            stats = per_type[etype]
            stats["tp"] += len(gt_set & pred_set)
            stats["fp"] += len(pred_set - gt_set)
            stats["fn"] += len(gt_set - pred_set)
        for etype in DIGITAL_GATE_TYPES + REFERENCE_STRICT_TYPES:
            if etype in gt:
                bucket = digital.setdefault(etype, {"exact": [], "near_miss": [], "miss": []})
                graded = grade_digital(gt[etype], pred.get(etype, []))
                bucket["exact"] += graded["exact"]
                bucket["near_miss"] += graded["near_miss"]
                bucket["miss"] += graded["miss"]

    def prf(tp: int, fp: int, fn: int) -> dict:
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return {"precision": precision, "recall": recall, "f1": f1}

    overall_tp = sum(s["tp"] for s in per_type.values())
    overall_fp = sum(s["fp"] for s in per_type.values())
    overall_fn = sum(s["fn"] for s in per_type.values())
    latencies = [r.get("latency_sec", 0.0) for r in records]
    digital_summary = {}
    for etype, bucket in digital.items():
        total = len(bucket["exact"]) + len(bucket["near_miss"]) + len(bucket["miss"])
        digital_summary[etype] = {
            "total_gt": total,
            "exact_rate": len(bucket["exact"]) / total if total else 1.0,
            "exact": len(bucket["exact"]), "near_miss": len(bucket["near_miss"]), "miss": len(bucket["miss"]),
            "near_miss_detail": bucket["near_miss"][:20],
            "miss_detail": bucket["miss"][:20],
        }
    return {
        "pages": len(records),
        "overall": {"tp": overall_tp, "fp": overall_fp, "fn": overall_fn, **prf(overall_tp, overall_fp, overall_fn)},
        "per_type": {t: {"tp": s["tp"], "fp": s["fp"], "fn": s["fn"], **prf(s["tp"], s["fp"], s["fn"])}
                     for t, s in sorted(per_type.items())},
        "digital": digital_summary,
        "latency_sec": {
            "mean": sum(latencies) / len(latencies) if latencies else 0.0,
            "max": max(latencies, default=0.0),
            "total": sum(latencies),
        },
    }


def digital_gate_pass(metrics: dict) -> tuple[bool, list[str]]:
    """一票否决：四类数字实体 exact_rate 必须 = 100%。"""
    failures = []
    for etype in DIGITAL_GATE_TYPES:
        summary = metrics["digital"].get(etype)
        if summary is None:
            continue
        if summary["exact_rate"] < 1.0:
            failures.append(
                f"{etype}: exact {summary['exact']}/{summary['total_gt']}"
                f"（near_miss {summary['near_miss']} / miss {summary['miss']}）"
            )
    return (not failures, failures)


def compare_with_baseline(candidate: dict, baseline: dict) -> dict:
    """闸门判定：召回 ≥ 基线 −1pp、精确率 ≥ 基线、数字逐字 100%。"""
    recall_delta = (candidate["overall"]["recall"] - baseline["overall"]["recall"]) * 100.0
    precision_delta = (candidate["overall"]["precision"] - baseline["overall"]["precision"]) * 100.0
    gate_ok, gate_failures = digital_gate_pass(candidate)
    checks = {
        "recall_ge_baseline_minus_1pp": recall_delta >= -1.0,
        "precision_ge_baseline": precision_delta >= 0.0,
        "digital_exact_100pct": gate_ok,
    }
    return {
        "recall_delta_pp": recall_delta,
        "precision_delta_pp": precision_delta,
        "checks": checks,
        "gate_pass": all(checks.values()),
        "digital_gate_failures": gate_failures,
    }


async def call_ner(client: httpx.AsyncClient, base: str, model: str | None, text: str,
                   types: list[str], max_tokens: int, timeout: float) -> tuple[dict, float, list[str]]:
    """单次 NER 调用，返回 (类型→实体 dict, 耗时, 非精确解析告警)。"""
    payload = {
        "messages": [{"role": "user", "content": build_ner_prompt(text, types)}],
        "temperature": _MODEL_TEMPERATURE,
        "top_p": _MODEL_TOP_P,
        "stream": False,
        "max_tokens": max_tokens,
    }
    if model:
        payload["model"] = model
    started = time.perf_counter()
    response = await client.post(f"{base.rstrip('/')}/chat/completions", json=payload,
                                 timeout=httpx.Timeout(timeout, connect=20.0))
    latency = time.perf_counter() - started
    response.raise_for_status()
    body = response.json()
    content = body["choices"][0]["message"]["content"]
    finish_reason = body["choices"][0].get("finish_reason")
    parsed = parse_model_json(content)
    warnings = []
    if not parsed and str(content).strip():
        warnings.append(f"unparsed content: {str(content)[:120]!r}")
    if finish_reason == "length":
        warnings.append("finish_reason=length（截断）")
    return parsed, latency, warnings


async def run_eval(args: argparse.Namespace) -> dict:
    pages = [json.loads(line) for line in Path(args.corpus).read_text(encoding="utf-8").splitlines() if line.strip()]
    all_types = sorted({t for page in pages for t in page["entities"]})
    print(f"语料 {len(pages)} 页，GT 类型 {all_types}")

    records = []
    warnings_all = []
    async with httpx.AsyncClient() as client:
        for page in pages:
            started = time.perf_counter()
            if args.grouping == "semantic":
                groups = group_types(all_types)
                results = await asyncio.gather(*[
                    call_ner(client, args.ner_base, args.model, page["text"], group,
                             args.max_tokens, args.timeout)
                    for group in groups
                ])
                merged: dict = {}
                for parsed, _, warns in results:
                    for etype, values in parsed.items():
                        if isinstance(values, list):
                            merged.setdefault(etype, []).extend(str(v) for v in values)
                    warnings_all += [f"page {page['page_id']}: {w}" for w in warns]
                pred = merged
            else:
                parsed, _, warns = await call_ner(client, args.ner_base, args.model, page["text"],
                                                  all_types, args.max_tokens, args.timeout)
                pred = {t: [str(v) for v in vs] for t, vs in parsed.items() if isinstance(vs, list)}
                warnings_all += [f"page {page['page_id']}: {w}" for w in warns]
            latency = time.perf_counter() - started
            records.append({"page_id": page["page_id"], "gt": page["entities"], "pred": pred,
                            "latency_sec": latency})
            print(f"  page {page['page_id']}: {latency:.1f}s, 预测类型 {len(pred)} 个")

    metrics = compute_metrics(records)
    metrics["records"] = records  # 明细随 result.json 落盘，供对比与复查
    metrics["warnings"] = warnings_all
    metrics["config"] = {"ner_base": args.ner_base, "model": args.model, "grouping": args.grouping,
                         "corpus": args.corpus, "label": args.label}
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        metrics["baseline_label"] = baseline.get("config", {}).get("label", args.baseline)
        metrics["comparison"] = compare_with_baseline(metrics, baseline)
    return metrics


def render_markdown(metrics: dict) -> str:
    lines = [
        f"# NER 质量评测：{metrics['config']['label']}",
        "",
        f"- 语料：{metrics['config']['corpus']}（{metrics['pages']} 页）",
        f"- NER 服务：{metrics['config']['ner_base']}，模型：{metrics['config']['model'] or '(服务默认)'}，"
        f"分组：{metrics['config']['grouping']}",
        f"- 总体：P={metrics['overall']['precision']:.4f} R={metrics['overall']['recall']:.4f} "
        f"F1={metrics['overall']['f1']:.4f}（tp {metrics['overall']['tp']} / fp {metrics['overall']['fp']} "
        f"/ fn {metrics['overall']['fn']}）",
        f"- 单页 NER 墙钟：mean {metrics['latency_sec']['mean']:.1f}s / max {metrics['latency_sec']['max']:.1f}s",
        "",
        "## 分类型",
        "",
        "| 类型 | P | R | F1 | tp | fp | fn |",
        "|---|---|---|---|---|---|---|",
    ]
    for etype, stats in metrics["per_type"].items():
        lines.append(f"| {etype} | {stats['precision']:.4f} | {stats['recall']:.4f} | {stats['f1']:.4f} "
                     f"| {stats['tp']} | {stats['fp']} | {stats['fn']} |")
    lines += ["", "## 数字实体逐字保真（一票否决区）", "", "| 类型 | exact | near_miss | miss | exact_rate |", "|---|---|---|---|---|"]
    for etype in DIGITAL_GATE_TYPES + REFERENCE_STRICT_TYPES:
        summary = metrics["digital"].get(etype)
        if not summary:
            continue
        lines.append(f"| {etype} | {summary['exact']} | {summary['near_miss']} | {summary['miss']} "
                     f"| {summary['exact_rate']:.4f} |")
        for v, w in summary["near_miss_detail"]:
            lines.append(f"  - near_miss: GT={v!r} PRED={w!r}")
        for v in summary["miss_detail"]:
            lines.append(f"  - miss: {v!r}")
    comparison = metrics.get("comparison")
    if comparison:
        lines += [
            "", "## 闸门判定（vs " + str(metrics.get("baseline_label")) + "）", "",
            f"- 召回差：{comparison['recall_delta_pp']:+.2f}pp（要求 ≥ −1.00pp）→ "
            f"{'✅' if comparison['checks']['recall_ge_baseline_minus_1pp'] else '❌'}",
            f"- 精确率差：{comparison['precision_delta_pp']:+.2f}pp（要求 ≥ 0）→ "
            f"{'✅' if comparison['checks']['precision_ge_baseline'] else '❌'}",
            f"- 数字逐字 100%：{'✅' if comparison['checks']['digital_exact_100pct'] else '❌'}",
            f"- **总闸门：{'通过' if comparison['gate_pass'] else '不通过'}**",
        ]
        for failure in comparison["digital_gate_failures"]:
            lines.append(f"  - {failure}")
    if metrics["warnings"]:
        lines += ["", "## 告警", ""] + [f"- {w}" for w in metrics["warnings"][:50]]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="NER 质量评测（Issue #23 质量闸门）")
    parser.add_argument("--corpus", required=True, help="GT 语料 JSONL（make_ner_gt_corpus.py 产出）")
    parser.add_argument("--ner-base", default="http://127.0.0.1:8080/v1", help="OpenAI 兼容 NER 端点")
    parser.add_argument("--model", default=None, help="payload model 字段（服务多模型时指定）")
    parser.add_argument("--grouping", choices=["off", "semantic"], default="off")
    parser.add_argument("--label", required=True, help="本次评测标签（输出文件名）")
    parser.add_argument("--out", default="eval_ner_out", help="输出目录")
    parser.add_argument("--baseline", default=None, help="基线 result.json，对比并输出闸门判定")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--max-tokens", type=int, default=2048)
    args = parser.parse_args()

    metrics = asyncio.run(run_eval(args))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{args.label}.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / f"{args.label}.md").write_text(render_markdown(metrics), encoding="utf-8")
    print(f"OK -> {out_dir}/{args.label}.{{json,md}}")
    comparison = metrics.get("comparison")
    if comparison and not comparison["gate_pass"]:
        print("❌ 质量闸门不通过（见 md 报告）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
