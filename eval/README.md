# 脱敏评测集与一键评测（Issue #37）

为脱敏**效果**（P/R/F1 + 数字保真）与**速度**（分阶段耗时/吞吐）提供可复现基线。
设计文档：`docs/issue-37-eval-benchmark.md`；需求与安全红线：Issue #37。

## 快速开始

```bash
# 0) 重建合成评测集（确定性，产物入库；改矩阵后重跑）
python eval/datasets/generators/build_all.py

# 1) NER 引擎层：合成语料直连 NER 端点（LLM NER 对比实验入口）
python eval/scripts/run_eval.py --level ner \
    --ner-base http://127.0.0.1:8080/v1 --grouping off \
    --target-label has-baseline --env-label <你的环境标签>

# 2) 端到端层：走 backend 公开 API（login→upload→逐页识别）
python eval/scripts/run_eval.py --level e2e --suite synthetic \
    --api-base http://127.0.0.1:8000 \
    --target-label preview2.0.0 --env-label <你的环境标签>

# 3) 化名子集构建（真实非扫描样本，两段式，中间人工复核）
python eval/scripts/build_pseudonym_set.py draft 真实样本.docx \
    --private-dir /root/private_data/eval37 --dataset-id pseudo_case_001 --api-base ...
#    …人工复核 mapping.draft.csv…
python eval/scripts/build_pseudonym_set.py finalize 复核后.csv \
    --source 真实样本.docx --private-dir ... --dataset-id pseudo_case_001

# 4) 入库前残留复检（可独立重跑）
python eval/scripts/leak_check.py eval/datasets/pseudonymized/pseudo_case_001.docx \
    --mapping /root/private_data/eval37/pseudo_case_001.mapping.final.csv
```

报告输出到 `eval/reports/<日期>-<环境标签>-<目标标签>-<层级>.{json,md}`。

## 指标口径（唯一口径在 backend/scripts/eval/eval_ner_quality.py，本目录脚本全部 import 复用）

| 指标 | 口径 |
|---|---|
| P / R / F1 | 实体串集合精确匹配；ner 层原串域，e2e 层**去空白域**（中文实体 OCR 空格噪声不虚罚召回） |
| 数字保真（一票否决） | 身份证号/护照号/电话/银行卡号在**原串域**逐字符分级：exact（逐字一致）/ near_miss（仅空白连字符大小写差异）/ miss；闸门只认 exact = 100% |
| 三闸门（--baseline） | 召回 ≥ 基线 −1pp；精确率 ≥ 基线；数字 exact = 100%（LLM NER 实验的判定基准，与 #23 M4 同源） |
| 速度 | 单页 duration_ms 分解（OCR/NER/LA/匹配埋点）、页墙钟 p50/p95、吞吐（页/分钟）；warmup 页（默认前 1 页）不计入 steady |

e2e−ner 的差值 = OCR/路由链路引入的质量损失（最有诊断价值的对比）。

## 环境对比规矩（硬规矩）

- `--env-label` **必填**：报告文件名与正文首行都带它。
- **跨环境不比绝对值**：速度数字只在同环境标签（同卡型/卡数/量化档/并发参数）内可比；
  跨环境只比相对值（如加速比）与质量指标。
- 报告 JSON 内含 git rev、时间、目标地址，保证可追溯。

## 入库安全红线

- 合成子集（`datasets/synthetic/`）：全虚构内容 + 确定性生成器，直接入库。
- 化名子集（`datasets/pseudonymized/`）：只能由 `build_pseudonym_set.py finalize`
  产出（执行自检零残留 + GT 定位成功 + leak_check 零残留三重闸），GT 不含原文。
- **对照表/复核 CSV/原始真实样本只存在于 `--private-dir` 指定的私有目录，永不入库。**
- 化名仅用于非扫描型 PDF 与纯文本（需求确认）；扫描型一律合成。

## 合成子集矩阵

`build_all.py` 内 `MATRIX` 是唯一事实源（载体 × 文档类型 × 规模 × 密度 + 边界样本，
共 15 文件 + 10 页 NER 语料）。扩展时改 MATRIX 重新生成，manifest 自动更新；
`manifest.json` 驱动 e2e 评测的文件选择。

## 命名与缩写

| 名词 | 含义 |
|---|---|
| carrier | 载体：scanned_pdf / text_pdf / hybrid_pdf / docx / txt |
| density | 实体密度：sparse（~2/页）/ mid（~13/页）/ dense（~40/页） |
| steady 页 | 排除 warmup 后的页（速度统计口径） |
| squash 域 | 去全部空白后的实体串（e2e P/R 对齐域） |
