> [!tip] 相关文档
> Issue：[#46](https://github.com/youayou-Lee/DataInfra-RedactionEverything/issues/46) ｜ 用户手册：工作区 `docs/用户使用说明-preview2.0.0.md`（第 2 节格式表待本 Issue 结论回写）｜ 复用设施：[[issue-37-eval-benchmark]] 的 eval/scripts 与生成器

# Issue #46：上传格式支持面端到端实测（格式 × 处理方式矩阵）

- 日期：2026-09-15　级别：P1（上线对外承诺的依据）　状态：设计定稿（用户 2026-09-15 批准）　工作量：M（1-2d）
- 目标读者：实现者 + 测试执行者；结论读者：产品/用户手册

## 0. 已确认决策（⓪ 道门产出，2026-09-15 与用户确认）

| 决策点 | 结论 |
|---|---|
| 执行环境 | **直打 scnet-main 实例 API**（preview2.0.0 生产构建，保真最高、零部署）；测试账号 apitester；**测试文件与任务用完即删** |
| 上线期门控 | **先不做前端门控**，手册口径约束（「未经完整测试勿用于正式业务」）；是否收窄 accept 列表待测试结论后定 |
| 样张来源 | **全合成虚构数据**，随仓库入库（沿用 #37 合成数据惯例，无敏感信息） |
| 执行节奏 | 实现+本地单测 → **P1 冒烟 8 格**（约 10 分钟，报用户）→ 用户点头 → **全量约 30 格**（约 40 分钟） |
| 缺陷处置 | 测试中发现的缺陷（如 .doc 兜底文案 UX）**转后续修复 Issue，不阻塞矩阵出结论** |

## 1. 背景与目标

前端 `ACCEPTED_UPLOAD_FILE_TYPES`（frontend/src/utils/fileUploadAccept.ts）放行 16 种扩展名，但其中仅 4 类（.txt / .docx / 文本型 PDF / 扫描型 PDF）经 T1 验收与 #37 e2e 覆盖。用户手册第一版只承诺这 4 类，其余格式的真实可用性未知。

本 Issue 对全部放行格式做端到端实测，产出：

1. **测试矩阵报告**（每格 PASS/FAIL + 证据），入库 `eval/reports/`；
2. **每格式三档结论**：承诺支持 / 实验性 / 前端禁用；
3. **用户手册第 2 节格式表回写**（结论经用户终审后）。

## 2. 测试矩阵

维度：格式 × 处理方式。文本类跑「打码（结构化标签）+ 替换（化名）」双格；图片类仅打码（替换对图片未上线，属产品边界而非缺陷，不进矩阵）。

| 组 | 格式 | 打码 | 化名 | 备注 |
|---|---|---|---|---|
| 基线（回归对照） | .txt | ✅ | ✅ | 基线 |
| 基线 | .docx | ✅ | ✅ | 基线 |
| 基线 | .pdf（文本型） | ✅ | ✅ | 基线 |
| 基线 | .pdf（扫描型） | ✅ | — | 仅打码（产品边界） |
| 文本类待测 | .md | ✅ | ✅ | 与 txt 同解析路径，预期低风险 |
| 文本类待测 | .html / .htm | ✅ | ✅ | 验证标签剥离 |
| 文本类待测 | .rtf | ✅ | ✅ | **P1 风险**：后端 RTF 处理朴素，`\uN` CJK 转义解析存疑 |
| 文本类待测 | .doc | ✅ | ✅ | **P1 风险**：依赖实例安装 LibreOffice（后端硬编码 `/usr/bin/soffice`、`/usr/bin/libreoffice`） |
| 图片类待测 | .jpg / .jpeg | ✅ | — | 视觉链路单文件形态首次正式验证 |
| 图片类待测 | .png | ✅ | — | 同上 |
| 图片类待测 | .bmp / .gif / .webp / .tif / .tiff | ✅ | — | **P2 风险**：解码器兼容性；单帧基线（多帧出圈） |

合计：文本类 17 格 + 图片类 8 格 = **25 格**；另有**异常路径 5 例**（横切，不按格式计）：

1. 损坏 docx（截断文件）；
2. 伪造扩展名（txt 内容改名 .pdf）；
3. 截断图片（损坏 png）；
4. 超 50MB 文件（预期客户端/服务端明确拒绝）；
5. 空文件（0 字节 txt）。

异常路径判定标准：拿到**结构化错误信息**（HTTP 4xx 或前端可见提示）即 PASS；500 崩溃、白屏、静默产出空成品均 FAIL。

## 3. 样张与 GT（全合成，入库）

统一 payload（沿用 #37 生成器的虚构数据风格）：**2 个人名 + 1 个身份证号 + 1 个手机号 + 1 个地址**，与 GT（`.gt.json`，实体原文/类型/span）一一对应，文件放 `eval/datasets/formats/`。

| 格式 | 生成方式 | 要点 |
|---|---|---|
| .txt / .md | 既有 gen_text 变体 | md 加标题/列表语法，验证语法字符不干扰识别 |
| .html / .htm | gen_text 包 HTML 骨架 | 含 `<title>`、`<p>` 标签 |
| .rtf | 手写 RTF 头 + `\uN` CJK 转义 | CJK 必须 `\'xx` GBK 或 `\uc1\uNNNN` 形式，这正是后端解析的风险点 |
| .doc | 本机 snap LibreOffice（`/snap/bin/libreoffice`）从 docx 一次性转出 | 转换脚本入库，产物（二进制）入库 |
| .jpg / .png / .bmp / .gif / .webp / .tif / .tiff | PIL 渲染文字图 | 系统 CJK 字体（Noto Sans CJK），字号 ≥28px 保证 OCR 可读——渲染质量本身不构成判 FAIL 的理由，检不出才是结论 |
| .docx / 两类 PDF | 既有 gen_docx / gen_pdf | 基线回归 |

GT 判定用原文精确匹配（同 #37 口径）；payload 全虚构，`.gt.json` 与样张一并入库。

## 4. 判定关卡（机械化，纯函数可单测）

每格四关，全部 PASS 该格才算 PASS：

| 关卡 | 判定 |
|---|---|
| G1 上传受理 | HTTP 2xx 且返回 file_id |
| G2 解析 | parse content 非空，且不含「无法解析」兜底文案（.doc 的兜底文案 = 转换链不可用，记 FAIL 并注明原因） |
| G3 识别 | GT 实体级召回 ≥ 80%，且身份证号/手机号逐字 exact 检出（数字保真镜像 #23 一票否决口径） |
| G4 成品 | 下载 2xx；`leak_check.run_rescan` 复扫成品**零原文残留**；载体完整性：docx 可被 python-docx 打开、PDF 用 fitz 打开且页数一致、图片用 PIL 打开且尺寸一致；化名格加验：对照表 CSV 四列与成品替换一致（复用 build_pseudonym_set 自证逻辑） |

执行规则：同一格式打码格 G1/G2 FAIL 时，化名格**标 SKIP**（上游不可用，不重跑、不计 FAIL）；异常路径例独立判定，不参与三档聚合。

三档结论规则（按格式聚合其所有格子）：

| 结论 | 条件 |
|---|---|
| **承诺支持** | 该格式全部格子 G1-G4 PASS |
| **实验性** | 打码格 PASS，但化名格 FAIL，或 G3 召回低于门槛但无残留（手册标注降级用法，如「仅打码」） |
| **前端禁用** | 上传即 5xx / 解析崩溃 / 成品损坏 / 复扫见原文残留 / 解析兜底不可用（该格式从 accept 列表移除的候选） |

## 5. 实现

分支 `feat/issue46-format-matrix`（基于 origin/preview，f00dbdb）。改动面（全部在 eval/ 内，不碰产品代码）：

1. `eval/datasets/generators/`：新增 `gen_image.py`（PIL 渲染）、`gen_rtf.py`、`gen_doc.sh`（libreoffice 转换），gen_text 扩展 md/html 变体；`build_all.py` 注册新格式；
2. `eval/datasets/formats/`：样张 + GT 入库；
3. `eval/scripts/run_format_matrix.py`：编排（构造格子清单 → 逐格执行 G1-G4 → 聚合三档结论 → 报告），复用 `common_api.EvalApi` 与 `leak_check`；
4. 判定逻辑抽纯函数（`gates` 模块），表驱动单测（不打网）；
5. 报告：`eval/reports/<ts>-<env>-format-matrix.{json,md}`，md 版式沿用 #37 的 Obsidian 风格（结论速览表 + 每格式小节 + 失败明细），但**不做**指标字典/SLO 预算等重量级结构（本矩阵只产 PASS/FAIL 与召回数值）；
6. 单测：生成器 golden 测试（产物非空/格式魔数正确）+ 判定逻辑表驱动 + 报告渲染快照。

## 6. 执行 runbook（scnet-main）

```bash
# 0. 环境预检：确认实例部署版本与 preview2.0.0 一致（grep dist 文案 / 版本接口）；
#    确认实例是否安装 /usr/bin/soffice（.doc 结论的关键输入，缺=记入报告）
# 1. P1 冒烟（6 格 + 2 异常例：doc/rtf 打码+化名、jpg/png 打码、伪造扩展名、超 50MB）
python eval/scripts/run_format_matrix.py --base-url <实例地址> --smoke
# 2. 全量（25 格 + 5 异常例，用户点头后）
python eval/scripts/run_format_matrix.py --base-url <实例地址> --suite full
# 3. 清理：删除本次创建的全部 file/job（脚本 --cleanup 自动做，或按 file_id 清单手动）
```

纪律：P1 冒烟结果报用户后再跑全量；测试文件/任务用完即删；样张为合成数据，遗留无敏感风险但仍清理。

## 7. 验收标准（DoD）

- [ ] 矩阵报告入库 `eval/reports/`（25 格 + 5 异常例，每格 PASS/FAIL + 证据/数值）；
- [ ] 每格式三档结论之一，规则可追溯（报告附每格关卡明细）；
- [ ] 新增单测绿、既有 eval 单测零回归、CI 三检查通过；
- [ ] 用户终审结论 → 工作区用户手册第 2 节回写 → 关闭 Issue #46；
- [ ] 发现类缺陷逐条转后续 Issue（在 #46 收尾评论中列出清单）。

## 8. 边界（本 Issue 不做）

- 前端/后端门控改动（accept 列表收窄与否，结论出来后另立 PR）；
- 性能/吞吐测量（只验正确性）；
- GUI 自动化（三类承诺格式的 GUI 用户已在 T1 验收亲手覆盖）；
- 多帧 GIF/TIFF、加密 PDF 等边界变体（加密 PDF 问题已在 #37 真实基线记录，另行处理）；
- 真实案卷样张（合成样张足以回答「格式能否走通」）。

## 9. 预期与风险

- **.doc**：实例若未装 LibreOffice → G2 FAIL（兜底文案）→ 「前端禁用」候选 + 转修复 Issue（兜底 UX 应改为明确拒绝而非产出兜底文本成品）；
- **.rtf**：朴素解析可能毁 `\uN` 转义 → 识别召回崩 → 「实验性/禁用」；
- **图片类**：视觉链路在单文件形态的行为（框选/打码导出）首次系统验证，可能暴露 UX/链路缺陷——同样转修复 Issue，不阻塞结论；
- 其余文本类预期 PASS。
