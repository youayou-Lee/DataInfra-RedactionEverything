# Issue #33 单次处理会话保持与恢复 — 实施计划

> 需求与决策定稿：2026-09-12，对应 Issue #33（fork 编号）。基线裁定：基于 T1（PR #22）head 分支叠层开发；T1 合入 preview 后本分支 rebase 至最新 preview 并重跑门禁。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 单次处理（/single Playground）的识别结果与手工编辑不再因切页或刷新丢失，且历史页可一键回到处理现场。

**Architecture:** 三层递进——P0 把 `PlaygroundProvider` 提升到 Layout 层实现 SPA 会话保持；P1 以 localStorage「会话草稿」（file_id + 实体/框/映射等轻量状态 + 文本内容，2MB 上限）实现刷新后完整恢复；P2 在历史页 playground 行加「回到处理现场」入口，草稿命中走秒级恢复（R1），无草稿走服务端 parse+重新识别（R2）。后端零改动。

**Tech Stack:** React 19 + react-router v7（createBrowserRouter）、zustand 风格 hooks、vitest（node 环境、纯逻辑测试）、localStorage（`@/lib/storage` 的 scoped 封装）。

**Spec:** 本文件即设计+计划（用户 2026-09-12 授权按推荐决策定稿）；定稿后随 Task 1 以 `docs/issue-33-playground-session-restore.md` 入库。

## 已定决策（用户授权，按推荐执行）

1. 范围：P0+P1+P2 全做。
2. 未执行的手工编辑（勾选、手动实体、化名映射草稿）刷新后也要恢复 → 草稿快照包含 entities/boundingBoxes/pseudonymMap 等。
3. 历史页入口只保留一个「回到处理现场」：草稿命中即秒恢复；未命中自动降级为「parse + 按当前配置重新识别」（等价于兜底的重新处理，不设第二个按钮）。
4. 识别完成但未执行脱敏的 playground 文件**已经**以「未脱敏」状态出现在历史页（`getHistoryDeliveryState` 的 unredacted 分支），无需后端改动，只需加入口。

## Global Constraints

- **开工前置：PR #22（T1）必须已合入 preview**。本计划基于 preview + T1 代码编写（T1 给 playground 增加了 `processingMode`/`pseudonymMap`/`confirmedPseudonymMap` 等状态）。开工分支 `feat/issue33-playground-session-restore`，基于最新 preview 创建。
- gh 操作一律显式 `--repo youayou-Lee/DataInfra-RedactionEverything`，绝不动上游 ttttccxxui 仓库。
- 本仓库多文件为 CRLF 行尾：**禁止用脚本整文件重写**（会把 CRLF 转 LF 产生上千行伪 diff），一律用精准 Edit；若意外产生行尾漂移，用 `git checkout <commit> -- <file>` 重建。
- 前端测试为 **node 环境纯逻辑测试**（无 jsdom/@testing-library）：只测导出的纯函数（仓库既有模式，参考 `frontend/src/features/playground/utils.pseudonym.test.ts`）。组件/路由行为靠云实例 GUI 验证（Task 8），验证时用整个 `frontend/src/` rsync。
- 敏感数据不入库：手动验收文档放工作区 `docs/acceptance/`（不提交）；草稿快照只存当前用户浏览器 localStorage（按用户名 scoping），内网自部署可接受，作为已知限制记录。
- i18n：新增文案必须同时加 `frontend/src/i18n/zh.ts` 与 `frontend/src/i18n/en.ts`。
- 本计划**无后端改动**；所有依赖端点已存在：`GET /files/{id}`、`GET /files/{id}/parse`、`POST /files/{id}/ner/hybrid`、`GET /redaction/{id}/report`、`GET /redaction/{id}/versions`。
- 七道门流程：本计划通过用户审查（门⓪）后，仍需用户明确确认才开工；实现走测试前置（门②）→ 独立 AI review（门③）→ 云实例验证 → 用户手动验收（门⑥）→ merge 前必须 rebase 最新 preview 并重跑 pytest/前端 build+test（门⑦）。

## 已知限制（记录到 Issue #33 定稿评论）

- 多标签页同时开单文件处理：草稿 last-writer-wins，不做跨标签同步。
- 撤销/重做栈（entityHistory/imageHistory）不随草稿恢复，恢复后从当前态重新计栈。
- 草稿为「单会话」语义：只保留最近一次单文件处理现场，处理新文件即覆盖。
- 草稿含原文与实体文本，驻留本机 localStorage（按用户隔离、重置/切换时清除）。

## File Structure

```
frontend/src/features/playground/
  lib/playground-draft.ts            # 新建：草稿快照纯函数（类型、序列化、校验、恢复决策）
  lib/playground-draft.test.ts       # 新建：纯逻辑单测
  hooks/use-playground.ts            # 修改：草稿写入/清除、挂载恢复、resumeFromFile
  hooks/use-playground-file.ts       # 修改：新增 loadExistingFile（无上传的服务端重建）
  playground-page.tsx                # 修改：去掉 Provider 包装；加 ?file_id 恢复入口
frontend/src/components/Layout/index.tsx   # 修改：挂载 PlaygroundProvider（P0）
frontend/src/constants/storage-keys.ts     # 修改：加 PLAYGROUND_DRAFT
frontend/src/utils/playgroundResume.ts     # 新建：历史页「回到现场」动作决策纯函数
frontend/src/utils/playgroundResume.test.ts# 新建：单测
frontend/src/features/history/components/history-row.tsx  # 修改：接入入口
frontend/src/i18n/zh.ts | en.ts            # 修改：新增文案
docs/issue-33-playground-session-restore.md  # 新建（Task 1，由本文件定稿入库）
```

---

### Task 1: 分支准备与设计文档入库

**Files:**
- Create: `docs/issue-33-playground-session-restore.md`（内容 = 本计划文档定稿）

**Interfaces:**
- Produces: 分支 `feat/issue33-playground-session-restore`（后续所有任务的基线）；入库的设计文档路径。

- [ ] **Step 1: 确认前置条件**

```bash
gh pr view 22 --repo youayou-Lee/DataInfra-RedactionEverything --json state,baseRefName,mergeable
git -C /home/you/workspace/Desensitization/DataInfra-RedactionEverything fetch origin
```

Expected: PR #22 state=MERGED 且 baseRefName=preview。若未合入：**停止**，等 T1 验收合入后再开工。

- [ ] **Step 2: 基于最新 preview 建分支**

```bash
cd /home/you/workspace/Desensitization/DataInfra-RedactionEverything
git checkout preview && git pull origin preview
git checkout -b feat/issue33-playground-session-restore
git branch --show-current   # 必须输出 feat/issue33-playground-session-restore（共享仓库防误分支铁律）
```

- [ ] **Step 3: 设计文档入库**

把本计划文档（用户审查通过后的定稿版）复制为 `docs/issue-33-playground-session-restore.md`，并在文档头部补一行：`> 需求与决策定稿：2026-09-12，对应 Issue #33（fork 编号）`。

- [ ] **Step 4: Commit**

```bash
git add docs/issue-33-playground-session-restore.md
git commit -m "docs(issue33): 单次处理会话保持与恢复设计定稿"
```

---

### Task 2: 草稿快照纯函数库 + 单测

**Files:**
- Create: `frontend/src/features/playground/lib/playground-draft.ts`
- Test: `frontend/src/features/playground/lib/playground-draft.test.ts`
- Modify: `frontend/src/constants/storage-keys.ts`

**Interfaces:**
- Consumes: `frontend/src/features/playground/types.ts` 的 `Entity`/`BoundingBox`/`FileInfo`/`Stage`。
- Produces（后续任务按这些签名调用）:
  - `type PlaygroundDraftSnapshot`（字段见实现）
  - `buildDraftSnapshot(input: DraftSnapshotInput): PlaygroundDraftSnapshot`
  - `serializeDraft(snapshot): string | null`（超 2MB 返回 null）
  - `parseDraft(raw: string | null | undefined): PlaygroundDraftSnapshot | null`（版本/结构校验）
  - `planResume({targetFileId, snapshot}): {mode:'draft',snapshot} | {mode:'rerun',fileId} | {mode:'unavailable'}`
  - `needsSwitchConfirm(currentFileId: string | null, targetFileId: string): boolean`
  - 常量 `PLAYGROUND_DRAFT_VERSION = 1`、`PLAYGROUND_DRAFT_MAX_JSON_LENGTH = 2_000_000`

- [ ] **Step 1: 注册 storage key**

`frontend/src/constants/storage-keys.ts` 的 `STORAGE_KEYS` 对象中、`BATCH_WIZ_FURTHEST_PREFIX` 之前加一行：

```ts
  PLAYGROUND_DRAFT: 'datainfraRedaction:playgroundDraft',
```

- [ ] **Step 2: 写失败测试**

创建 `frontend/src/features/playground/lib/playground-draft.test.ts`：

```ts
import { describe, expect, it } from 'vitest';
import {
  PLAYGROUND_DRAFT_MAX_JSON_LENGTH,
  PLAYGROUND_DRAFT_VERSION,
  buildDraftSnapshot,
  needsSwitchConfirm,
  parseDraft,
  planResume,
  serializeDraft,
} from './playground-draft';
import type { DraftSnapshotInput } from './playground-draft';

const baseInput: DraftSnapshotInput = {
  stage: 'preview',
  fileInfo: { file_id: 'f1', filename: 'a.pdf', file_size: 123, file_type: 'pdf', is_scanned: false, page_count: 2, pages: ['张三于2024年借款。', '第二页'] },
  content: '张三于2024年借款。第二页',
  entities: [
    { id: 'e1', text: '张三', type: 'person', start: 0, end: 2, selected: true, source: 'llm' },
    { id: 'e2', text: '李四', type: 'person', start: 10, end: 12, selected: false, source: 'manual' },
  ],
  boundingBoxes: [],
  processingMode: 'replace',
  replacementMode: 'structured',
  watermarkText: '',
  pseudonymMap: { 张三: '化名一' },
  confirmedPseudonymMap: null,
  entityMap: {},
  redactedCount: 0,
  currentPage: 1,
};

describe('buildDraftSnapshot + serializeDraft + parseDraft', () => {
  it('roundtrip：序列化后解析得到等价快照', () => {
    const snapshot = buildDraftSnapshot(baseInput);
    expect(snapshot.version).toBe(PLAYGROUND_DRAFT_VERSION);
    const parsed = parseDraft(serializeDraft(snapshot));
    expect(parsed).toEqual(snapshot);
  });

  it('版本不匹配的快照被拒绝', () => {
    const snapshot = buildDraftSnapshot(baseInput);
    const raw = JSON.stringify({ ...snapshot, version: PLAYGROUND_DRAFT_VERSION + 1 });
    expect(parseDraft(raw)).toBeNull();
  });

  it('损坏的 JSON 被拒绝', () => {
    expect(parseDraft('not-json{')).toBeNull();
    expect(parseDraft(null)).toBeNull();
    expect(parseDraft(undefined)).toBeNull();
  });

  it('缺关键字段（file_id/entities/boundingBoxes）被拒绝', () => {
    const snapshot = buildDraftSnapshot(baseInput);
    expect(parseDraft(JSON.stringify({ ...snapshot, fileInfo: { filename: 'x' } }))).toBeNull();
    expect(parseDraft(JSON.stringify({ ...snapshot, entities: 'no' }))).toBeNull();
    expect(parseDraft(JSON.stringify({ ...snapshot, boundingBoxes: undefined }))).toBeNull();
    expect(parseDraft(JSON.stringify({ ...snapshot, stage: 'upload' }))).toBeNull();
  });

  it('文本型 preview 快照必须有非空 content；扫描件/图片/result 不要求', () => {
    const snapshot = buildDraftSnapshot(baseInput);
    expect(parseDraft(JSON.stringify({ ...snapshot, content: '' }))).toBeNull();

    const scanned = buildDraftSnapshot({ ...baseInput, fileInfo: { ...baseInput.fileInfo, is_scanned: true }, content: '' });
    expect(parseDraft(JSON.stringify(scanned))).not.toBeNull();

    const result = buildDraftSnapshot({ ...baseInput, stage: 'result', content: '' });
    expect(parseDraft(JSON.stringify(result))).not.toBeNull();
  });

  it('超过大小上限时 serializeDraft 返回 null（放弃持久化）', () => {
    const huge = buildDraftSnapshot({ ...baseInput, content: 'x'.repeat(PLAYGROUND_DRAFT_MAX_JSON_LENGTH) });
    expect(serializeDraft(huge)).toBeNull();
  });
});

describe('planResume', () => {
  it('草稿命中：file_id 一致 → draft', () => {
    const snapshot = buildDraftSnapshot(baseInput);
    expect(planResume({ targetFileId: 'f1', snapshot })).toEqual({ mode: 'draft', snapshot });
  });

  it('草稿属于其他文件或缺失 → rerun', () => {
    const snapshot = buildDraftSnapshot(baseInput);
    expect(planResume({ targetFileId: 'f2', snapshot })).toEqual({ mode: 'rerun', fileId: 'f2' });
    expect(planResume({ targetFileId: 'f2', snapshot: null })).toEqual({ mode: 'rerun', fileId: 'f2' });
  });

  it('空 file_id → unavailable', () => {
    expect(planResume({ targetFileId: '', snapshot: null })).toEqual({ mode: 'unavailable' });
  });
});

describe('needsSwitchConfirm', () => {
  it('无当前会话或同文件 → 不确认', () => {
    expect(needsSwitchConfirm(null, 'f1')).toBe(false);
    expect(needsSwitchConfirm('f1', 'f1')).toBe(false);
  });
  it('不同文件 → 确认', () => {
    expect(needsSwitchConfirm('f1', 'f2')).toBe(true);
  });
});
```

- [ ] **Step 3: 跑测试确认失败**

```bash
cd frontend && npx vitest run src/features/playground/lib/playground-draft.test.ts
```

Expected: FAIL（模块不存在）。

- [ ] **Step 4: 实现 playground-draft.ts**

创建 `frontend/src/features/playground/lib/playground-draft.ts`：

```ts
// Copyright 2026 DataInfra-RedactionEverything Contributors

import type { BoundingBox, Entity, FileInfo, Stage } from '../types';

/** 草稿结构版本：字段变更时 +1，旧草稿解析即废弃（走重跑路径） */
export const PLAYGROUND_DRAFT_VERSION = 1;
/** 序列化后超过该长度放弃持久化（localStorage 容量保护），会话仅在内存中可用 */
export const PLAYGROUND_DRAFT_MAX_JSON_LENGTH = 2_000_000;

export type DraftSnapshotInput = {
  stage: Stage;
  fileInfo: FileInfo;
  content: string;
  entities: Entity[];
  boundingBoxes: BoundingBox[];
  // 与 use-playground-recognition.ts 中对应 state 的联合类型保持一致（T1 引入 processingMode）
  processingMode: 'mask' | 'replace';
  replacementMode: 'structured' | 'smart' | 'mask' | 'pseudonym';
  watermarkText: string;
  pseudonymMap: Record<string, string>;
  confirmedPseudonymMap: Record<string, string> | null;
  entityMap: Record<string, string>;
  redactedCount: number;
  currentPage: number;
};

export type PlaygroundDraftSnapshot = DraftSnapshotInput & {
  version: number;
  savedAt: string;
};

export function buildDraftSnapshot(input: DraftSnapshotInput): PlaygroundDraftSnapshot {
  return { version: PLAYGROUND_DRAFT_VERSION, savedAt: new Date().toISOString(), ...input };
}

export function serializeDraft(snapshot: PlaygroundDraftSnapshot): string | null {
  try {
    const json = JSON.stringify(snapshot);
    if (json.length > PLAYGROUND_DRAFT_MAX_JSON_LENGTH) return null;
    return json;
  } catch {
    return null;
  }
}

export function parseDraft(raw: string | null | undefined): PlaygroundDraftSnapshot | null {
  if (!raw) return null;
  let value: PlaygroundDraftSnapshot;
  try {
    value = JSON.parse(raw) as PlaygroundDraftSnapshot;
  } catch {
    return null;
  }
  if (!value || typeof value !== 'object' || value.version !== PLAYGROUND_DRAFT_VERSION) return null;
  if (!value.fileInfo || typeof value.fileInfo.file_id !== 'string') return null;
  if (!Array.isArray(value.entities) || !Array.isArray(value.boundingBoxes)) return null;
  if (typeof value.content !== 'string') return null;
  if (value.stage !== 'preview' && value.stage !== 'result') return null;
  // 文本型 preview 却没有正文 → 草稿不完整，宁可重跑也不要白屏
  const isImageMode = value.fileInfo.file_type === 'image' || Boolean(value.fileInfo.is_scanned);
  if (value.stage === 'preview' && !isImageMode && value.content.length === 0) return null;
  return value;
}

export type ResumeDecision =
  | { mode: 'draft'; snapshot: PlaygroundDraftSnapshot }
  | { mode: 'rerun'; fileId: string }
  | { mode: 'unavailable' };

export function planResume(input: { targetFileId: string; snapshot: PlaygroundDraftSnapshot | null }): ResumeDecision {
  const { targetFileId, snapshot } = input;
  if (!targetFileId) return { mode: 'unavailable' };
  if (snapshot && snapshot.fileInfo.file_id === targetFileId) return { mode: 'draft', snapshot };
  return { mode: 'rerun', fileId: targetFileId };
}

export function needsSwitchConfirm(currentFileId: string | null, targetFileId: string): boolean {
  return Boolean(currentFileId) && currentFileId !== targetFileId;
}
```

注意：实现前先打开 `use-playground-recognition.ts`（合入 T1 后的版本）核对 `processingMode`/`replacementMode` 的实际联合类型字面量，与 `DraftSnapshotInput` 保持一字不差（类型不一致会导致 tsc 失败，这是有意的防漂移锚点）。

- [ ] **Step 5: 跑测试确认通过 + 类型检查**

```bash
cd frontend && npx vitest run src/features/playground/lib/playground-draft.test.ts && npx tsc --noEmit -p tsconfig.json
```

Expected: 测试 PASS，tsc 无错误。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/constants/storage-keys.ts frontend/src/features/playground/lib/playground-draft.ts frontend/src/features/playground/lib/playground-draft.test.ts
git commit -m "feat(issue33): playground 会话草稿快照纯函数库"
```

---

### Task 3: P0 — PlaygroundProvider 提升到 Layout 层

**Files:**
- Modify: `frontend/src/features/playground/playground-page.tsx:410-418`（去掉 Provider 包装）
- Modify: `frontend/src/components/Layout/index.tsx`（挂载 Provider）

**Interfaces:**
- Consumes: `PlaygroundProvider`（`frontend/src/features/playground/playground-context.tsx`，T1 合入后含 pseudonym 相关字段）。
- Produces: 全 Layout 路由（登录后所有页面）可消费 playground context；`Playground` 页面组件不再创建 Provider。

- [ ] **Step 1: 页面组件去掉 Provider 包装**

`playground-page.tsx` 文件末尾，把：

```tsx
export const Playground: FC = () => (
  <PlaygroundProvider>
    <PlaygroundInner />
  </PlaygroundProvider>
);
```

改为：

```tsx
/** Playground 页面——Provider 已提升到 Layout 层，切页不再丢失会话状态。 */
export const Playground: FC = () => <PlaygroundInner />;
```

同时更新文件顶部 import：保留 `usePlaygroundContext`/`usePlaygroundUIContext`，`PlaygroundProvider` 若不再被本文件引用则从 import 中移除（tsc 会提示 unused）。

- [ ] **Step 2: Layout 挂载 Provider**

先读 `frontend/src/components/Layout/index.tsx` 确认当前 JSX 骨架（SidebarProvider → OfflineBanner / AppSidebar / SidebarInset(AppHeader, `<main key={location.pathname}><Outlet/></main>`) / ToastContainer / OnboardingGuide）。在 `SidebarProvider` 内、所有现有子元素外层包一层 `PlaygroundProvider`：

```tsx
import { PlaygroundProvider } from '@/features/playground/playground-context';

// JSX：
<SidebarProvider>
  <PlaygroundProvider>
    <OfflineBanner />
    <AppSidebar />
    <SidebarInset>
      <AppHeader />
      <main key={location.pathname}>
        <Outlet />
      </main>
    </SidebarInset>
    <ToastContainer />
    <OnboardingGuide />
  </PlaygroundProvider>
</SidebarProvider>
```

要点（写进 commit message 或代码注释）：Provider 必须在 `<main key>` **之外**，否则路由切换仍会重建。导入方向 components/Layout → features/playground 无循环依赖（playground 不反向 import Layout，实现后用 `npx madge --circular src/components/Layout/index.tsx` 或 tsc 通过佐证）。

- [ ] **Step 3: 验证**

```bash
cd frontend && npx tsc --noEmit -p tsconfig.json && npm run test && npm run build
```

Expected: 全部通过。行为变化需知（记录到 commit message）：① 识别目录/预设查询提前到进入任一登录页时发生（有模块级缓存，幂等）；② 健康轮询单例提前启动（15s 一次、仅页面可见时请求）；③ 在设置页应用预设而 /single 有活动会话时，会按既有 `presetApplySeq` 机制在后台重跑 NER——这是期望行为（会话保持新鲜）。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/playground/playground-page.tsx frontend/src/components/Layout/index.tsx
git commit -m "feat(issue33): PlaygroundProvider 提升至 Layout，SPA 内切页不再丢失单文件处理现场"
```

---

### Task 4: 草稿写入与清除接线

**Files:**
- Modify: `frontend/src/features/playground/hooks/use-playground.ts`

**Interfaces:**
- Consumes: Task 2 的 `buildDraftSnapshot`/`serializeDraft`；`@/lib/storage` 的 `setScopedStorageItem`/`removeStorageItem`/`scopedStorageKey`；`STORAGE_KEYS.PLAYGROUND_DRAFT`。
- Produces: 会话状态变化自动落盘；`performReset` 清除草稿。无新导出。

- [ ] **Step 1: use-playground.ts 增加 import**

```ts
import { STORAGE_KEYS } from '@/constants/storage-keys';
import { removeStorageItem, scopedStorageKey, setScopedStorageItem } from '@/lib/storage';
import { buildDraftSnapshot, serializeDraft } from '../lib/playground-draft';
```

- [ ] **Step 2: 防抖写草稿 effect**

加在 `performReset` 定义之后（依赖列表里的每一项都是快照字段来源，缺一个就会存出过时草稿）：

```ts
// 会话草稿（Issue #33）：有活动文件时防抖落盘；显式重置时清除。
// 上传新文件后本 effect 随 fileInfo 变化自然覆盖旧草稿。
useEffect(() => {
  if (!fileCtx.fileInfo) return;
  const timer = setTimeout(() => {
    const snapshot = buildDraftSnapshot({
      stage: fileCtx.stage,
      fileInfo: fileCtx.fileInfo!,
      content: fileCtx.content,
      entities: entityCtx.entities,
      boundingBoxes: imageCtx.boundingBoxes,
      processingMode: recognition.processingMode,
      replacementMode: recognition.replacementMode,
      watermarkText: recognition.watermarkText,
      pseudonymMap,
      confirmedPseudonymMap,
      entityMap,
      redactedCount,
      currentPage: imageCtx.currentPage,
    });
    const json = serializeDraft(snapshot);
    if (json === null) return; // 超限：放弃持久化，内存会话不受影响
    setScopedStorageItem(STORAGE_KEYS.PLAYGROUND_DRAFT, json);
  }, 400);
  return () => clearTimeout(timer);
}, [
  fileCtx.fileInfo,
  fileCtx.stage,
  fileCtx.content,
  entityCtx.entities,
  imageCtx.boundingBoxes,
  imageCtx.currentPage,
  recognition.processingMode,
  recognition.replacementMode,
  recognition.watermarkText,
  pseudonymMap,
  confirmedPseudonymMap,
  entityMap,
  redactedCount,
]);
```

- [ ] **Step 3: performReset 清除草稿**

在 `performReset` 回调体末尾（`setVersionHistoryOpen(false);` 之后）追加：

```ts
  removeStorageItem(scopedStorageKey(STORAGE_KEYS.PLAYGROUND_DRAFT));
```

- [ ] **Step 4: 验证 + Commit**

```bash
cd frontend && npx tsc --noEmit -p tsconfig.json && npm run test && npm run build
git add frontend/src/features/playground/hooks/use-playground.ts
git commit -m "feat(issue33): 单文件会话状态防抖写入草稿，重置时清除"
```

Expected: 通过（写盘接线无法用 node 环境单测覆盖，行为在 Task 8 GUI 验证）。

---

### Task 5: R1 — 挂载时草稿恢复（刷新/重开浏览器）

**Files:**
- Modify: `frontend/src/features/playground/hooks/use-playground.ts`
- Modify: `frontend/src/i18n/zh.ts`、`frontend/src/i18n/en.ts`

**Interfaces:**
- Consumes: Task 2 的 `parseDraft`；Task 4 的存储接线。
- Produces: `applyDraftSnapshot(snapshot: PlaygroundDraftSnapshot): void`（use-playground 内部函数，Task 6 复用）；应用启动即恢复草稿到内存。

- [ ] **Step 1: 新增 i18n 文案**

`frontend/src/i18n/zh.ts`（playground.* 区域）与 `en.ts` 对应位置：

```ts
// zh.ts
'playground.restored': '已恢复上次单文件处理现场',
'playground.restoreFileGone': '原文件已不存在，无法恢复上次现场',
// en.ts
'playground.restored': 'Last single-file session restored',
'playground.restoreFileGone': 'Original file no longer exists; last session cannot be restored',
```

（Task 6 会再加 switchSession/restoreRerunning 组，本任务只加这两个。）

- [ ] **Step 2: 实现 applyDraftSnapshot + 挂载恢复 effect**

在 use-playground.ts 中（`performReset` 之后、`handleReset` 之前）：

```ts
// 把草稿快照整体恢复为当前会话（挂载恢复与历史页「回到现场」共用）。
// 恢复是幂等的：undo 栈重置、dialog 态一律回到关闭，epoch 前进使在途异步结果失效。
const applyDraftSnapshot = useCallback(
  (snapshot: PlaygroundDraftSnapshot) => {
    asyncResultEpochRef.current += 1;
    latestFileIdRef.current = snapshot.fileInfo.file_id;
    redactionAbortRef.current?.abort();
    redactionInFlightRef.current = false;
    fileCtx.setFileInfo(snapshot.fileInfo);
    fileCtx.setContent(snapshot.content);
    fileCtx.setStage(snapshot.stage);
    entityCtx.setEntities(snapshot.entities);
    entityCtx.entityHistory.reset();
    imageCtx.setBoundingBoxes(snapshot.boundingBoxes);
    imageCtx.imageHistory.reset();
    imageCtx.setCurrentPage(snapshot.currentPage);
    setEntityMap(snapshot.entityMap);
    setRedactedCount(snapshot.redactedCount);
    setRedactionVersion((version) => version + 1); // 触发 result 阶段脱敏预览图重取
    setPseudonymMap(snapshot.pseudonymMap);
    setPseudonymMapLoading(false);
    setPseudonymMapError(null);
    setConfirmedPseudonymMap(snapshot.confirmedPseudonymMap);
    recognition.setProcessingMode(snapshot.processingMode);
    recognition.setReplacementMode(snapshot.replacementMode);
    recognition.setWatermarkText(snapshot.watermarkText);
    setResetConfirmOpen(false);
    setReportOpen(false);
    setVersionHistoryOpen(false);
  },
  [entityCtx, fileCtx, imageCtx, recognition],
);

// 挂载恢复（R1）：每个应用生命周期只做一次；幂等，StrictMode 双挂载无害。
const draftRestoreDoneRef = useRef(false);
useEffect(() => {
  if (draftRestoreDoneRef.current) return;
  draftRestoreDoneRef.current = true;
  const snapshot = parseDraft(
    getScopedStorageItem<string | null>(STORAGE_KEYS.PLAYGROUND_DRAFT, null),
  );
  if (!snapshot) return;
  applyDraftSnapshot(snapshot);
  showToast(t('playground.restored'), 'info');
}, [applyDraftSnapshot]);
```

import 增补：`parseDraft` 与类型 `PlaygroundDraftSnapshot`（from `../lib/playground-draft`）、`getScopedStorageItem`（from `@/lib/storage`）。注意 `imageCtx.setCurrentPage` 若未在 imageCtx 返回值中导出，则在 `use-playground-image.ts` 的返回对象中补导出（该 hook 已有 currentPage state 与 setCurrentPage，仅供内部使用的可能性）。

- [ ] **Step 3: 验证 + Commit**

```bash
cd frontend && npx tsc --noEmit -p tsconfig.json && npm run test && npm run build
git add frontend/src/features/playground/hooks/use-playground.ts frontend/src/features/playground/hooks/use-playground-image.ts frontend/src/i18n/zh.ts frontend/src/i18n/en.ts
git commit -m "feat(issue33): 应用挂载时从草稿恢复单文件处理现场（R1）"
```

Expected: 通过。

---

### Task 6: R2 + 历史页跳转入口（?file_id= 协议）

**Files:**
- Modify: `frontend/src/features/playground/hooks/use-playground-file.ts`（新增 `loadExistingFile`）
- Modify: `frontend/src/features/playground/hooks/use-playground.ts`（新增 `resumeFromFile` action）
- Modify: `frontend/src/features/playground/playground-page.tsx`（读 `?file_id`、切换确认框）
- Modify: `frontend/src/features/playground/playground-context.tsx`（透出新 action）
- Modify: `frontend/src/i18n/zh.ts`、`frontend/src/i18n/en.ts`

**Interfaces:**
- Consumes: Task 2 `planResume`/`needsSwitchConfirm`；Task 5 `applyDraftSnapshot`；`fileApi.getInfo`（`frontend/src/services/api.ts:210`，返回含 `original_filename`/`file_size`）。
- Produces:
  - `use-playground-file` 返回值新增 `loadExistingFile(fileId: string): Promise<void>`（拉 meta+parse → setFileInfo/setContent/setPendingFile，识别复用既有 pendingFile effect）。
  - playground context actions 新增 `resumeFromFile(targetFileId: string): Promise<void>`。

- [ ] **Step 1: 新增 i18n 文案**

```ts
// zh.ts
'playground.switchSessionTitle': '切换处理文件？',
'playground.switchSessionMessage': '当前单文件处理现场将被替换，尚未执行的编辑不会保留。',
'playground.switchSessionConfirm': '继续切换',
'playground.restoreRerunning': '未找到上次现场，正在按当前识别配置重新识别',
// en.ts
'playground.switchSessionTitle': 'Switch file?',
'playground.switchSessionMessage': 'The current single-file session will be replaced; unexecuted edits will be lost.',
'playground.switchSessionConfirm': 'Switch anyway',
'playground.restoreRerunning': 'Previous session not found; re-recognizing with current settings',
```

- [ ] **Step 2: use-playground-file.ts 新增 loadExistingFile**

在 `handleFileDrop` 之后新增（识别阶段完全复用既有 `pendingFile` effect，含服务健康 blocker 检查）：

```ts
// 从服务端按 file_id 重建会话（历史页「回到现场」且无草稿时的 R2 路径）：
// 不上传新文件，parse 后走与上传后完全相同的自动识别管线。
const loadExistingFile = useCallback(async (fileId: string) => {
  abortRef.current?.abort();
  const controller = new AbortController();
  abortRef.current = controller;
  const { signal } = controller;

  setIsLoading(true);
  setStage('upload');
  setUploadIssue(null);
  setRecognitionIssue(null);

  const opts = optionsRef.current;
  try {
    setLoadingMessage(t('playground.parsing'));
    const [infoRes, parseRes] = await Promise.all([
      authFetch(`/api/v1/files/${fileId}`, { signal }),
      authFetch(`/api/v1/files/${fileId}/parse`, { signal }),
    ]);
    if (signal.aborted) return;
    if (!infoRes.ok) throw new Error(await responseErrorMessage(infoRes, 'playground.parseFailed'));
    if (!parseRes.ok) throw new Error(await responseErrorMessage(parseRes, 'playground.parseFailed'));
    const info = await safeJson<{ original_filename?: string; file_size?: number }>(infoRes);
    const parseData = await safeJson<ParseResponse>(parseRes);
    if (signal.aborted) return;

    const isScanned = parseData.is_scanned || false;
    const pageCount = Math.max(1, Number(parseData.page_count || 1));
    const parsedFileType = parseData.file_type || 'pdf';
    const parsedContent = parseData.content || '';
    const parsedPages = Array.isArray(parseData.pages) ? parseData.pages : undefined;

    setFileInfo({
      file_id: fileId,
      filename: info.original_filename || fileId,
      file_size: info.file_size || 0,
      file_type: parsedFileType,
      is_scanned: isScanned,
      page_count: pageCount,
      pages: parsedPages,
    });
    setContent(parsedContent);
    opts.setBoundingBoxes([]);
    opts.resetImageHistory();
    opts.setEntities([]);
    setPendingFile({
      fileId,
      fileType: parsedFileType,
      isScanned,
      pageCount,
      content: parsedContent,
    });
  } catch (err) {
    if (signal.aborted) return;
    showToast(localizeErrorMessage(err, 'playground.restoreFileGone'), 'error');
    setIsLoading(false);
    setLoadingMessage('');
  } finally {
    if (abortRef.current === controller) {
      abortRef.current = null;
    }
  }
}, []);
```

并在返回对象中加入 `loadExistingFile`。

- [ ] **Step 3: use-playground.ts 新增 resumeFromFile 并透出 context**

```ts
const resumeFromFile = useCallback(
  async (targetFileId: string) => {
    const snapshot = parseDraft(
      getScopedStorageItem<string | null>(STORAGE_KEYS.PLAYGROUND_DRAFT, null),
    );
    const decision = planResume({ targetFileId, snapshot });
    if (decision.mode === 'unavailable') return;
    if (decision.mode === 'draft') {
      applyDraftSnapshot(decision.snapshot);
      showToast(t('playground.restored'), 'info');
      return;
    }
    // R2：无草稿（或草稿属于其他文件）→ 重置后按当前配置重新识别
    performReset();
    showToast(t('playground.restoreRerunning'), 'info');
    await fileCtx.loadExistingFile(decision.fileId);
  },
  [applyDraftSnapshot, fileCtx, performReset],
);
```

`planResume` 加入 import；在返回对象 actions 部分加 `resumeFromFile`；`playground-context.tsx` 的 `PlaygroundActionsContextValue` 加 `resumeFromFile: PlaygroundContextValue['resumeFromFile']`，actionsValue/依赖数组同步补 `ctx.resumeFromFile`。

- [ ] **Step 4: playground-page.tsx 读 ?file_id 并加切换确认**

`PlaygroundInner` 内加（import `useSearchParams` from 'react-router-dom'、`needsSwitchConfirm` from './lib/playground-draft'）：

```tsx
const [searchParams, setSearchParams] = useSearchParams();
const resumeFileId = searchParams.get('file_id');
const [switchConfirmTarget, setSwitchConfirmTarget] = useState<string | null>(null);
const resumeHandledRef = useRef<string | null>(null);

const startResume = (target: string) => {
  resumeHandledRef.current = target;
  void resumeFromFile(target);
  setSearchParams({}, { replace: true }); // 清参数，防刷新/回退重复触发
};

useEffect(() => {
  if (!resumeFileId || resumeHandledRef.current === resumeFileId) return;
  if (needsSwitchConfirm(fileInfo?.file_id ?? null, resumeFileId)) {
    setSwitchConfirmTarget(resumeFileId); // 已有其他会话：先确认再覆盖
    return;
  }
  startResume(resumeFileId);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- startResume 稳定引用由 useCallback 保证
}, [resumeFileId, fileInfo?.file_id]);
```

（`resumeFromFile`、`fileInfo` 均从既有 `usePlaygroundContext()` 解构；`startResume` 用 `useCallback` 包装，依赖 `resumeFromFile` 与 `setSearchParams`。）

JSX 中在既有 `ConfirmDialog`（重置确认）旁再加一个：

```tsx
<ConfirmDialog
  open={switchConfirmTarget !== null}
  title={t('playground.switchSessionTitle')}
  message={t('playground.switchSessionMessage')}
  confirmText={t('playground.switchSessionConfirm')}
  danger
  onConfirm={() => {
    const target = switchConfirmTarget;
    setSwitchConfirmTarget(null);
    if (target) startResume(target);
  }}
  onCancel={() => {
    setSwitchConfirmTarget(null);
    setSearchParams({}, { replace: true });
  }}
/>
```

- [ ] **Step 5: 验证 + Commit**

```bash
cd frontend && npx tsc --noEmit -p tsconfig.json && npm run test && npm run build
git add frontend/src/features/playground/
git commit -m "feat(issue33): 历史页跳转协议 /single?file_id= 与 R2 重新识别降级"
```

Expected: 通过。

---

### Task 7: P2 — 历史页「回到处理现场」入口

**Files:**
- Create: `frontend/src/utils/playgroundResume.ts`
- Test: `frontend/src/utils/playgroundResume.test.ts`
- Modify: `frontend/src/features/history/components/history-row.tsx`
- Modify: `frontend/src/i18n/zh.ts`、`frontend/src/i18n/en.ts`

**Interfaces:**
- Consumes: `@/types` 的 `FileListItem`（`upload_source`/`job_id`/`file_id` 字段）。
- Produces: `buildPlaygroundResumeAction(row: FileListItem): { kind: 'link'; to: string } | { kind: 'none' }`。

- [ ] **Step 1: 写失败测试**

`frontend/src/utils/playgroundResume.test.ts`：

```ts
import { describe, expect, it } from 'vitest';
import { buildPlaygroundResumeAction } from './playgroundResume';
import type { FileListItem } from '@/types';

function row(overrides: Partial<FileListItem>): FileListItem {
  return {
    file_id: 'f1',
    original_filename: 'a.pdf',
    file_size: 1,
    file_type: 'pdf',
    has_output: false,
    entity_count: 0,
    upload_source: 'playground',
    ...overrides,
  } as FileListItem;
}

describe('buildPlaygroundResumeAction', () => {
  it('playground 单文件行 → 链接到 /single?file_id=', () => {
    expect(buildPlaygroundResumeAction(row({}))).toEqual({
      kind: 'link',
      to: '/single?file_id=f1',
    });
  });

  it('file_id 需要编码', () => {
    const action = buildPlaygroundResumeAction(row({ file_id: 'a b/c' }));
    expect(action.kind === 'link' && action.to === '/single?file_id=a%20b%2Fc').toBe(true);
  });

  it('批量行（有 job_id）→ none，走既有 continue-review', () => {
    expect(buildPlaygroundResumeAction(row({ job_id: 'j1' })).kind).toBe('none');
  });

  it('非 playground 来源 → none', () => {
    expect(buildPlaygroundResumeAction(row({ upload_source: 'batch' })).kind).toBe('none');
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd frontend && npx vitest run src/utils/playgroundResume.test.ts
```

Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现**

`frontend/src/utils/playgroundResume.ts`：

```ts
// Copyright 2026 DataInfra-RedactionEverything Contributors

import type { FileListItem } from '@/types';

export type PlaygroundResumeAction = { kind: 'link'; to: string } | { kind: 'none' };

/** playground 单文件行展示「回到处理现场」；批量行走既有 continue-review，不放此入口 */
export function buildPlaygroundResumeAction(row: FileListItem): PlaygroundResumeAction {
  if ((row.upload_source ?? '') !== 'playground') return { kind: 'none' };
  if (row.job_id) return { kind: 'none' };
  return { kind: 'link', to: `/single?file_id=${encodeURIComponent(row.file_id)}` };
}
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd frontend && npx vitest run src/utils/playgroundResume.test.ts
```

Expected: PASS。

- [ ] **Step 5: history-row 接线**

`frontend/src/features/history/components/history-row.tsx`：

1. import：`import { buildPlaygroundResumeAction } from '@/utils/playgroundResume';`
2. `HistoryDataRow` 组件体内、`const reviewAction = ...` 旁加：`const playgroundResume = buildPlaygroundResumeAction(row);`
3. 第一个操作格（现 `reviewAction.kind === 'link'` 判断处，playground 行目前恒为 placeholder）改为三分支：

```tsx
<div className="jobs-action-cell">
  {reviewAction.kind === 'link' ? (
    <Button
      variant="outline"
      size="icon"
      className={cn(actionIconBtnBase, 'bg-background hover:bg-muted')}
      title={t('history.continueReview')}
      aria-label={t('history.continueReview')}
      asChild
    >
      <Link to={reviewAction.to} data-testid={`continue-review-${row.file_id}`}>
        <ArrowRight data-icon="inline-end" />
      </Link>
    </Button>
  ) : playgroundResume.kind === 'link' ? (
    <Button
      variant="outline"
      size="icon"
      className={cn(actionIconBtnBase, 'bg-background hover:bg-muted')}
      title={t('history.resumeSession')}
      aria-label={t('history.resumeSession')}
      asChild
    >
      <Link to={playgroundResume.to} data-testid={`resume-playground-${row.file_id}`}>
        <ArrowRight data-icon="inline-end" />
      </Link>
    </Button>
  ) : (
    actionPlaceholder
  )}
</div>
```

网格列数不变（填充既有占位格）。批量组行 `HistoryBatchRow` 不动。

- [ ] **Step 6: i18n 文案**

```ts
// zh.ts（history.* 区域）
'history.resumeSession': '回到处理现场',
// en.ts
'history.resumeSession': 'Resume session',
```

- [ ] **Step 7: 验证 + Commit**

```bash
cd frontend && npx tsc --noEmit -p tsconfig.json && npm run test && npm run build
git add frontend/src/utils/playgroundResume.ts frontend/src/utils/playgroundResume.test.ts frontend/src/features/history/components/history-row.tsx frontend/src/i18n/zh.ts frontend/src/i18n/en.ts
git commit -m "feat(issue33): 历史页 playground 行新增「回到处理现场」入口"
```

Expected: 通过。

---

### Task 8: 端到端验证、文档与流程收口

**Files:**
- Create: `/home/you/workspace/Desensitization/docs/acceptance/手动验收-Issue33-会话保持.md`（**不入库**）
- Modify: fork 仓库 Issue #33（评论，`--body-file`）

**Interfaces:**
- Consumes: Task 3-7 的全部产物；云实例部署流程（WORKFLOW.md，实例 scnet-main，前端 10800）。

- [ ] **Step 1: 全量本地门禁**

```bash
cd frontend && npm run test && npm run build
cd .. && rm -rf .venv-eval/../.venv-eval 2>/dev/null; true
# backend 无改动，若 CI 要求全绿则跑：source .venv-eval/bin/activate && pytest backend/tests -q
git log --oneline preview..HEAD   # 确认提交干净、无行尾漂移：git diff preview --stat 检查无大规模 CRLF 变更
```

- [ ] **Step 2: 云实例 GUI 验证**

按 WORKFLOW.md 部署（整个 `frontend/src/` rsync --delete → `~/.local/node-v22/bin` 的 node rebuild → 重启 tmux frontend 窗口 → ssh -N -L 10800 本地访问）。逐项验证并截图记录到验收文档：

1. **P0**：/single 识别完成 → 切到 历史页/设置页 → 返回 /single：识别结果与勾选态原样保留。
2. **R1-preview**：识别完成 → F5 刷新 → 进入 /single：现场恢复，出现「已恢复上次单文件处理现场」toast。
3. **R1-编辑**：手工取消某实体勾选、手动添加一个实体、替换模式下改一条化名 → 刷新 → 三处编辑全部保留。
4. **R1-result**：执行脱敏进入结果页 → 刷新 → 直接恢复到结果页，对照表 csv 与报告可正常打开/下载。
5. **P2-R1 路**：history 找到该 playground 行 → 点「回到处理现场」→ 秒级回到现场（草稿命中，无重新识别 loading）。
6. **P2-R2 路**：换一个浏览器 profile（无草稿）→ history 同一行 → 点入口 → 出现切换确认（若另一会话活动）→ 确认后按当前配置重新识别到 preview。
7. **异常路**：把该文件删除（回收站）→ history 残留行点入口 → 明确报「原文件已不存在」类提示，页面停在可用的上传态。
8. **多用户隔离**：用户 A 的草稿不影响用户 B（scoped key）。

- [ ] **Step 3: 验收文档与 Issue 收口**

写工作区 `docs/acceptance/手动验收-Issue33-会话保持.md`（步骤+预期+实测记录栏，供用户手动验收）。fork Issue #33 发定稿评论（`gh issue comment 33 --repo youayou-Lee/DataInfra-RedactionEverything --body-file …`）：决策摘要、方案三层结构、已知限制、设计文档路径 docs/issue-33-playground-session-restore.md。

- [ ] **Step 4: 流程门**

推分支 + 开 PR（base=preview）→ 独立 AI review（门③，按仓库评审规范）→ 修复 → 用户按验收文档手动验收（门⑥）→ **通过后 rebase 最新 preview、重跑 pytest + 前端 build+test，再 merge**（门⑦，T1 同款约定）。

## Self-Review 记录

- 覆盖检查：Issue #33 主诉①切页丢状态 → Task 3；主诉②history 回不去 → Task 6+7；刷新丢状态（讨论新增）→ Task 2/4/5；与 T3 的复用点（恢复管线）→ Task 6 的 `loadExistingFile`+`planResume` 为纯函数/hook 级复用单位，T3 批量复核可参考。
- 类型一致性：`PlaygroundDraftSnapshot` 在 Task 2 定义、Task 4 构建、Task 5/6 消费，字段一一对应；`processingMode`/`replacementMode` 联合类型以合入 T1 后的 `use-playground-recognition.ts` 为准（Task 2 Step 4 有核对步骤）。
- 占位符扫描：无 TBD；Task 3 Step 2 与 Task 6 Step 4 的代码为骨架+精确落点说明，执行者需先读目标文件（已注明）。
- 无后端改动；历史页未执行文件已天然展示（决策 4），无需新增列表逻辑。
