> [!tip] 相关文档
> Issue：[#46](https://github.com/youayou-Lee/DataInfra-RedactionEverything/issues/46) ｜ 设计：[[issue-46-format-matrix-testing]] ｜ 样张：`eval/datasets/formats/`

# Issue #46：格式 × 处理方式实测矩阵报告

- 环境：`scnet-main`（http://127.0.0.1:18000）　suite：full　执行：2026-09-15T16:42:56 → 2026-09-15T16:47:54
- 结果：21/25 格 PASS，3 FAIL，1 SKIP，0 ERROR；异常例 2/5 PASS

> [!summary] 一句话诊断
> **15 类格式可承诺支持**（bmp、docx、gif、htm、html、jpeg、jpg、md、png、scanned_pdf、text_pdf、tif、tiff、txt、webp）；**2 类建议前端禁用**（doc、rtf）。异常路径 2/5 通过。

## 三档结论速览

| 格式 | 结论 | 打码格 | 化名格 | 依据 |
|---|---|---|---|---|
| txt | ✅ 承诺支持 | ✅ | ✅ | G1-G4 全过 |
| docx | ✅ 承诺支持 | ✅ | ✅ | G1-G4 全过 |
| text_pdf | ✅ 承诺支持 | ✅ | ✅ | G1-G4 全过 |
| scanned_pdf | ✅ 承诺支持 | ✅ | —（产品边界） | G1-G4 全过 |
| md | ✅ 承诺支持 | ✅ | ✅ | G1-G4 全过 |
| html | ✅ 承诺支持 | ✅ | ✅ | G1-G4 全过 |
| htm | ✅ 承诺支持 | ✅ | ✅ | G1-G4 全过 |
| rtf | ⛔ 前端禁用 | ❌ | ❌ | g4：成品残留原文 2 处: ['110118197712242590', '14145904781'] |
| doc | ⛔ 前端禁用 | ❌ | ⏭️ | g2：命中解析兜底文案: [无法解析 .doc 文件…（转换链不可用） |
| jpg | ✅ 承诺支持 | ✅ | —（产品边界） | G1-G4 全过 |
| jpeg | ✅ 承诺支持 | ✅ | —（产品边界） | G1-G4 全过 |
| png | ✅ 承诺支持 | ✅ | —（产品边界） | G1-G4 全过 |
| bmp | ✅ 承诺支持 | ✅ | —（产品边界） | G1-G4 全过 |
| gif | ✅ 承诺支持 | ✅ | —（产品边界） | G1-G4 全过 |
| webp | ✅ 承诺支持 | ✅ | —（产品边界） | G1-G4 全过 |
| tif | ✅ 承诺支持 | ✅ | —（产品边界） | G1-G4 全过 |
| tiff | ✅ 承诺支持 | ✅ | —（产品边界） | G1-G4 全过 |

## 失败明细

- **rtf×mask**（hard）：g4：成品残留原文 2 处: ['110118197712242590', '14145904781']
- **rtf×pseudonym**（hard）：g4：成品残留原文 2 处: ['110118197712242590', '14145904781']
- **doc×mask**（hard）：g2：命中解析兜底文案: [无法解析 .doc 文件…（转换链不可用）

## 异常路径

| 用例 | 预期 | 结果 | 观察 |
|---|---|---|---|
| truncated_docx | 截断 docx → 结构化错误 | ⛔ FAIL | upload HTTP 200；parse HTTP 500，content 0 字 |
| fake_ext_pdf | txt 内容伪造 .pdf 扩展名 | ✅ PASS | upload HTTP 400（{"error_code":"HTTP_400","message":"文件内容与扩展名 .pdf 不匹配，可能是伪造文件","detail":{},"request_id":"2bb6178dfea3"}） |
| truncated_png | 截断 png → 结构化错误 | ⛔ FAIL | upload HTTP 200；vision 200，检出 0 框 |
| empty_txt | 0 字节 txt | ⛔ FAIL | upload HTTP 200；parse HTTP 200，content 0 字 |
| oversize | 51MB 超 50MB 上限 → 明确拒绝 | ✅ PASS | upload HTTP 400（{"error_code":"HTTP_400","message":"文件过大，最大支持 50MB","detail":{},"request_id":"4fa0775670e2"}） |

## 逐格明细

### txt

| 格子 | G1-G4 | 状态 | 耗时 |
|---|---|---|---|
| mask | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 10.33s |
| pseudonym | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 10.94s |

### docx

| 格子 | G1-G4 | 状态 | 耗时 |
|---|---|---|---|
| mask | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 10.43s |
| pseudonym | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 11.83s |

### text_pdf

| 格子 | G1-G4 | 状态 | 耗时 |
|---|---|---|---|
| mask | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 20.09s |
| pseudonym | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 14.98s |

### scanned_pdf

| 格子 | G1-G4 | 状态 | 耗时 |
|---|---|---|---|
| mask | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 15.71s |

### md

| 格子 | G1-G4 | 状态 | 耗时 |
|---|---|---|---|
| mask | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 10.68s |
| pseudonym | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 10.42s |

### html

| 格子 | G1-G4 | 状态 | 耗时 |
|---|---|---|---|
| mask | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 11.19s |
| pseudonym | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 10.6s |

### htm

| 格子 | G1-G4 | 状态 | 耗时 |
|---|---|---|---|
| mask | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 11.27s |
| pseudonym | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 10.63s |

### rtf

| 格子 | G1-G4 | 状态 | 耗时 |
|---|---|---|---|
| mask | g1 PASS ｜ g2 PASS ｜ g3 0% FAIL ｜ g4 FAIL | FAIL | 4.94s |
| pseudonym | g1 PASS ｜ g2 PASS ｜ g3 0% FAIL ｜ g4 FAIL | FAIL | 5.18s |

### doc

| 格子 | G1-G4 | 状态 | 耗时 |
|---|---|---|---|
| mask | g1 PASS ｜ g2 FAIL ｜ g3 — ｜ g4 — | FAIL | 0.15s |
| pseudonym | — | SKIP（同格式打码格 G1/G2 FAIL，上游不可用） | 0.0s |

### jpg

| 格子 | G1-G4 | 状态 | 耗时 |
|---|---|---|---|
| mask | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 16.02s |

### jpeg

| 格子 | G1-G4 | 状态 | 耗时 |
|---|---|---|---|
| mask | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 15.3s |

### png

| 格子 | G1-G4 | 状态 | 耗时 |
|---|---|---|---|
| mask | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 14.36s |

### bmp

| 格子 | G1-G4 | 状态 | 耗时 |
|---|---|---|---|
| mask | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 14.2s |

### gif

| 格子 | G1-G4 | 状态 | 耗时 |
|---|---|---|---|
| mask | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 13.92s |

### webp

| 格子 | G1-G4 | 状态 | 耗时 |
|---|---|---|---|
| mask | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 18.9s |

### tif

| 格子 | G1-G4 | 状态 | 耗时 |
|---|---|---|---|
| mask | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 14.43s |

### tiff

| 格子 | G1-G4 | 状态 | 耗时 |
|---|---|---|---|
| mask | g1 PASS ｜ g2 PASS ｜ g3 100% PASS ｜ g4 PASS | PASS | 14.09s |

---
*由 `eval/scripts/run_format_matrix.py` 生成于 2026-09-15 17:39；样张全合成，实例任务文件已清理。*
