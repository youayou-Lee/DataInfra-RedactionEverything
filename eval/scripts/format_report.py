"""Issue #46 格式矩阵报告渲染（纯函数，快照单测）：JSON 结果 → Obsidian 风格 md。

版式（沿用 #37 报告骨架，轻量化——本矩阵只产 PASS/FAIL 与召回数值）：
  一句话诊断（含手册口径决策建议）→ 三档结论速览表 → 失败/异常明细 → 逐格式小节。
"""

from __future__ import annotations

import json
from datetime import datetime

_GATE_ORDER = ("g1", "g2", "g3", "g4")
_TIER_BADGE = {"承诺支持": "✅", "实验性": "⚠️", "前端禁用": "⛔", "无有效格子": "❓"}


def _fmt_gates(cell: dict) -> str:
    parts = []
    for gate in _GATE_ORDER:
        r = cell.get("gates", {}).get(gate)
        if not r:
            parts.append(f"{gate} —")
            continue
        status = r["status"]
        if gate == "g3" and r.get("detail", {}).get("recall") is not None:
            parts.append(f"g3 {r['detail']['recall']:.0%} {status}")
        else:
            parts.append(f"{gate} {status}")
    return " ｜ ".join(parts)


def render_md(data: dict) -> str:
    meta = data.get("meta", {})
    summary = data.get("summary", {})
    tiers = data.get("tiers", {})
    cells = data.get("cells", [])
    anomalies = data.get("anomalies", [])

    lines: list[str] = []
    lines.append("> [!tip] 相关文档")
    lines.append("> Issue：[#46](https://github.com/youayou-Lee/DataInfra-RedactionEverything/issues/46)"
                 " ｜ 设计：[[issue-46-format-matrix-testing]] ｜ 样张：`eval/datasets/formats/`")
    lines.append("")
    lines.append("# Issue #46：格式 × 处理方式实测矩阵报告")
    lines.append("")
    lines.append(f"- 环境：`{meta.get('env', '?')}`（{meta.get('base_url', '?')}）　"
                 f"suite：{meta.get('suite', '?')}　"
                 f"执行：{meta.get('started_at', '?', )} → {meta.get('finished_at', '?')}")
    lines.append(f"- 结果：{summary.get('cells_pass', 0)}/{summary.get('cells_total', 0)} 格 PASS，"
                 f"{summary.get('cells_fail', 0)} FAIL，{summary.get('cells_skip', 0)} SKIP，"
                 f"{summary.get('cells_error', 0)} ERROR；异常例 "
                 f"{summary.get('anomalies_pass', 0)}/{summary.get('anomalies_total', 0)} PASS")
    lines.append("")

    # 一句话诊断
    disable = sorted(t for t, v in tiers.items() if v == "前端禁用")
    experimental = sorted(t for t, v in tiers.items() if v == "实验性")
    passed_formats = sorted(t for t, v in tiers.items() if v == "承诺支持")
    diagnosis = f"**{len(passed_formats)} 类格式可承诺支持**（{'、'.join(passed_formats) or '无'}）"
    if experimental:
        diagnosis += f"；**{len(experimental)} 类实验性**（{'、'.join(experimental)}）"
    if disable:
        diagnosis += f"；**{len(disable)} 类建议前端禁用**（{'、'.join(disable)}）"
    lines.append(f"> [!summary] 一句话诊断")
    lines.append(f"> {diagnosis}。异常路径 {summary.get('anomalies_pass', 0)}/"
                 f"{summary.get('anomalies_total', 0)} 通过。")
    lines.append("")

    # 三档结论速览
    lines.append("## 三档结论速览")
    lines.append("")
    lines.append("| 格式 | 结论 | 打码格 | 化名格 | 依据 |")
    lines.append("|---|---|---|---|---|")
    for format_id in _ordered_formats(cells):
        tier = tiers.get(format_id, "无有效格子")
        mode_cells = {c["mode"]: c for c in cells if c["format"] == format_id}
        mask, pseudo = mode_cells.get("mask"), mode_cells.get("pseudonym")
        basis = _basis(mask or pseudo)
        lines.append(f"| {format_id} | {_TIER_BADGE.get(tier, '')} {tier} "
                     f"| {_cell_mark(mask)} | {_cell_mark(pseudo)} | {basis} |")
    lines.append("")

    # 失败明细
    failed = [c for c in cells if c["status"] == "FAIL"]
    if failed:
        lines.append("## 失败明细")
        lines.append("")
        for cell in failed:
            lines.append(f"- **{cell['cell_id']}**（{cell.get('fail_class') or 'soft'}）："
                         f"{_fail_reason(cell)}")
        lines.append("")

    errors = [c for c in cells if c["status"] == "ERROR"]
    if errors:
        lines.append("## 执行异常（ERROR，不计格式结论）")
        lines.append("")
        for cell in errors:
            lines.append(f"- **{cell['cell_id']}**：{cell.get('error', '')}")
        lines.append("")

    if anomalies:
        lines.append("## 异常路径")
        lines.append("")
        lines.append("| 用例 | 预期 | 结果 | 观察 |")
        lines.append("|---|---|---|---|")
        for a in anomalies:
            obs = "；".join(a.get("observations", [])) or (a.get("error") or "")
            mark = "✅ PASS" if a["status"] == "PASS" else ("⛔ FAIL" if a["status"] == "FAIL" else "❓ ERROR")
            lines.append(f"| {a['case_id']} | {a.get('expect', '')} | {mark} | {obs} |")
        lines.append("")

    # 逐格式小节
    lines.append("## 逐格明细")
    lines.append("")
    for format_id in _ordered_formats(cells):
        lines.append(f"### {format_id}")
        lines.append("")
        lines.append("| 格子 | G1-G4 | 状态 | 耗时 |")
        lines.append("|---|---|---|---|")
        for cell in [c for c in cells if c["format"] == format_id]:
            note = f"（{cell['note']}）" if cell.get("note") else ""
            lines.append(f"| {cell['mode']} | {_fmt_gates(cell) if cell['gates'] else '—'} "
                         f"| {cell['status']}{note} | {cell.get('wall_s', 0)}s |")
        lines.append("")

    lines.append("---")
    lines.append(f"*由 `eval/scripts/run_format_matrix.py` 生成于 "
                 f"{datetime.now().strftime('%Y-%m-%d %H:%M')}；样张全合成，任务文件已清理。*")
    return "\n".join(lines) + "\n"


def _ordered_formats(cells: list[dict]) -> list[str]:
    seen: list[str] = []
    for cell in cells:
        if cell["format"] not in seen:
            seen.append(cell["format"])
    return seen


def _cell_mark(cell: dict | None) -> str:
    if cell is None:
        return "—（产品边界）" 
    return {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️", "ERROR": "❓"}.get(cell["status"], cell["status"])


def _basis(cell: dict | None) -> str:
    if cell is None:
        return "无格子"
    if cell["status"] == "PASS":
        return "G1-G4 全过"
    if cell["status"] == "SKIP":
        return cell.get("note", "上游不可用")
    if cell["status"] == "ERROR":
        return "执行异常"
    return _fail_reason(cell)


def _fail_reason(cell: dict) -> str:
    for gate in _GATE_ORDER:
        r = cell.get("gates", {}).get(gate)
        if r and r["status"] == "FAIL":
            detail = r.get("detail", {})
            if detail.get("reason"):
                return f"{gate}：{detail['reason']}"
            if detail.get("problems"):
                return f"{gate}：{'；'.join(detail['problems'][:2])}"
            if detail.get("missing"):
                return f"{gate}：缺 {detail['missing'][:3]}"
    return cell.get("error") or "未知原因"


if __name__ == "__main__":
    import sys
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(render_md(data))
