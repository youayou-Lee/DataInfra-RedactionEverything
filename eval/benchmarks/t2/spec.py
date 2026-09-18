"""T2 benchmark 规格：类型映射表（人工核对锁定）与桶清单。Issue #93 子任务 A。"""

# 映射表 v1（2026-09-18 核对）：原类型 -> preset 中文名；映射不到的类型丢弃并计数。
TYPE_MAPS = {
    "cluener": {
        "name": "姓名", "address": "地址", "organization": "机构名称",
        "company": "公司名称", "government": "机关单位", "position": "职业",
        "scene": "地址",  # 场景按地点语义并入地址，丢弃率报告中单列
    },
    "leven": {
        "被告人": "姓名", "被害人": "姓名", "犯罪嫌疑人": "姓名",
        "法院": "机关单位", "检察院": "机关单位", "公安": "机关单位",
        "地点": "地址",
    },
    "resume": {
        "NAME": "姓名", "LOCATION": "籍贯", "ORGANIZATION": "工作单位",
        "RACE": "民族",
        # EDUCATION 无对应 preset 类型：按丢弃机制处理（Task 2 中计数报告）
    },
}

# 桶清单 v1：bucket -> {kind, source, size, input_modality}
BUCKETS = {
    "cluener-person":          {"kind": "public", "source": "cluener", "size": 200, "input_modality": "text"},
    "cluener-address":         {"kind": "public", "source": "cluener", "size": 200, "input_modality": "text"},
    "cluener-organization":    {"kind": "public", "source": "cluener", "size": 200, "input_modality": "text"},
    "leven-judicial-person":   {"kind": "public", "source": "leven", "size": 200, "input_modality": "text"},
    "leven-judicial-org":      {"kind": "public", "source": "leven", "size": 200, "input_modality": "text"},
    "resume-person":           {"kind": "public", "source": "resume", "size": 200, "input_modality": "text"},
    "resume-native-place":     {"kind": "public", "source": "resume", "size": 200, "input_modality": "text"},
    "digit-confusion":         {"kind": "synthetic", "source": "generator", "size": 50, "input_modality": "text"},
    "quoted-entity":           {"kind": "synthetic", "source": "generator", "size": 50, "input_modality": "text"},
    "long-entity":             {"kind": "synthetic", "source": "generator", "size": 50, "input_modality": "text"},
    "lowfreq-type":            {"kind": "synthetic", "source": "generator", "size": 50, "input_modality": "text"},
    "context-distractor":      {"kind": "synthetic", "source": "generator", "size": 50, "input_modality": "text"},
    "hardcase":                {"kind": "hardcase", "source": "ingest", "size": 50, "input_modality": "text"},  # size 为 ingest 目标配额（brief 原值 0 与测试 size>0 断言冲突，改为正配额）
}

ENTRY_SCHEMA_KEYS = {"id", "bucket", "bucket_kind", "source", "input_modality", "text", "entities"}
