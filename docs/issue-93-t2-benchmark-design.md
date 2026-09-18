# Issue #93 子任务 A — T2 实体识别 Benchmark 设计文档

> 2026-09-18 brainstorming 定稿（四决策+方案 A），父 Issue：fork #93（按任务边界的环节级 Benchmark 体系 T1–T4）。
> 定位：**调优/选型基准**，首要消费者 = #73（小 LLM 替代 HaS_Text）的 E1–E4 质量矩阵。

## 0. 已拍板决策（brainstorming 四问）

| # | 决策 | 结论 |
|---|---|---|
| D1 | 实体类型口径 | **映射到我们的类型体系**（`preset_entity_types.json`），映射表人工核对锁定版本，映射不到的类型丢弃并计数 |
| D2 | 司法桶数据来源 | **LEVEN 映射当司法桶主力 + 合成补公开集没有的失败模式桶（数字串混淆等）+ 难例沉淀**；零人工标注，不阻塞 #73 E1 |
| D3 | 数据存储 | **benchmark 数据一律不入 GitHub**；正本放云实例持久卷（仅用户可访问），本地 `test-data/` 同步副本；仓库只放代码+规格+manifest 模板 |
| D4 | 输入通道 | **本期只做文本通道**，条目保留 `input_modality: text\|image` 字段、桶目录预留 image 位，VLM 来了加 runner 即可 |

整体架构选**方案 A**：独立 T2 模块（`eval/benchmarks/t2/`），复用 `eval_ner_quality.py` 指标口径，不挂进 run_eval（选型工具无闸门、不 exit 1，与回归评测语义分工）。

## 1. 数据布局

云持久卷（数据正本，唯一可信源）：

```
/root/private_data/benchmarks/t2/
  raw/                    # 公开集原始下载物（cluener/leven/resume，含版本戳）
  buckets/
    public/               # 能力基线桶（CLUENER/Resume 映射）
    judicial/             # 司法桶（LEVEN 映射）+ 合成失败模式桶
    hardcase/             # 难例桶
  manifest.private.json   # 数据源 URL+版本+许可证、映射表版本、桶清单、条目索引
```

- 本地 `test-data/benchmarks/t2/` 为同步副本（eval37-real 同款双份模式）。
- 仓库内：`eval/benchmarks/t2/`（代码）+ 桶规格 README + `manifest.private.example.json`，**零数据**。
- 可复现机制：子采样固定 seed，适配器从 `raw/` 现场重建 `buckets/`；manifest 记录 raw 版本戳防上游漂移。

## 2. 内部 GT 格式与类型映射

条目格式（与 #37 口径同构，直接喂 `eval_ner_quality` 的指标计算）：

```json
{"id": "leven_defendant_0007", "bucket": "leven-judicial-person",
 "bucket_kind": "public|synthetic|hardcase", "source": "leven",
 "input_modality": "text",
 "text": "……", "entities": {"姓名": ["陈某"], "机构名称": ["某市人民法院"]}}
```

- 映射表为代码内显式常量（如 `LEVEN_TYPE_MAP = {"被告人": "姓名", "被害人": "姓名", "法院": "机构名称", …}`），人工核对后锁版本；映射表版本写入 manifest。
  > **勘误（2026-09-18 实测）**：LEVEN（thunlp/LEVEN 官方 jsonl）仅有事件触发词标注（108 种事件类型），**无实体/论元标注**，上表中的 被告人/法院 等类型在数据中不存在。故 `leven` 映射表已置空（见 `spec.py`），leven 两桶 BLOCKED；司法领域覆盖改由 hardcase + 合成桶承接，替代数据集待选。
- 映射不到的类型丢弃，**丢弃率进适配报告与 manifest**（防悄悄丢掉大半标注）。
- 桶的维度分两层：**公开桶按「领域×实体类型」**（公开集无失败模式标注），**合成桶与难例桶按失败模式**。`bucket_kind` 字段区分。
- 难例条目额外字段：`origin`（Issue 链接）、`story`（一句话失败故事）、`raw_form`（OCR 噪声形态，如多空格原文）。

## 3. 桶清单与首版规模

| 桶 | 来源 | 规模（首版） |
|---|---|---|
| cluener-person / -address / -organization | CLUENER 映射 | 各 200 句 |
| leven-judicial-person / -judicial-org | LEVEN 映射 | 各 200 句（勘误 2026-09-18：LEVEN 无实体标注，两桶 BLOCKED，见 §2 勘误注记；替代数据集待选） |
| resume-person / -native-place | Resume NER 映射 | 各 200 句 |
| digit-confusion（身份证/案号/车牌/银行卡同现互扰） | 合成 | 50 条 |
| quoted-entity / long-entity / lowfreq-type / context-distractor | 合成 | 各 50 条 |
| hardcase（首批：曾庆成案数字串、#60 表格跨行机构名） | 难例转录 | 存量难例全收 |

数字实体公开集没有，**数字保真只在合成桶 + hardcase 桶上评**——这两桶专为 HaS 痛处（数字串互扰）设计。

## 4. runner 与引擎申报

`eval/benchmarks/t2/benchmark_t2.py`：

- 引擎注册表：`--engine has`（复用 `eval_ner_quality` 直连与生产 prompt 对齐）｜`--engine llm=<OpenAI 兼容端点>`（vLLM/llama-server，#73 E1 直接可用）｜将来 `--engine vlm=<…>`（图像通道占位，本期 N/A）。
- 引擎申报制：引擎不支持的桶记 **N/A 不记零分**。
- `--buckets` 选桶、`--baseline <json>` 出 Δ 列、多引擎同跑出对比表。
- 输出：桶×引擎 P/R/F1 明细 + 数字保真桶级三级分级（exact/near_miss/miss）+ N/A 标注，json+md 报告入 `eval/reports/`（Obsidian 简版，沿用 `indicator_meta`）。（deferred：`--baseline` Δ 列与 `indicator_meta` 版式延期至 #73 E2 阶段按需实现。）
- **无闸门**：不 exit 1，结论写进报告（与 run_eval 三闸门语义分工）。

## 5. 难例沉淀 skill（hardcase-ingest）

- Skill 位置：工作区 `.zcode/skills/hardcase-ingest/`；封装脚本 `eval/benchmarks/t2/ingest_hardcase.py`（入库）。
- 动线：用户提供「文件 + 页码或片段 + 一句说明（如『这页的案号漏了』）」→ skill 引导脚本：
  1. 提取该页/片段文本（fitz）；
  2. 从说明解析实体与类型——类型必须落在 preset 内，拿不准列选项让用户确认；
  3. **自检**：每个 GT 实体真实在文中（OCR 噪声形态记 `raw_form`）；数字实体校验格式；
  4. 写入云+本地 hardcase 桶 + 登记 manifest；
  5. 回显条目摘要供用户过目。
- **失败即拒收并说明原因，不产半成品。**

## 6. 测试策略（CI 零数据依赖）

| 层 | 测试 |
|---|---|
| 适配器 | 内嵌 mini fixture（每集 3-5 条写死在测试文件，属代码不入数据目录）：映射正确性 + 丢弃计数 |
| 映射表 | 契约测试：映射目标全部存在于 `preset_entity_types.json` |
| 合成生成器 | GT 自检（实体原样在文中、数字格式合法）+ 确定性（同 seed 逐字节一致） |
| runner | 离线 stub 引擎测编排与报告渲染 |
| ingest | 自检失败拒收各用例 |

## 7. 非目标

- 不做图像通道（D4，接口留位）；
- 不做 T1/T3 benchmark（子任务 B/C 另行设计）；
- 不改 #37 run_eval 与三闸门；
- 不做化名管线/leak_check 入库门（数据不上 GitHub，D3）；
- 不设闸门 exit code（选型工具）。
