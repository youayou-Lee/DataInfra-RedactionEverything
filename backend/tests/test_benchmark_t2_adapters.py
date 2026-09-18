"""T2 公开集适配器测试：CLUENER / Resume / LEVEN。数据零入库，fixture 内嵌。Ref #93"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "eval" / "benchmarks" / "t2"))
import adapters  # noqa: E402
import spec  # noqa: E402

# CLUENER/Resume 官方 jsonl 格式：{"text":..., "label": {type: {span: {start: "end"}}}}
CLUENER_LINE = {"text": "张伟在北京市朝阳区人民法院参加了听证会。",
                "label": {"name": {"张伟": {"0": "1"}},
                          "address": {"北京市朝阳区": {"3": "9"}},
                          "government": {"朝阳区人民法院": {"5": "13"}},
                          "position": {"院长": {"100": "101"}}}}

RESUME_LINE = {"text": "李雷，汉族，生于内蒙古包头市，现就职于腾讯科技有限公司。",
               "label": {"NAME": {"李雷": {"0": "2"}},
                         "RACE": {"汉族": {"3": "5"}},
                         "LOCATION": {"内蒙古包头市": {"8": "13"}},
                         "ORGANIZATION": {"腾讯科技有限公司": {"18": "26"}},
                         "EDUCATION": {"本科": {"40": "42"}}}}


def test_parse_cluener_line_maps_and_spans():
    text, entities = adapters.parse_cluener_line(CLUENER_LINE, spec.TYPE_MAPS["cluener"])
    assert text == CLUENER_LINE["text"]
    assert entities["姓名"] == ["张伟"]
    assert entities["地址"] == ["北京市朝阳区"]
    assert entities["机关单位"] == ["朝阳区人民法院"]
    assert entities["职业"] == ["院长"]


def test_parse_cluener_drop_unmapped():
    line = {"text": "测试", "label": {"movie": {"流浪地球": {"0": "4"}}}}
    _, entities = adapters.parse_cluener_line(line, spec.TYPE_MAPS["cluener"])
    assert entities == {}
    assert all(k in {"姓名", "地址", "机构名称", "公司名称", "机关单位", "职业"} for k in entities)


def test_parse_resume_line():
    text, entities = adapters.parse_cluener_line(RESUME_LINE, spec.TYPE_MAPS["resume"])
    assert text == RESUME_LINE["text"]
    assert entities["姓名"] == ["李雷"]
    assert entities["民族"] == ["汉族"]
    assert entities["籍贯"] == ["内蒙古包头市"]
    assert entities["工作单位"] == ["腾讯科技有限公司"]
    assert "EDUCATION" not in entities  # 未映射类型不出现


def test_adapt_dataset_deterministic_and_schema(tmp_path):
    lines = [dict(CLUENER_LINE, text=f"样本{i}号张伟在朝阳。", label={"name": {"张伟": {"0": "1"}}})
             for i in range(10)]
    raw = tmp_path / "cluener.jsonl"
    raw.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    a = adapters.adapt_dataset("cluener", raw, "cluener-person", 5, seed=42)
    b = adapters.adapt_dataset("cluener", raw, "cluener-person", 5, seed=42)
    assert json.dumps(a[0], ensure_ascii=False) == json.dumps(b[0], ensure_ascii=False)
    entry = a[0][0]
    assert set(entry) >= spec.ENTRY_SCHEMA_KEYS
    assert entry["bucket"] == "cluener-person" and entry["bucket_kind"] == "public"
    assert entry["source"] == "cluener" and entry["input_modality"] == "text"
    assert entry["entities"]["姓名"] == ["张伟"]
    assert a[1]["sampled"] == 5 and a[1]["total_candidates"] == 10


def test_adapt_dataset_drop_stats_and_ids(tmp_path):
    lines = [CLUENER_LINE, {"text": "无实体文本", "label": {}},
             {"text": "仅电影", "label": {"movie": {"流浪地球": {"0": "4"}}}}]
    raw = tmp_path / "mixed.jsonl"
    raw.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    entries, stats = adapters.adapt_dataset("cluener", raw, "cluener-person", 10, seed=7)
    assert stats["dropped_spans"]["cluener"] == 1  # movie 丢弃
    # 无实体/全丢弃的行不产生候选
    assert stats["total_candidates"] == 1
    assert [e["id"] for e in entries] == [f"cluener-person_{i:04d}" for i in range(len(entries))]


def test_adapt_dataset_resume(tmp_path):
    raw = tmp_path / "resume.jsonl"
    raw.write_text(json.dumps(RESUME_LINE, ensure_ascii=False), encoding="utf-8")
    entries, stats = adapters.adapt_dataset("resume", raw, "resume-person", 5, seed=1)
    assert stats["dropped_spans"]["resume"] == 1  # EDUCATION 丢弃
    assert len(entries) == 1
    assert entries[0]["bucket"] == "resume-person"


# ---- LEVEN：真实格式（thunlp/LEVEN 官方 jsonl，仅触发词标注，见 parse_leven_doc docstring）----

LEVEN_DOCS = [
    # 触发词 offset 为 [L, R)，针对 content[sent_id]["tokens"] 的字符级区间
    {"id": "train-0",
     "content": [{"tokens": list("张三盗窃财物后被公安机关拘捕。")}],
     "events": [{"type": "盗窃财物", "type_id": 10,
                 "mention": [{"id": "T1", "sent_id": 0, "offset": [2, 6]}]},
                {"type": "拘捕", "type_id": 22,
                 "mention": [{"id": "T2", "sent_id": 0, "offset": [12, 14]}]}],
     "negative_triggers": [{"id": "N1", "sent_id": 0, "offset": [0, 2]}]},
    {"id": "train-1",
     "content": [{"tokens": list("经审理查明。")}, {"tokens": list("被告人如实供述罪行。")}],
     "events": [{"type": "供述", "type_id": 5,
                 "mention": [{"id": "T3", "sent_id": 1, "offset": [5, 7]}]}],
     "negative_triggers": []},
    {"id": "train-2",
     "content": [{"tokens": list("没有任何事件的空白判决书。")}],
     "events": [],
     "negative_triggers": []},
]


def test_leven_trigger_extraction_real_format():
    spans = adapters.leven_trigger_spans(LEVEN_DOCS[0])
    assert spans == [("train-0", 0, "张三盗窃财物后被公安机关拘捕。", "盗窃财物", "盗窃财物", 2, 6),
                     ("train-0", 0, "张三盗窃财物后被公安机关拘捕。", "拘捕", "拘捕", 12, 14)]


def test_parse_leven_doc_returns_sentence_texts():
    out = adapters.parse_leven_doc(LEVEN_DOCS[1])
    # 每个句子一条：文本 + 映射后实体（当前 leven 映射表为空 -> 实体为空 dict）
    assert [text for text, _ in out] == ["经审理查明。", "被告人如实供述罪行。"]
    assert all(entities == {} for _, entities in out)


def test_parse_leven_doc_with_mapping():
    # 用一个临时映射验证真实抽取链路（触发词文本按 offset 切出，忽略 negative_triggers）
    out = adapters.parse_leven_doc(LEVEN_DOCS[0], type_map={"拘捕": "机关单位"})
    text, entities = out[0]
    assert text == "张三盗窃财物后被公安机关拘捕。"
    assert entities == {"机关单位": ["拘捕"]}


def test_adapt_dataset_leven_empty_mapping_blocks(tmp_path):
    raw = tmp_path / "leven.jsonl"
    raw.write_text("\n".join(json.dumps(d, ensure_ascii=False) for d in LEVEN_DOCS), encoding="utf-8")
    entries, stats = adapters.adapt_dataset("leven", raw, "leven-judicial-person", 5, seed=3)
    # 当前 TYPE_MAPS["leven"] 为空表（BLOCKED：LEVEN 无实体标注），候选为 0
    assert entries == []
    assert stats["total_candidates"] == 0
    assert stats["dropped_spans"]["leven"] == 3  # 3 个触发词全部因未映射丢弃
