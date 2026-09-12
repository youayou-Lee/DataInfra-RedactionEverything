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
