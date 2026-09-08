# Issue #21 设计 — 单次处理:处理方式选择 + 化名映射确认 + 对照表

> 需求确认结论见 Issue #21 正文(2026-09-08 与用户定稿)。本文档为 ① 设计,含技术方案与验收标准。

## 1. 现状与差距

- Playground 文本模式识别后,实体面板有 4 选 1 模式栅格(结构化/智能/直接掩码/化名替换),默认 structured,**没有"打码 vs 替换"的上层心智区分**;化名模式下用户看不到也改不了映射。
- 执行链路 `use-playground.ts handleRedact` → `POST /redaction/execute`,`config.custom_replacements` 现传空;后端引擎已支持 custom_replacements(原文→替换词,优先级最高)。
- `/redaction/preview-map`(POST,带 auth)已在批量复核用,返回 `{entity_map: {原文: 替换词}}`。

## 2. 技术方案

### 2.1 状态(use-playground-recognition.ts)

- 新增 `processingMode: 'mask' | 'replace'`,默认 `'mask'`;
- `setReplacementMode` 包装:传入 `'pseudonym'` → 置 `processingMode='replace'`(预设应用含 pseudonym 时自动落到替换分支);传其余三值 → `processingMode='mask'` + 原行为;
- 面板不再直接展示 4 选 1;`replacementMode` 保留三值作为打码子模式。

### 2.2 化名映射(use-playground.ts,entities 可及)

- state:`pseudonymMap: Record<原文, 化名>`、`pseudonymMapLoading`;
- 自动补全:processingMode='replace' 且文本模式时,对 selected 实体去重后的原文集合,若存在 map 中缺失的 key → 调 `/redaction/preview-map`(全量 selected 实体,`replacement_mode:'pseudonym'`)→ **只合并缺失 key,不覆盖已编辑行**;
- `setPseudonymReplacement(text, value)` 单行编辑;
- 冲突检测(derived):不同原文映射到同一非空化名 → 面板显示警告;
- `handleRedact`:replace 模式时 `replacement_mode='pseudonym'`,`custom_replacements`= map 中属于 selected 原文的子集;记录执行时快照供对照表使用;mask 模式完全走原逻辑(回归零改动);
- `handleDownloadPseudonymCsv`:前端生成 csv(原文,类型,化名,出现次数),`\uFEFF` BOM + utf-8 保证 Excel 中文正常,blob 下载;仅 replace 模式执行成功后展示入口。

### 2.3 UI(playground-entity-panel.tsx / playground-result.tsx)

- 实体面板(文本模式)顶部:「处理方式」二选一 Button 组(打码=默认 / 替换(化名)),`data-testid="playground-processing-mode-{mask|replace}"`;
- mask 分支:原模式栅格去掉 pseudonym 项(3 选);
- replace 分支:**化名映射小节**——每行 `原文 | 类型徽章 | 化名 Input`;顶部小字说明"确认后执行替换,可逐行调整";冲突行高亮警告;加载中显示 skeleton;
- 样例预览:`getModePreview` 的 pseudonym 分支改用实际映射(`原文 -> 化名`),签名加可选 map 参数;
- 结果页:replace 执行后「下载化名对照表」按钮(与现有下载并列)。

### 2.4 不改动

- 后端零改动(preview-map / execute / custom_replacements 均现成);
- 图像模式(isImageMode)不出现处理方式选择(图像走打码,替换式后续按需);
- 批量向导本轮不动。

## 3. 验收标准(DoD)

1. **自动化**:tsc/build、vitest 29+、eslint 0 error;`getModePreview` 映射预览与冲突检测补单测(纯函数部分);
2. **云实例实测**(scnet-dcu,重建前端后):
   - 识别完成 → 「打码/替换」选择出现,默认「打码」,执行行为与现状一致(回归);
   - 切「替换」→ 映射小节出现,默认化名来自词池,证件号为格式合法虚构号;
   - 编辑某行化名 → 样例预览同步;新勾选实体自动补默认化名且不覆盖已编辑行;
   - 构造两行同化名 → 冲突警告出现;
   - 执行 → 下载成品,该实体按编辑后化名替换,其余按默认;对照表 csv Excel 打开中文正常、内容与成品一致;
   - 切回「打码」→ 与现有打码行为完全一致;
3. **用户手动验收**:《手动验收-T1-单次处理化名映射.md》(工作区,不入库),用户通过并放行后才 merge。

## 4. 风险

- preview-map 在切换/勾选频繁时的请求节流:仅对缺失 key 触发,entities 引用变更才重算(derived),无轮询;
- 大实体量(数百行)渲染:映射小节放 ScrollArea,行高紧凑;
- 已合并的 4 选 1 UI 被替换:`replacementMode` 仍含 pseudonym(类型层不动),仅展示层重组,批量向导不受影响。
