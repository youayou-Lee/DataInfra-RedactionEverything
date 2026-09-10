"""NER 调优一键评测套件（Issue #23 起，历次调优通用）。

一次命令完成：拉起被测 NER 服务（自管端口，不碰生产）→ 按「调优矩阵」逐项评测 →
汇总延迟/加速比/质量闸门成一张表。调优后回归速查用。

用法（在 DCU 实例上，DTK 环境已 source，见 bench_ner_onekey.sh 包装）：
  python bench_ner_onekey.py \
      --model /root/redaction/DataInfra-RedactionEverything/backend/models/has/HaS_Text_0209_0.6B \
      --server-script /root/redaction/cloud-deploy/ner_transformers_server.py \
      --python /root/.venvs/nl/bin/python \
      --pages 10 --out /root/issue23-eval/run-latest \
      [--matrix base,axisA,axisB,axisAB] [--batch-window-ms 250] [--max-batch 8] \
      [--reference-base http://127.0.0.1:8081/v1 --reference-model HaS_Text_0209_0.6B]

矩阵项（每项 = 服务端配置 × 客户端拆分方式）：
  base   : --batch-window-ms 0   + 整页单请求     （现状基线，同套件内加速比分母）
  axisA  : --batch-window-ms 0   + 语义分组并发   （拆请求对质量/速度的独立影响，E4-A）
  axisB  : --batch-window-ms W   + 整页单请求     （攒批在无并发下的表现，预期≈base）
  axisAB : --batch-window-ms W   + 语义分组并发   （组合拳，主收益项）
  vllm-ref（可选）：不拉服务，直评 --reference-base（换引擎参考行）

输出：out/ 下每项 {label}.json/.md/.run.log + SUMMARY.md（总表+闸门判定）+ summary.json。
退出码：任一矩阵项质量闸门不通过（相对 base）→ 1，全部通过 → 0。
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent


def poll_health(health_url: str, timeout_s: float, log_file: Path) -> bool:
    """健康检查打根路径 /health（注意：不是 OpenAI 基座 /v1/health，那是 404）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = httpx.get(health_url, timeout=5.0)
            if r.status_code == 200 and r.json().get("ready"):
                return True
        except Exception:
            pass
        if not log_file.exists() or "Traceback" in log_file.read_text(encoding="utf-8", errors="ignore")[-4000:]:
            # 服务进程崩溃时提前退出，避免傻等满超时
            time.sleep(2)
            try:
                r = httpx.get(health_url, timeout=5.0)
                if r.status_code == 200 and r.json().get("ready"):
                    return True
            except Exception:
                return False
        time.sleep(3)
    return False


class ServerProc:
    """自管 transformers NER 服务：拉起 → 等 ready → 评测 → 销毁。"""

    def __init__(self, args, port: int, window_ms: float):
        self.args = args
        self.port = port
        self.window_ms = window_ms
        self.proc = None
        self.base = f"http://127.0.0.1:{port}/v1"
        self.health_url = f"http://127.0.0.1:{port}/health"
        self.log_file = Path(args.out) / f"server-p{port}-w{int(window_ms)}.log"

    def __enter__(self):
        cmd = [
            self.args.python, self.args.server_script,
            "--model", self.args.model,
            "--host", "127.0.0.1", "--port", str(self.port),
            "--device", self.args.device,
            "--batch-window-ms", str(self.window_ms),
            "--max-batch", str(self.args.max_batch),
        ]
        env = dict(
            PATH="/usr/local/bin:/usr/bin:/bin",
            **{k: v for k, v in {
                "CUDA_VISIBLE_DEVICES": self.args.cuda_visible_devices,
                "HF_HUB_OFFLINE": "1",
                "MIOPEN_USER_CACHE_PATH": self.args.miopen_cache,
            }.items() if v},
        )
        env.update({k: v for k, v in __import__("os").environ.items()
                    if k not in env or k in ("PATH", "LD_LIBRARY_PATH", "PYTHONPATH")})
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_handle = open(self.log_file, "ab")
        print(f"[server] 拉起 port={self.port} window={self.window_ms}ms …", flush=True)
        self.proc = subprocess.Popen(cmd, stdout=self.log_handle, stderr=subprocess.STDOUT, env=env)
        if not poll_health(self.health_url, self.args.server_boot_timeout, self.log_file):
            self.__exit__(None, None, None)
            raise RuntimeError(f"NER 服务 {self.port} 未在 {self.args.server_boot_timeout}s 内就绪，日志: {self.log_file}")
        print(f"[server] 就绪 {self.base}", flush=True)
        return self

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            print(f"[server] 已停止 port={self.port}", flush=True)
        if getattr(self, "log_handle", None):
            self.log_handle.close()


def run_eval(args, label: str, ner_base: str, grouping: str, baseline_json: Path | None,
             model: str | None) -> dict:
    """调用 eval_ner_quality.py 跑一项评测，返回其 result.json 内容。"""
    cmd = [
        args.python, str(HERE / "eval_ner_quality.py"),
        "--corpus", args.corpus, "--ner-base", ner_base,
        "--label", label, "--grouping", grouping, "--out", args.out,
    ]
    if model:
        cmd += ["--model", model]
    if baseline_json and baseline_json.exists():
        cmd += ["--baseline", str(baseline_json)]
    run_log = Path(args.out) / f"{label}.run.log"
    print(f"[eval] {label}: grouping={grouping} base={ner_base}", flush=True)
    with open(run_log, "wb") as lf:
        rc = subprocess.call(cmd, stdout=lf, stderr=subprocess.STDOUT)
    result_file = Path(args.out) / f"{label}.json"
    if not result_file.exists():
        raise RuntimeError(f"{label}: 评测失败 rc={rc}，见 {run_log}")
    return json.loads(result_file.read_text(encoding="utf-8"))


def gate_summary(metrics: dict) -> str:
    c = metrics.get("comparison")
    if not c:
        return "—（基线自身）"
    ok = "✅" if c["gate_pass"] else "❌"
    return (f"{ok} R{c['recall_delta_pp']:+.2f}pp P{c['precision_delta_pp']:+.2f}pp "
            f"数字{'100%' if c['checks']['digital_exact_100pct'] else '<100%'}")


def digital_rate(metrics: dict) -> str:
    ds = metrics.get("digital", {})
    total = exact = 0
    for t in ("身份证号", "护照号", "电话", "银行卡号"):
        s = ds.get(t)
        if s:
            total += s["total_gt"]
            exact += s["exact"]
    return f"{exact}/{total}" if total else "n/a"


def main() -> int:
    p = argparse.ArgumentParser(description="NER 调优一键评测套件")
    p.add_argument("--model", required=True, help="被测模型目录")
    p.add_argument("--server-script", required=True, help="ner_transformers_server.py 路径")
    p.add_argument("--python", default=sys.executable, help="跑服务与评测用的 python（须已装 httpx/transformers）")
    p.add_argument("--pages", type=int, default=10, help="合成语料页数（默认 10 = Issue #23 验收口径）")
    p.add_argument("--corpus", default=None, help="已有 GT 语料 JSONL（缺省自动生成到 out/ 下）")
    p.add_argument("--out", default="bench_ner_out", help="输出目录")
    p.add_argument("--port", type=int, default=18080, help="被测服务端口（须空闲）")
    p.add_argument("--matrix", default="base,axisA,axisB,axisAB",
                   help="逗号分隔：base,axisA,axisB,axisAB（base 始终隐含，作为分母）")
    p.add_argument("--batch-window-ms", type=float, default=250.0, help="axisB/axisAB 的攒批窗口")
    p.add_argument("--max-batch", type=int, default=8)
    p.add_argument("--device", default="cuda:0")
    p.add_argument("--cuda-visible-devices", default="0")
    p.add_argument("--miopen-cache", default="")
    p.add_argument("--server-boot-timeout", type=float, default=300.0)
    p.add_argument("--reference-base", default=None, help="参考引擎端点（如 vLLM http://127.0.0.1:8081/v1），不拉服务直接评")
    p.add_argument("--reference-model", default=None, help="参考端点需要的 model 字段")
    p.add_argument("--reference-label", default="vllm-ref")
    args = p.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    matrix = [m.strip() for m in args.matrix.split(",") if m.strip()]
    if "base" not in matrix:
        matrix.insert(0, "base")

    # 1) 语料（确定性生成，同页数重复运行逐字节一致，可直接复用）
    if args.corpus:
        args.corpus = str(Path(args.corpus).resolve())
    else:
        args.corpus = str(out / f"ner_gt_corpus_p{args.pages}.jsonl")
        if not Path(args.corpus).exists():
            subprocess.check_call([args.python, str(HERE / "make_ner_gt_corpus.py"),
                                   "--out", args.corpus, "--pages", str(args.pages)])
    print(f"[corpus] {args.corpus}（{args.pages} 页）", flush=True)

    results: dict[str, dict] = {}
    overall_ok = True

    # 2) base + axisA 共用窗口=0 的服务；axisB/axisAB 共用窗口=W 的服务
    need_w0 = any(m in matrix for m in ("base", "axisA"))
    need_wW = any(m in matrix for m in ("axisB", "axisAB"))
    baseline_json = out / "base.json"

    if need_w0:
        with ServerProc(args, args.port, 0.0) as srv:
            if "base" in matrix:
                results["base"] = run_eval(args, "base", srv.base, "off", None, None)
            if "axisA" in matrix:
                results["axisA"] = run_eval(args, "axisA", srv.base, "semantic", baseline_json, None)
    if need_wW:
        with ServerProc(args, args.port, args.batch_window_ms) as srv:
            if "axisB" in matrix:
                results["axisB"] = run_eval(args, "axisB", srv.base, "off", baseline_json, None)
            if "axisAB" in matrix:
                results["axisAB"] = run_eval(args, "axisAB", srv.base, "semantic", baseline_json, None)
    if args.reference_base:
        results[args.reference_label] = run_eval(
            args, args.reference_label, args.reference_base, "semantic", baseline_json, args.reference_model)

    # 3) 汇总表
    base_mean = results["base"]["latency_sec"]["mean"]
    desc = {
        "base": "基线（窗口0+整页单请求）",
        "axisA": "轴A 语义分组（窗口0）",
        "axisB": f"轴B 仅攒批（窗口{int(args.batch_window_ms)}ms）",
        "axisAB": f"轴A+B 组合（窗口{int(args.batch_window_ms)}ms）",
    }
    lines = [
        "# NER 调优一键评测汇总", "",
        f"- 语料：{args.corpus}（{args.pages} 页）",
        f"- 模型：{args.model}",
        f"- 被测服务：{args.server_script}（端口 {args.port}，自管生命周期）",
        f"- 攒批窗口：{args.batch_window_ms}ms / max-batch {args.max_batch}", "",
        "| 配置 | 单页均值(s) | 单页max(s) | 加速比 | P | R | 数字exact | 质量闸门(vs base) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for label, m in results.items():
        o = m["overall"]
        speedup = base_mean / m["latency_sec"]["mean"] if m["latency_sec"]["mean"] else 0.0
        lines.append(
            f"| {label} {desc.get(label, '')} | {m['latency_sec']['mean']:.2f} | {m['latency_sec']['max']:.2f} "
            f"| x{speedup:.2f} | {o['precision']:.4f} | {o['recall']:.4f} "
            f"| {digital_rate(m)} | {gate_summary(m)} |")
        c = m.get("comparison")
        if c and not c["gate_pass"]:
            overall_ok = False
    lines += ["", f"**总判定：{'✅ 全部通过' if overall_ok else '❌ 存在闸门不通过项'}**",
              "", "明细：各配置同名 .json/.md；服务日志 server-*.log；逐项运行日志 *.run.log。"]
    (out / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "summary.json").write_text(
        json.dumps({k: {kk: v.get(kk) for kk in ("overall", "digital", "latency_sec", "comparison", "config")}
                    for k, v in results.items()}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n".join(lines), flush=True)
    print(f"\nOK -> {out}/SUMMARY.md", flush=True)
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
