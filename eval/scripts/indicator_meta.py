"""指标元数据（Issue #37 v2）：每个指标的名字、业务含义、目标/参考线——报告「管理者摘要」
与「指标字典」的唯一数据源。改口径先改这里（README 口径表与本渲染同源）。"""

from __future__ import annotations

# 指标字典：全量指标定义（报告中「指标字典」节按此渲染）
INDICATOR_DICTIONARY: list[tuple[str, str]] = [
    ("数字保真 exact 率", "身份证/护照/电话/银行卡四类数字被逐字符正确识别并定位的比例；"
     "错一位=该脱的没脱干净=漏脱敏红线。目标恒为 100%（一票否决）"),
    ("实体召回 R", "应被识别的敏感实体中实际被找出的比例；漏检=漏脱敏。"
     "参考线=NER 引擎层基线 −1pp（LLM NER 实验的三闸门之一）"),
    ("实体精确 P", "识别出的实体中真正是敏感实体的比例；误检=把无关内容也脱掉，损害文档可读性"),
    ("F1", "P 与 R 的调和平均，效果的综合分"),
    ("宽松口径 wrong_type", "找对了文本但类型标错的条数（如把人名标成机构）；区分「类型分错」与「真漏检」"),
    ("near_miss", "数字识别结果与真值仅差空格/连字符/大小写的条数；属 OCR 层噪声，"
     "可自动修复修复后转为 exact——是定位「损失在哪一层」的关键信号"),
    ("miss", "数字识别结果与真值实质不同的条数；真损失，需要按层排查（OCR/NER/匹配）"),
    ("单页耗时 p50 / p95", "稳态下单页端到端处理时间的中位数与 95 分位；p95 决定用户「最惨等多久」"),
    ("吞吐（页/分钟）", "稳态页均耗时的倒数换算；估算「N 页卷宗要等多久」用总页数÷吞吐"),
    ("duration_ms 分解", "单页内 OCR / NER / 视觉定位(LA) / 匹配各阶段耗时埋点；性能优化的靶子定位"),
    ("失败文件 / 空框页", "识别请求报错的文件数 / 未检出任何框的页数；稳健性指标，"
     "静默失败（job 看着成功但页没处理）是最危险形态"),
    ("e2e−ner 差值", "同一实体串口径下端到端与 NER 引擎层的效果差；差值即 OCR/路由链路引入的损失"),
]


def build_e2e_summary(overall: dict, perf_agg: dict, failed_count: int,
                      ner_baseline: float | None = None) -> list[dict]:
    """管理者摘要行（e2e 层）：从指标值构造 {指标, 本次, 目标/参考, 状态, 说明}。

    perf_agg: {"pages", "p50", "p95", "throughput", "empty_pages"}（可为 None=纯效果运行）。
    """
    rows: list[dict] = []
    digital = overall.get("digital", {})
    total_gt = sum(s.get("total_gt", 0) for s in digital.values())
    exact = sum(s.get("exact", 0) for s in digital.values())
    near = sum(s.get("near_miss", 0) for s in digital.values())
    exact_rate = exact / total_gt if total_gt else None
    if exact_rate is not None:
        rows.append({
            "指标": "数字保真 exact 率", "本次": f"{exact_rate * 100:.1f}%（{exact}/{total_gt}）",
            "目标/参考": "100%（红线）", "状态": "✅" if exact_rate == 1.0 else "❌",
            "说明": "该脱敏的数字错一位都算漏脱敏，一票否决"})
        if total_gt:
            rows.append({
                "指标": "数字 near_miss / miss", "本次": f"{near} / {total_gt - exact - near}",
                "目标/参考": "miss=0、near_miss 越低越好",
                "状态": "⚠️" if (near or total_gt - exact - near) else "✅",
                "说明": "near_miss=空格型损伤（OCR 层可自动修复）；miss=真损失，需按层排查"})
    o = overall.get("overall", {})
    if o:
        r, p = o.get("recall", 0), o.get("precision", 0)
        ref = f"≥ NER 基线({ner_baseline * 100:.1f}%) −1pp" if ner_baseline else "越高越好"
        rows.append({
            "指标": "实体召回 R", "本次": f"{r * 100:.1f}%", "目标/参考": ref,
            "状态": "✅" if (ner_baseline is None or r >= ner_baseline - 0.01) else "❌",
            "说明": "漏检实体=漏脱敏，最核心效果指标"})
        rows.append({
            "指标": "实体精确 P", "本次": f"{p * 100:.1f}%", "目标/参考": "越高越好",
            "状态": "✅" if p >= 0.8 else "⚠️",
            "说明": "误检=把无关内容脱掉，文档可读性受损"})
        rows.append({
            "指标": "F1 / 宽松口径类型错", "本次": f"{o.get('f1', 0):.3f} / {overall.get('loose', {}).get('wrong_type', 0)} 条",
            "目标/参考": "—", "状态": "—", "说明": "综合分 / 找对文本但类型标错"})
    if perf_agg:
        rows.append({
            "指标": "单页耗时 p50 / p95", "本次": f"{perf_agg['p50']:.1f}s / {perf_agg['p95']:.1f}s",
            "目标/参考": "越低越好（提速承诺线 15s/页）",
            "状态": "✅" if perf_agg["p95"] <= 15 else "⚠️", "说明": "用户单页等待时间；p95=最惨等多久"})
        rows.append({
            "指标": "吞吐", "本次": f"{perf_agg['throughput']:.1f} 页/分钟",
            "目标/参考": "20 页卷宗 ≈ N 分钟", "状态": "—",
            "说明": "20 页卷宗约 " + (f"{20 / perf_agg['throughput']:.0f}" if perf_agg["throughput"] else "∞") + " 分钟"})
        rows.append({
            "指标": "失败文件 / 空框页", "本次": f"{failed_count} / {perf_agg['empty_pages']}",
            "目标/参考": "0 / 尽量低", "状态": "✅" if failed_count == 0 else "❌",
            "说明": "稳健性：请求报错与未检出任何框的页；静默失败最危险"})
    return rows


def build_real_summary(perf_agg: dict, failed_count: int, rejected: list[str],
                       anomalies: list[str] | None = None) -> list[dict]:
    """真实子集（速度/稳健性，无 GT）的管理者摘要行。anomalies=未被正确拒绝的边界样本。"""
    anomalies = anomalies or []
    rows = [{
        "指标": "单页耗时 p50 / p95", "本次": f"{perf_agg['p50']:.1f}s / {perf_agg['p95']:.1f}s",
        "目标/参考": "合成集同口径对照", "状态": "—",
        "说明": "真实扫描件（噪声/盖章/票据）的单页处理耗时，与合成集对比即「真实数据税」"},
    ]
    if perf_agg.get("throughput"):
        rows.append({
            "指标": "吞吐", "本次": f"{perf_agg['throughput']:.1f} 页/分钟", "目标/参考": "20 页卷宗 ≈ N 分钟",
            "状态": "—",
            "说明": "20 页卷宗约 " + (f"{20 / perf_agg['throughput']:.0f}" if perf_agg["throughput"] else "∞") + " 分钟"})
    rows.append({
        "指标": "失败文件 / 空框页", "本次": f"{failed_count} / {perf_agg.get('empty_pages', 0)}",
        "目标/参考": "0 / 尽量低", "状态": "✅" if failed_count == 0 else "❌",
        "说明": "稳健性：真实数据上服务是否稳定、有无整页识别不出"})
    rows.append({
        "指标": "加密卷拒识", "本次": f"{len(rejected)} 例拒绝 / {len(anomalies)} 例异常",
        "目标/参考": "边界样本应全部明确拒绝", "状态": "✅" if rejected and not anomalies else "❌",
        "说明": "加密文件应被明确报错拒绝；被正常受理（accepted_should_reject）属稳健性缺陷"})
    return rows


def build_ner_summary(metrics: dict) -> list[dict]:
    """ner 层管理者摘要行。"""
    o = metrics["overall"]
    rows = [{
        "指标": "实体召回 R", "本次": f"{o['recall'] * 100:.1f}%", "目标/参考": "LLM NER 候选需 ≥ 基线 −1pp",
        "状态": "—", "说明": "漏检实体=漏脱敏；本层是纯 NER 引擎对比，无 OCR 噪声"},
        {"指标": "实体精确 P", "本次": f"{o['precision'] * 100:.1f}%", "目标/参考": "≥ 基线",
         "状态": "—", "说明": "误检=把无关内容脱掉，文档可读性受损"},
        {"指标": "F1", "本次": f"{o['f1']:.4f}", "目标/参考": "—", "状态": "—", "说明": "效果综合分"}]
    digital = metrics.get("digital", {})
    total_gt = sum(s["total_gt"] for s in digital.values())
    exact = sum(s["exact"] for s in digital.values())
    if total_gt:
        rows.append({
            "指标": "数字保真 exact 率", "本次": f"{exact / total_gt * 100:.1f}%（{exact}/{total_gt}）",
            "目标/参考": "100%（红线）", "状态": "✅" if exact == total_gt else "❌",
            "说明": "数字错一位=漏脱敏，一票否决；ner 层不过=模型本身的问题"})
    lat = metrics.get("latency_sec", {})
    if lat:
        rows.append({
            "指标": "单次 NER 墙钟 mean / max", "本次": f"{lat.get('mean', 0):.1f}s / {lat.get('max', 0):.1f}s",
            "目标/参考": "LLM NER 目标 <5s（拆请求+batch 后）", "状态": "—",
            "说明": "引擎层推理速度（不含 OCR/编排）"})
    return rows


SUMMARY_HEADER = ["指标", "本次", "目标/参考", "状态", "说明"]


def render_summary_md(rows: list[dict]) -> str:
    lines = ["| " + " | ".join(SUMMARY_HEADER) + " |",
             "|" + "---|" * len(SUMMARY_HEADER)]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(k, "")) for k in SUMMARY_HEADER) + " |")
    return "\n".join(lines)


def render_dictionary_md() -> str:
    lines = ["| 指标 | 定义与用途 |", "|---|---|"]
    for name, definition in INDICATOR_DICTIONARY:
        lines.append(f"| {name} | {definition} |")
    return "\n".join(lines)
