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
  // 用户手改过的映射键：恢复后刷新自动映射时不被覆盖
  pseudonymUserEditedKeys?: string[];
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

// ---------------------------------------------------------------------------
// 服务端识别缓存（R2 秒回）：后端每次 NER 成功都会把 entities + 当时的识别配置
// 写进文件记录（GET /files/{id} 可取）。命中则直接恢复「当时的现场」，
// 未识别过（实体为空，含扫描件恒空）才重跑。
// ---------------------------------------------------------------------------

export type ServerResumeInfo = {
  entities?: unknown;
  recognition_config?: { entity_type_ids?: string[] | null } | null;
};

export type ServerCachedResumeDecision =
  | { mode: 'cached'; entities: Record<string, unknown>[]; entityTypeIds: string[] | null }
  | { mode: 'rerun' };

export function planServerCachedResume(info: ServerResumeInfo): ServerCachedResumeDecision {
  if (!Array.isArray(info.entities) || info.entities.length === 0) return { mode: 'rerun' };
  const ids = info.recognition_config?.entity_type_ids;
  return {
    mode: 'cached',
    entities: info.entities as Record<string, unknown>[],
    entityTypeIds: Array.isArray(ids) ? ids : null,
  };
}

/** 超长单页文本的虚拟分页：按固定字符窗口切分，避免全文+全实体一次性渲染 */
export function splitVirtualPages(content: string, limit: number): string[] {
  if (limit <= 0 || content.length <= limit) return [content];
  const chunks: string[] = [];
  for (let i = 0; i < content.length; i += limit) {
    chunks.push(content.slice(i, i + limit));
  }
  return chunks;
}
