# T2 实体识别 benchmark（NER 环节选型对比，无闸门）

## 这个模块评测的是哪个环节？

脱敏流水线分三段：**T1 文本提取**（OCR，图变文字）→ **T2 实体识别**（NER，从文字里找出姓名/身份证号/案号等敏感信息）→ **T3 定位**（LA，在页面上框出位置）。本模块**只评测 T2（NER）**：给引擎一段文字，看它找实体找得全不全、准不准。T1/T3 的 benchmark 是 Issue #93 的子任务 B/C，另行建设；端到端整体评测见 `eval/scripts/run_eval.py`。

用途：给「换 NER 引擎」当裁判（如 #73 用小 LLM 替代 HaS_Text）——新旧引擎在同一套桶上考试，桶级明细回答「谁在哪类难题上赢/输」。**选型工具，不做闸门**（不卡发布、不设阈值判定、不 exit 1）。

考试对象是**文本**（`input_modality: text`）；将来 VLM 直接吃图参赛时扩展图像通道（设计文档 D4 留位）。

## 桶清单

人类可读版，权威定义见 `spec.py` 的 `BUCKETS`（bucket -> kind/source/size/input_modality）：

| 桶名 | kind | source | size | 说明 |
|---|---|---|---|---|
| cluener-person | public | cluener | 200 | 公开集，人名 |
| cluener-address | public | cluener | 200 | 公开集，地址（含 scene 并入） |
| cluener-organization | public | cluener | 200 | 公开集，机构 |
| leven-judicial-person | public | leven | 200 | **BLOCKED**：LEVEN 只有事件触发词标注（108 种事件类型），无任何实体/论元标注，候选为 0，需换数据源或人工映射（见 task-2-report.md） |
| leven-judicial-org | public | leven | 200 | **BLOCKED**：同上 |
| resume-person | public | resume | 200 | 公开集，简历人名 |
| resume-native-place | public | resume | 200 | 公开集，籍贯 |
| digit-confusion | synthetic | generator | 50 | 数字串混淆（身份证/案号/车牌/电话/银行卡） |
| quoted-entity | synthetic | generator | 50 | 引述语境内实体 |
| long-entity | synthetic | generator | 50 | 长实体（拼接机构名） |
| lowfreq-type | synthetic | generator | 50 | 低频类型（民族/宗教信仰/政治面貌/国籍） |
| context-distractor | synthetic | generator | 50 | 上下文干扰词紧贴实体 |
| hardcase | hardcase | ingest | 50 | 真实难例人工沉淀（size 为 ingest 目标配额） |

## 条目 schema

每桶一个 jsonl，每行一个条目，最小必填键集见 `spec.py` 的 `ENTRY_SCHEMA_KEYS`：

| 字段 | 说明 |
|---|---|
| id | 条目唯一标识（hardcase 为 `hardcase_<时间戳>_<序号>`） |
| bucket / bucket_kind | 所属桶名 / 桶类别（public / synthetic / hardcase） |
| source | 数据来源（cluener / leven / resume / generator / ingest） |
| input_modality | 输入模态（当前均为 text） |
| text | 待脱敏文本 |
| entities | `{类型中文名: [实体串]}`，类型必须在 preset（`backend/config/preset_entity_types.json`） |
| origin | （hardcase）来源 issue 链接，必填 |
| story | （hardcase）一句话失败故事 |
| raw_forms | （hardcase，可选）GT 串 -> 文中实际形态 |

## 数据目录约定（仓库零数据红线）

- 仓库内**不落任何数据本体**；jsonl 与 PDF 只存在私有目录。
- 云端：`/root/private_data/benchmarks/t2/`（scnet-main，`/root/redaction` 仓库同构执行）。
- 本地：`<仓库根>/test-data/benchmarks/t2/`（git 忽略，仅本机调试）。
- hardcase 同目录上级维护 `manifest.private.json` 私有索引模板（id/origin/story/ts），由 `ingest_hardcase.py` 自动追加。

## 快速用法

1. **构建桶数据**（写入上述私有目录，公开集经 `adapters.py` 转换、合成桶用 `gen_failure_buckets.py`、难例用 `ingest_hardcase.py`）：

```bash
PYTHONPATH=$PWD .venv-eval/bin/python eval/benchmarks/t2/build_buckets.py \
  --raw-dir test-data/benchmarks/t2/raw \
  --out-dir test-data/benchmarks/t2
```

输出 `buckets/*.jsonl` + `manifest.private.json`（结构模板见 `manifest.private.example.json`；leven 桶 BLOCKED 打印提示、hardcase 桶跳过由 ingest 维护）。
2. **跑 benchmark**：

```bash
PYTHONPATH=$PWD .venv-eval/bin/python eval/benchmarks/t2/benchmark_t2.py \
  --data-dir <私有桶 jsonl 目录> \
  --engine has=http://localhost:8000
```

可选 `--buckets <逗号分隔桶名>` 过滤、`--out` 指定报告目录（默认 `eval/reports`）。

## 难例沉淀

真实难例入库走工作区 `hardcase-ingest` skill（引导解析实体、确认 preset 类型、调 ingest CLI、拒收即转述原因）。
