"""公开集适配器：原格式 -> T2 内部条目。数据本体在云/本地私有目录，本模块只做转换。Ref #93"""
import json
import random
from pathlib import Path

import spec


def parse_cluener_line(line: dict, type_map: dict[str, str]) -> tuple[str, dict[str, list[str]]]:
    """CLUENER/Resume 共用 jsonl 格式：label={type: {span: {start_str: end_str}}}。
    span 已在 text 中，直接按映射表转类型；未映射类型丢弃（调用方累计丢弃数）。
    """
    entities: dict[str, list[str]] = {}
    for src_type, spans in (line.get("label") or {}).items():
        our_type = type_map.get(src_type)
        if not our_type:
            continue
        entities.setdefault(our_type, []).extend(str(s) for s in spans.keys())
    return line["text"], {k: sorted(set(v)) for k, v in entities.items()}


def leven_trigger_spans(doc: dict) -> list[tuple[str, int, str, str, str, int, int]]:
    """抽取 LEVEN 官方 jsonl（thunlp/LEVEN，train/dev）中的全部触发词 mention。

    实测格式（据官方 Baselines 读取代码 utils_leven.py / LevenReader.py 核对，2026-09-18）：
      {"id": str,
       "content": [{"tokens": [str, ...]}, ...],          # 每句一个 token 列表（中文按字切分）
       "events": [{"type": str, "type_id": int,
                   "mention": [{"id": str, "sent_id": int, "offset": [L, R]}]}],
       "negative_triggers": [{"id", "sent_id", "offset"}]}

    注意：LEVEN 只标注事件触发词（108 种事件类型，offset 为该句 tokens 上的 [L, R) 字符区间），
    没有任何实体/论元标注。negative_triggers 是未触发候选，抽取时忽略。

    返回 (doc_id, sent_id, sentence_text, event_type, trigger_text, L, R) 列表。
    """
    spans = []
    for event in doc.get("events") or []:
        etype = event["type"]
        for mention in event.get("mention") or []:
            sent_id = mention["sent_id"]
            tokens = doc["content"][sent_id]["tokens"]
            left, right = mention["offset"][0], mention["offset"][1]
            spans.append((doc.get("id", ""), sent_id, "".join(tokens),
                         etype, "".join(tokens[left:right]), left, right))
    return spans


def parse_leven_doc(doc: dict,
                    type_map: dict[str, str] | None = None) -> list[tuple[str, dict[str, list[str]]]]:
    """LEVEN doc -> 每个句子一条 (text, {preset类型: [触发词串]})。

    LEVEN 仅有触发词标注（见 leven_trigger_spans docstring），事件类型（如 盗窃财物/拘捕）
    无法语义映射到 preset 实体类型（姓名/机关单位/地址...），因此 spec.TYPE_MAPS["leven"]
    当前为空表：所有触发词按"未映射丢弃"计数，leven 桶候选为 0（BLOCKED，详见任务报告）。
    type_map 参数供测试/后续换映射时验证抽取链路。
    """
    tm = spec.TYPE_MAPS["leven"] if type_map is None else type_map
    per_sentence: dict[int, dict[str, list[str]]] = {}
    for _, sent_id, _, etype, trigger_text, _, _ in leven_trigger_spans(doc):
        our_type = tm.get(etype)
        if not our_type:
            continue
        per_sentence.setdefault(sent_id, {}).setdefault(our_type, []).append(trigger_text)
    out = []
    for sent_id, tokens in enumerate(doc.get("content") or []):
        text = "".join(tokens["tokens"])
        entities = {k: sorted(set(v)) for k, v in per_sentence.get(sent_id, {}).items()}
        out.append((text, entities))
    return out


def adapt_dataset(source: str, raw_path: Path, bucket_prefix: str, size: int, seed: int,
                  bucket_meta: dict | None = None) -> tuple[list[dict], dict]:
    """raw 文件 -> 该 source 的全部桶条目 + 丢弃统计。按 seed 确定性采样。"""
    type_map = spec.TYPE_MAPS[source]
    meta = bucket_meta or {}
    dropped: dict[str, int] = {}
    candidates: list[tuple[str, dict[str, list[str]]]] = []
    if source in ("cluener", "resume"):
        for raw_line in raw_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            line = json.loads(raw_line)
            before = sum(len(v) for v in (line.get("label") or {}).values())
            text, entities = parse_cluener_line(line, type_map)
            after = sum(len(v) for v in entities.values())
            dropped[source] = dropped.get(source, 0) + (before - after)
            if entities:
                candidates.append((text, entities))
    else:  # leven
        for raw_line in raw_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            doc = json.loads(raw_line)
            n_triggers = sum(len(e.get("mention") or []) for e in doc.get("events") or [])
            n_mapped = 0
            for text, entities in parse_leven_doc(doc):
                n_mapped += sum(len(v) for v in entities.values())
                if entities:
                    candidates.append((text, entities))
            dropped[source] = dropped.get(source, 0) + (n_triggers - n_mapped)

    rng = random.Random(seed)
    rng.shuffle(candidates)
    kind = meta.get("kind", "public")
    entries = []
    for i, (text, entities) in enumerate(candidates[:size]):
        entries.append({"id": f"{bucket_prefix}_{i:04d}", "bucket": bucket_prefix,
                        "bucket_kind": kind, "source": source,
                        "input_modality": "text", "text": text, "entities": entities})
    return entries, {"dropped_spans": dropped, "total_candidates": len(candidates),
                     "sampled": len(entries)}
