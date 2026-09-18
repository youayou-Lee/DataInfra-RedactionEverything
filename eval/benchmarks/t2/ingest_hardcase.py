"""T2 难例沉淀 ingest 脚本：从真实 PDF 页面人工登记难例，自检拒收非法输入。

仓库零数据原则：脚本只在调用方显式 --out-dir 时写盘，测试 fixture 用 fitz 现造临时 PDF。
Issue #93 子任务 A（Task 5）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import fitz

from spec import ENTRY_SCHEMA_KEYS

REPO = Path(__file__).resolve().parents[3]
PRESET_JSON = REPO / "backend" / "config" / "preset_entity_types.json"
DEFAULT_OUT_DIR = REPO / "test-data" / "benchmarks" / "t2" / "hardcase"
HARDCASE_JSONL = "hardcase.jsonl"
MANIFEST_NAME = "manifest.private.json"

_id_check = re.compile(r"\d{17}[\dX]").fullmatch


def load_preset_names(preset_json: Path = PRESET_JSON) -> set[str]:
    """读取 preset 中文名集合（所有条目的 name 字段）。"""
    data = json.loads(Path(preset_json).read_text(encoding="utf-8"))
    return {v["name"] for v in data.values() if isinstance(v, dict) and v.get("name")}


def build_hardcase_entry(
    file_path: Path,
    page: int,
    spans: list[tuple[str, str]],
    story: str,
    origin: str,
    raw_forms: dict[str, str] | None = None,
) -> dict:
    """构建 hardcase 条目；类型不在 preset / 实体不在文本 / 身份证格式非法均抛 ValueError 拒收。"""
    raw_forms = raw_forms or {}
    preset_names = load_preset_names()
    with fitz.open(file_path) as doc:
        if page >= doc.page_count:
            raise ValueError(f"页码 {page} 越界（共 {doc.page_count} 页）")
        text = doc[page].get_text()

    entities: dict[str, list[str]] = {}
    for typ, val in spans:
        if typ not in preset_names:
            raise ValueError(f"类型 {typ} 不在 preset")
        effective = raw_forms.get(val, val)
        if effective not in text:
            raise ValueError(f"实体 {val} 不在文本中")
        if typ == "身份证号" and not _id_check(val):
            raise ValueError(f"身份证格式非法 {val}")
        entities.setdefault(typ, []).append(val)

    entry = {
        "id": "",  # 由 main 按时间戳+序号分配
        "bucket": "hardcase",
        "bucket_kind": "hardcase",
        "source": "ingest",
        "input_modality": "text",
        "text": text,
        "entities": entities,
        "origin": origin,
        "story": story,
        "raw_forms": raw_forms,
    }
    assert ENTRY_SCHEMA_KEYS <= set(entry)
    return entry


def _next_id(out_dir: Path) -> str:
    jsonl = out_dir / HARDCASE_JSONL
    n = 0
    if jsonl.exists():
        n = sum(1 for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip())
    return f"hardcase_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{n + 1:03d}"


def _write_entry(entry: dict, out_dir: Path) -> None:
    """先全部校验后写盘：jsonl 追加 + manifest 索引更新（无则按模板建）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / HARDCASE_JSONL).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    manifest_path = out_dir.parent / MANIFEST_NAME
    manifest = {"entries": []}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("entries", []).append(
        {"id": entry["id"], "origin": entry["origin"], "story": entry["story"],
         "ts": datetime.now().isoformat(timespec="seconds")}
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parse_pairs(items: list[str], sep: str, what: str) -> dict[str, str]:
    pairs = {}
    for it in items:
        if sep not in it:
            raise ValueError(f"{what} 格式非法（应为 'A{sep}B'）: {it}")
        k, v = it.split(sep, 1)
        pairs[k] = v
    return pairs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T2 难例沉淀 ingest（自检拒收）")
    ap.add_argument("--file", required=True)
    ap.add_argument("--page", type=int, default=0)
    ap.add_argument("--entity", action="append", default=[], metavar="类型:实体串")
    ap.add_argument("--story", required=True)
    ap.add_argument("--origin", required=True)
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--raw-form", action="append", default=[], metavar="GT串:文中实际形态")
    args = ap.parse_args(argv)

    if not args.entity:
        print("错误：至少提供一个 --entity", file=sys.stderr)
        return 2
    try:
        spans = list(_parse_pairs(args.entity, ":", "--entity").items())
        raw_forms = _parse_pairs(args.raw_form, ":", "--raw-form")
        entry = build_hardcase_entry(Path(args.file), args.page, spans,
                                     story=args.story, origin=args.origin, raw_forms=raw_forms)
        entry["id"] = _next_id(Path(args.out_dir))
        _write_entry(entry, Path(args.out_dir))
    except (ValueError, FileNotFoundError) as e:
        print(f"拒收：{e}", file=sys.stderr)
        return 1

    n_ents = sum(len(v) for v in entry["entities"].values())
    print(f"[hardcase] id={entry['id']} 桶={entry['bucket']} 实体数={n_ents} origin={entry['origin']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
