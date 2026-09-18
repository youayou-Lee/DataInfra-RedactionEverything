"""T2 桶数据构建入口：raw/ + 生成器 -> buckets/*.jsonl + manifest.private.json。

仓库零数据红线：所有输出写私有目录（本地 test-data/benchmarks/t2/ 或云
/root/private_data/benchmarks/t2/），不入库。确定性：公开集采样与合成桶均 seed=42。

用法：
  PYTHONPATH=$PWD .venv-eval/bin/python eval/benchmarks/t2/build_buckets.py \
    --raw-dir <公开集 raw 目录> [--out-dir test-data/benchmarks/t2]

raw 目录约定（缺失时打印下载指引后以非零码退出，不静默跳过）：
  cluener/train.json        CLUENER train（CLUENER2020 官方 zip 解包；dev.json 可选自动合并）
  resume/train.char.bmes    Resume NER（LatticeLSTM ResumeNER，BMES 自动转 jsonl；
                            resume.train/.dev jsonl 或 dev.char.bmes 亦接受，自动合并）
  leven/…                   当前无需（映射为空，桶 BLOCKED）

Ref #93（Task 7）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapters  # noqa: E402
import gen_failure_buckets  # noqa: E402
import spec  # noqa: E402

SEED = 42
REPO = Path(__file__).resolve().parents[3]
DEFAULT_OUT_DIR = REPO / "test-data" / "benchmarks" / "t2"

# 每个 raw 源可接受的文件（按顺序取第一个存在的；resume 合并全部存在的）
RAW_FILES: dict[str, list[str]] = {
    "cluener": ["cluener/train.json", "cluener/dev.json"],
    "resume": ["resume/resume.train", "resume/resume.dev",
               "resume/train.char.bmes", "resume/dev.char.bmes"],
    "leven": ["leven/train.jsonl", "leven/dev.jsonl"],
}

# 数据源版本/许可/URL（人工核对记录，写入 manifest 供溯源）
SOURCES_META: dict[str, dict] = {
    "cluener": {
        "version": "CLUENER 1.0（CLUEbenchmark/CLUENER 仓库 master，2026-09 拉取）",
        "license": "CLUENER 按 CLUE 组织声明限研究用途（详见仓库 README）",
        "url": "https://github.com/CLUEbenchmark/CLUENER2020",
        "download": "https://storage.googleapis.com/cluebenchmark/tasks/cluener_public.zip "
                    "解包取 train.json -> raw/cluener/train.json（dev.json 可选）",
    },
    "resume": {
        "version": "Resume NER（jiesutd/LatticeLSTM 仓库 ResumeNER，char-level BMES，2026-09 拉取）",
        "license": "随 LatticeLSTM 仓库发布，限研究用途",
        "url": "https://github.com/jiesutd/LatticeLSTM",
        "download": "https://raw.githubusercontent.com/jiesutd/LatticeLSTM/master/ResumeNER/"
                    "train.char.bmes -> raw/resume/train.char.bmes（BMES 自动转 jsonl；"
                    "dev.char.bmes 可选，自动合并）",
    },
    "leven": {
        "version": "LEVEN（thunlp/LEVEN release，infer-train-generic.jsonl 等）",
        "license": "CC BY-NC 4.0（LEVEN 仓库声明）",
        "url": "https://github.com/thunlp/LEVEN",
        "download": "https://github.com/thunlp/LEVEN/releases -> raw/leven/（当前 BLOCKED 不需要）",
    },
}

# 桶名 -> 目标 preset 类型集合（用于公开集桶的候选聚焦；None=不过滤）
BUCKET_TARGET_TYPES: dict[str, set[str] | None] = {
    "cluener-person": {"姓名"},
    "cluener-address": {"地址"},
    "cluener-organization": {"机构名称", "公司名称", "机关单位"},
    "leven-judicial-person": None,
    "leven-judicial-org": None,
    "resume-person": {"姓名"},
    "resume-native-place": {"籍贯"},
}


# Resume BMES 实际标签 -> spec.TYPE_MAPS["resume"] 键；未列（CONT/EDU/PRO/TITLE）原样保留，
# 由适配器按"未映射类型丢弃"计数。
RESUME_TAG_MAP = {"NAME": "NAME", "LOC": "LOCATION", "ORG": "ORGANIZATION", "RACE": "RACE"}

_BMES_LINE = re.compile(r"^\S+\s+[BMES]O?(-\S+)?$|^\S+\s+O$")


def resume_bmes_to_jsonl(bmes_text: str) -> list[str]:
    """char-level BMES（每行"字 标签"，空行分句）-> CLUENER 风格 jsonl 行。

    标签取实体 span 文本为键（adapters.parse_cluener_line 只按键取 span），
    值记 [[start, end]] 偏移（与 CLUENER 官方格式对齐）。
    """
    out: list[str] = []
    chars: list[str] = []
    labels: dict[str, dict[str, list[list[int]]]] = {}
    cur: tuple[str, int] | None = None  # (原始标签, start)

    def close(end: int):
        nonlocal cur
        if cur is not None:
            tag, start = cur
            span = "".join(chars[start:end])
            labels.setdefault(RESUME_TAG_MAP.get(tag, tag), {})[span] = [[start, end]]
            cur = None

    def flush():
        close(len(chars))
        if chars:
            out.append(json.dumps({"text": "".join(chars), "label": labels},
                                  ensure_ascii=False))
        chars.clear()
        labels.clear()

    for line in bmes_text.splitlines():
        if not line.strip():
            flush()
            continue
        ch, _, tag = line.rpartition(" ")
        pos = len(chars)
        chars.append(ch)
        if tag == "O":
            close(pos)
        elif tag.startswith("B-"):
            close(pos)
            cur = (tag[2:], pos)
        elif tag.startswith("E-"):
            close(pos + 1)
        elif tag.startswith("S-"):
            close(pos)
            cur = (tag[2:], pos)
            close(pos + 1)
        # M-：实体内部，不动作
    flush()
    return out


def _is_bmes(text: str) -> bool:
    first = next((l for l in text.splitlines() if l.strip()), "")
    return _BMES_LINE.match(first) is not None


def _concat_raw(raw_dir: Path, source: str) -> tuple[Path | None, list[Path]]:
    """返回 (适配器可读的 jsonl 路径 or None, 实际用到的文件列表)。

    - 多文件（resume train+dev / cluener train+dev）合并；
    - BMES 格式的 Resume 语料自动转 jsonl（写隐藏临时文件，同 raw 目录）。
    """
    files = [raw_dir / rel for rel in RAW_FILES[source] if (raw_dir / rel).is_file()]
    if not files:
        return None, []
    if len(files) == 1 and not _is_bmes(files[0].read_text(encoding="utf-8")):
        return files[0], files
    merged = raw_dir / f".{source}.merged.tmp"
    parts: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        parts.extend(resume_bmes_to_jsonl(text) if _is_bmes(text)
                     else [l for l in text.splitlines() if l.strip()])
    merged.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return merged, files


def build_public_bucket(bucket: str, source: str, raw_path: Path, size: int,
                        meta: dict) -> tuple[list[dict], dict]:
    """调 adapters.adapt_dataset 构建单个公开集桶。

    两段式（均确定性）：先 size=0 探测候选总数，再取全量候选（已按 seed 洗牌），
    按 BUCKET_TARGET_TYPES 聚焦过滤后截取 size 条；不足时用其余候选补齐。
    """
    _probe, probe_stats = adapters.adapt_dataset(source, raw_path, bucket, 0, SEED, meta)
    all_entries, stats = adapters.adapt_dataset(
        source, raw_path, bucket, probe_stats["total_candidates"], SEED, meta)
    targets = BUCKET_TARGET_TYPES.get(bucket)
    if targets is None:
        picked = all_entries[:size]
    else:
        hit = [e for e in all_entries if targets & set(e["entities"])]
        rest = [e for e in all_entries if not (targets & set(e["entities"]))]
        picked = (hit + rest)[:size]
        stats["target_matched"] = len(hit)
    return picked, stats


def build_all(raw_dir: Path, out_dir: Path) -> dict:
    buckets_dir = out_dir / "buckets"
    buckets_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"generated_at": datetime.now().isoformat(timespec="seconds"),
                      "seed": SEED, "raw_dir": str(raw_dir), "sources": {}, "buckets": {},
                      "notes": []}

    # 保留 ingest_hardcase 维护的难例索引（entries），不清空
    old = out_dir / "manifest.private.json"
    if old.is_file():
        try:
            entries = json.loads(old.read_text(encoding="utf-8")).get("entries")
            if entries:
                manifest["entries"] = entries
        except (json.JSONDecodeError, OSError) as e:
            manifest["notes"].append(f"旧 manifest 读取失败（entries 未保留）：{e}")

    used_raw: dict[str, list[str]] = {}
    for bucket, meta in spec.BUCKETS.items():
        kind = meta["kind"]
        path = buckets_dir / f"{bucket}.jsonl"
        if kind == "hardcase":
            manifest["buckets"][bucket] = {"skipped": True, "reason": "hardcase 桶由 ingest_hardcase.py 维护"}
            manifest["notes"].append(f"{bucket}: 跳过（ingest 管理，不覆盖已有文件）")
            continue
        if kind == "synthetic":
            entries = gen_failure_buckets.build_bucket(bucket, meta["size"])
            stats = {"count": len(entries), "kind": "synthetic"}
        else:  # public
            source = meta["source"]
            raw_path, files = _concat_raw(raw_dir, source)
            for f in files:  # 同源多桶去重（cluener 3 桶共用 train.json）
                p = str(f)
                if p not in used_raw.setdefault(source, []):
                    used_raw[source].append(p)
            if source == "leven" and not spec.TYPE_MAPS["leven"]:
                entries = []
                stats = {"count": 0, "blocked": True,
                         "reason": "LEVEN 仅有事件触发词标注（无实体/论元），TYPE_MAPS['leven'] 为空，"
                                   "候选 0；需换数据源或人工映射（见 task-2-report.md）"}
                print(f"[BLOCKED] {bucket}: {stats['reason']}", file=sys.stderr)
            elif raw_path is None:
                m = SOURCES_META.get(source, {})
                raise FileNotFoundError(
                    f"缺少 {source} raw 数据（期望 {RAW_FILES[source]} 之一存在于 {raw_dir}）。\n"
                    f"下载指引：{m.get('download', '见仓库 README')}（{m.get('url', '')}）")
            else:
                entries, stats = build_public_bucket(bucket, source, raw_path, meta["size"], meta)
                stats["count"] = len(entries)
                stats["sampled"] = len(entries)  # sampled=实际落桶条数（adapters 返回的原值=全量候选数）
                if not entries:
                    print(f"[WARN] {bucket}: 采样后 0 条（候选 {stats.get('total_candidates', 0)}）",
                          file=sys.stderr)
        path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries),
                        encoding="utf-8")
        manifest["buckets"][bucket] = stats

    for source, files in sorted(used_raw.items()):
        manifest["sources"][source] = {**SOURCES_META[source], "files": files}
    (out_dir / "manifest.private.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T2 桶构建：raw + 生成器 -> buckets/*.jsonl + manifest")
    ap.add_argument("--raw-dir", required=True, type=Path, help="公开集 raw 目录")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                    help=f"桶输出目录（默认 {DEFAULT_OUT_DIR}）")
    args = ap.parse_args(argv)
    try:
        manifest = build_all(args.raw_dir, args.out_dir)
    except FileNotFoundError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2
    for name, st in manifest["buckets"].items():
        if st.get("skipped"):
            print(f"{name}: skipped（ingest 管理）")
        else:
            print(f"{name}: count={st.get('count', '?')}{' BLOCKED' if st.get('blocked') else ''}")
    print(f"manifest -> {args.out_dir / 'manifest.private.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
