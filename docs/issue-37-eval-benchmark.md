# Issue #37 — 脱敏评测集与一键评测脚本：设计文档

> 分支 `feat/issue37-eval-benchmark`（基底 preview 8a7d62d）。
> 正式 Issue：#37（含安全红线、方法学坑与既有资产复用清单，本文不重复）。

## 1. 需求确认结论（来自 ⓪，2026-09-13 用户确认）

- **目的**：为脱敏效果（P/R/F1 + 数字保真）与速度（分阶段耗时/吞吐）建立可复现基线；服务 #11 Perf 验收与 LLM 替代 HaS_Text 的质量闸门量化。
- **部署形态**：评测集放本地与云实例，fork 仓库为版本载体（git 同步）。
- **化名分工（用户定）**：化名替换只处理**非扫描型 PDF 与纯文本**；扫描型一律合成。
- **入库红线**：真实案卷永不入库；化名子集 GT 不含原文；对照表不入库；入库前 leak_check 零残留。
- v1 规模（Issue 建议，用户确认）：合成 25–35 文件、化名 10–15 文件（化名真实数据集构建需用户参与，见 §8 非目标）。

## 2. 复用清单（不另起炉灶）

| 既有资产 | 位置 | 复用方式 |
|---|---|---|
| `make_ner_gt_corpus.py`（确定性合成、GB11643 校验位、GT 自检、干扰项） | `backend/scripts/eval/` | 文本语料生成核心被 `eval/datasets/generators/gen_text.py` 引用扩展（按路径 import，不搬不抄） |
| `eval_ner_quality.py`（三闸门、P/R/F1、数字三级分级、MD 报告、基线对比） | `backend/scripts/eval/` | `run_eval.py --level ner` 直接 import 其 `compute_metrics`/`digital_gate_pass`/`compare_with_baseline`/`render_markdown`；**口径唯一定义处** |
| `perf_bench.py` 的 duration_ms 分解思路（工作区 perf-work/，不入库） | 迁移思路 | `--level e2e --with-perf` 的速度分解沿用其字段口径 |
| `gen_test_pdf.py`（排版→整页渲染成图） | 工作区 perf-work/ | 迁入 `gen_pdf.py --carrier scanned` 实现，入库 |
| Playground API 链路（login→upload→vision→preview-map→execute） | `backend/app/api/` | `run_eval --level e2e` 与 `build_pseudonym_set.py` 走同一组公开端点 |

## 3. 目录结构（全部新增，零改动现有代码路径）

```
eval/
  README.md                 # 使用说明 + 指标口径 + 环境对比规矩
  datasets/
    manifest.json           # 数据集清单（schema 见 §4）
    synthetic/              # 合成样本（pdf/docx/txt）+ *.gt.json
    pseudonymized/          # 化名样本 + GT（无原文；v1 为空目录 + .gitkeep，管线就绪后填充）
    generators/
      gen_text.py           # 文本语料生成（吸收 make_ner_gt_corpus，加 doc_type/密度参数）
      gen_pdf.py            # 语料→PDF（carrier: text|scanned|hybrid）
      gen_docx.py           # 语料→docx
      build_all.py          # 按 manifest 种子配置一键重建全部合成样本
  scripts/
    run_eval.py             # 一键评测（--level ner|e2e）
    build_pseudonym_set.py  # 化名子集构建管线（走 API，对照表输出到不入库目录）
    leak_check.py           # 入库前残留检测
  reports/
    .gitkeep                # 历史评测报告（评测产出物，入库沉淀基线）
backend/tests/test_eval37_*.py  # 单测（进既有 CI backend job，零新 CI 配置）
```

## 4. GT 与 manifest schema

GT（`<file>.gt.json`，统一按「页 → 实体串集合」表达，与三闸门口径同构）：

```json
{
  "file": "syn_contract_10p_mid.pdf",
  "source": "synthetic | pseudonymized",
  "carrier": "scanned_pdf | text_pdf | hybrid_pdf | docx | txt",
  "seed": 42,
  "pages": [
    {"page": 1, "entities": {"姓名": ["赵伟娜"], "身份证号": ["11010119600101000X"]}}
  ]
}
```

- 实体键用**中文类型名**（与 `make_ner_gt_corpus`、`eval_ner_quality` 一致）；`run_eval` 从 `backend/config/preset_entity_types.json` 读 `id→name` 映射做 e2e 预测侧的英→中归一（不硬编码，防漂移）。
- 数字实体（身份证号/护照号/电话/银行卡号）在生成侧即满足格式合法（校验位/号段），e2e 层正则兜底不误拒。
- v1 **不做视觉框 bbox 口径**：扫描型经 OCR 后框与生成坐标天然偏差，IoU 对齐不稳定（理由见 §7-D3）；e2e 层与 ner 层同用「实体串集合」口径，e2e 额外覆盖 OCR 文本还原链路。bbox IoU 留 v2。

manifest（`manifest.json`）：

```json
{"version": 1, "files": [
  {"id": "syn_contract_10p_mid", "path": "synthetic/syn_contract_10p_mid.pdf",
   "gt": "synthetic/syn_contract_10p_mid.gt.json", "source": "synthetic",
   "carrier": "scanned_pdf", "doc_type": "contract", "density": "mid",
   "pages": 10, "generator": "gen_pdf.py --profile contract_mid --seed 42",
   "levels": ["e2e"], "notes": "性能主力档（对齐 perf_bench 语料规模）"}
]}
```

- `levels`：该文件参与哪些评测层（txt 语料参与 `ner`；PDF/docx 参与 `e2e`；`ner` 层的文本直接取 GT 同源语料，见 §5）。

## 5. 合成子集 v1 矩阵（15 文件 + 10 页 NER 语料）

| id 模式 | carrier | doc_type | 页数 | 密度 | 评测用途 |
|---|---|---|---|---|---|
| syn_contract_1p_mid | scanned_pdf | contract | 1 | mid | 单页延迟 |
| syn_contract_10p_mid | scanned_pdf | contract | 10 | mid | 速度主力（对齐现 perf 语料） |
| syn_contract_50p_mid | scanned_pdf | contract | 50 | mid | 吞吐/批量 |
| syn_judgment_3p_mid | scanned_pdf | judgment | 3 | mid | 文档类型多样性 |
| syn_contract_10p_dense | scanned_pdf | contract | 10 | dense（~40/页） | 高密度召回压力 |
| syn_contract_10p_sparse | scanned_pdf | contract | 10 | sparse（~5/页） | 低召回压力 |
| syn_contract_3p_mid | text_pdf | contract | 3 | mid | 文本层链路 |
| syn_judgment_txt_3p_mid | text_pdf | judgment | 3 | mid | 文本层×类型 |
| syn_warrant_1p_mid | text_pdf | warrant | 1 | mid | 最小文件 |
| syn_hybrid_4p_mid | hybrid_pdf | contract | 4（2 文本层+2 扫描） | mid | 混合载体路由 |
| syn_contract_2p_mid.docx | docx | contract | 2 | mid | docx 链路 |
| syn_judgment_2p_mid.docx | docx | judgment | 2 | mid | docx×类型 |
| syn_contract_txt_1p_mid | txt | contract | 1 | mid | 纯文本 |
| syn_statement_1p_table | txt | bank_statement | 1 | dense 表格 | 表格密集边界 |
| syn_edge_3p_mixed | scanned_pdf | contract | 3（空页+纯表格页+正常页） | mid | 边界页 |
| ner_corpus_10p（JSONL） | txt | 混合 | 10 | mid | `--level ner` 语料（既有 make_ner_gt_corpus 产出） |

- 生成器**确定性**：无 `random`，实体与文本按索引算术派生（沿用 make_ner_gt_corpus 模式）；`build_all.py` 重复运行**全部产物逐字节一致**（PDF trailer /ID 与 docx zip 条目时间戳在生成器内固定；单测锁定全量 sha256）。
- 生成即自检：GT 实体串必须原样出现在对应页文本/文本层中，否则断言失败。
- 密度实现：mid=make_ner_gt_corpus 每页既有量；dense=实体槽位循环 ×3 次并附表格段；sparse=仅保留每页前 2 类实体。
- hybrid：前半页插入文本层、后半页渲染整页图；边界文件含 1 页空白+1 页纯表格（无实体，验证空页不误报、GT 空集合法）。

## 6. 一键评测 `run_eval.py`

```
# NER 引擎层（LLM NER 对比实验入口；直连 OpenAI 兼容端点，绕过 OCR）
python eval/scripts/run_eval.py --level ner \
  --ner-base http://127.0.0.1:8080/v1 --grouping off \
  --target-label has-baseline --env-label scnet-main-2x16g \
  --out eval/reports/

# 端到端层（完整 API 链路：login→upload→逐页 vision→实体抽取）
python eval/scripts/run_eval.py --level e2e \
  --api-base http://127.0.0.1:8000 --suite synthetic \
  --with-perf --target-label preview2.0.0 --env-label scnet-main-2x16g \
  --out eval/reports/
```

- `--level ner`：语料 = `ner_corpus_10p.jsonl`；逐页调 NER → import `eval_ner_quality` 算指标（三闸门、`--baseline` 对比、MD 报告沿用其 render）。
- `--level e2e`：遍历 manifest（`levels` 含 e2e）→ 上传 → 逐页 `POST /api/v1/redaction/{file_id}/vision`（`force=true`，perf_bench 模式）→ 从响应实体框抽 `(类型中文名, 实体串)` 集合为 pred，GT 按页对齐 → 同一 `compute_metrics` 口径算 P/R/F1 + 数字保真。
  - 实体串归一：trim + 全角/半角空格不敏感（与 OCR 输出对齐需要）；数字闸门仍逐字符严判（near_miss 只降级不入闸）。
- `--with-perf`（e2e 附带）：聚合每页 `duration_ms` 分解（OCR/NER/LA/匹配各段均值与 p95）、文件 wall、页/分钟吞吐；warmup 参数 `--warmup-pages N`（默认 1，冷/热分离：首页计时单独列）。
- **环境元数据必录**：`--env-label` 必填（报告文件名与正文首行）；报告记录 api_base/ner_base、git rev（`git rev-parse --short HEAD`，容错无 git）、时间戳、`--target-label`。跨环境不比绝对值（README 规矩）。
- 报告产出：`eval/reports/<date>-<env-label>-<target-label>-<level>.{json,md}`；ner 层复用 `eval_ner_quality.render_markdown`（记录 config/指标/闸门），e2e 层新增渲染（分文件表 + 汇总 + 速度分解表）。
- 认证：`--api-user/--api-pass`（默认 eval_user，自动注册——perf_bench 模式）；测试数据用完即 DELETE（沿用既有纪律）。

## 7. 关键设计决策

- **D1 口径唯一**：指标与闸门的计算全部 import `eval_ner_quality`，`run_eval` 不重写公式；类型归一映射读 `preset_entity_types.json`。两套口径 = 评测集失效。
- **D2 e2e 实体串对齐而非 bbox IoU（v1）**：OCR 框与生成坐标天然偏差使 IoU 不稳定；实体串口径让 ner/e2e 可直接对比（e2e−ner 差值即 OCR/路由链路引入的损失），这是本设计最有诊断价值的输出。bbox IoU 与框级指标列 v2（届时 GT schema 已预留 pages[].entities 扩展位）。
- **D3 化名子集交付管线而非数据（v1）**：真实样本构建需用户人工复核（⓪ 需求确认的 GT 补漏环节），属运营动作不阻塞本 PR。管线正确性用合成 text_pdf 自证（当真实样本走全流程，断言：成品含化名串、GT 定位成功、leak_check 对注入残留用例能抓到）。
- **D4 化名管线走生产 API**：识别→`preview-map`（derived 编号映射）→导出 CSV 供人工复核→`execute`（custom_replacements）→成品；GT 生成 = 在成品文本层/docx XML 中定位化名串出现位置（而非解析 entity_map，避免契约耦合）；对照表仅写 `--private-dir`（不入库目录）。
- **D5 leak_check 双保险**：①对照表每个原文串对化名版全文做规范化 grep（归一空格/全半角）；②对化名版跑一次识别，结果与对照表原文串求交。零命中 → exit 0；任何命中即列出并 exit 1。`build_pseudonym_set.py` 末步自动调用，入库前人工再跑一次。
- **D6 生成器 import 而非复制 make_ner_gt_corpus**：`sys.path` 注入 `backend/scripts/eval` 后 `import make_ner_gt_corpus`（有 test_eval_ner_quality 先例），模板/口径漂移由既有防漂移契约测试继续锁。
- **D7 docx/txt 走 parse + hybrid NER 链路（云实测发现）**：backend vision 端点仅支持 pdf/图片，对 txt/docx 返回 404「Unsupported file type for vision」。docx/txt 载体在 e2e 层改走 `GET /files/{id}/parse → POST /files/{id}/ner/hybrid`（HaS+正则+共指，与 Playground 文本链路一致），指标口径不变（文档级聚合）；另 txt 条目改独立 id `syn_contract_txt_1p_mid`（修复与 scanned 条目的 id 冲突）。

## 8. 非目标（v1 明确不做）

- bbox/IoU 视觉指标（v2，见 D2）；
- 真实化名数据集正式构建与人工复核（管线交付后与用户另约，Issue #37 DoD 该项由「管线就绪+自测通过」承接）；
- 并发用户压测（`e2e/concurrent_users.py` 既有能力，另行触发）；
- 报告历史趋势 UI（`--baseline` 数值 diff 已覆盖核心诉求）；
- CI 中跑评测本身（评测需 GPU 服务，CI 只跑单测）。

## 9. 测试计划（backend/tests/test_eval37_*.py，进既有 CI backend job）

- `test_eval37_generators.py`：
  - 确定性：`build_all` 全量生成两次，sha256 逐一相等；
  - GT 自检：随机抽文件，GT 实体串原样出现在对应页文本（text_pdf 用 fitz 提文本层，scanned 用语料源文本）；
  - 矩阵完整性：manifest 列 16 项与 §5 表一一对应，文件与 GT 均存在；
  - 格式合法：身份证校验位、手机号段、银行卡长度抽验；
  - 边界：hybrid 前半有文本层后半无；edge 文件空页 GT 为空 dict 不报错。
- `test_eval37_leak_check.py`：干净化名版 → pass；注入一个原文残留 → fail 且指明串与页；归一化变体（全角空格）同样被抓。
- `test_eval37_run_eval.py`（纯函数，不发网络请求）：
  - e2e 实体抽取：构造假 vision 响应（英文类型+带空白实体串）→ 归一为中文名集合；
  - 指标：GT/pred 构造样例 → P/R/F1、数字三级与 `eval_ner_quality` 口径一致（含空页、空类型）；
  - 报告文件名与环境元数据字段；`--with-perf` 聚合（假 duration_ms）的 p95/均值正确。
- `test_eval37_pseudonym_pipeline.py`：合成 text_pdf 输入（mock API 响应或本地直接调纯函数段）→ 映射草稿生成、GT 定位、（leak 注入）全链路断言。
- 既有全量测试零回归。

## 10. 验收标准（DoD）

**自动化（②③）**：`python -m pytest tests -q`（backend）全绿，含上述新测试；ruff 通过（eval 脚本纳入 lint 范围或显式豁免，取仓库现状低摩擦方案）。

**云实例端到端（④，scnet-main 跑 preview2.0.0）**：
1. rsync eval/ 上云，`run_eval --level ner` 对 HaS 端点跑通，产出基线报告入库 `eval/reports/`；
2. `run_eval --level e2e --with-perf` 跑合成子集全量：数字保真 100%（若 Fail 须给出逐项明细并定位是模型还是 OCR 链路）、P/R/F1 与速度分解合理（与 #11 实测量级一致：scnet-main 单页 ~50s 级）；
3. 50 页档跑通无超时，吞吐数字落报告；
4. 评测账号与上传文件测完清理。

**用户手动验收（⑥）**：按 `docs/acceptance/手动验收-Issue37-评测集.md`（工作区，不入库）——本地与隧道云各跑一次一键命令、查看 MD 报告的关键节、抽查一个合成 PDF 内容与 GT 一致；（可选）拿一份真实非扫描样本走 build_pseudonym_set 演示。用户放行后 merge。
