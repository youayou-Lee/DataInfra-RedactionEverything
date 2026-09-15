// Copyright 2026 DataInfra-RedactionEverything Contributors

import type { FileListItem } from '@/types';

export type PlaygroundResumeAction = { kind: 'link'; to: string } | { kind: 'none' };

/** playground 单文件行展示「回到处理现场」；批量行走既有 continue-review，不放此入口 */
export function buildPlaygroundResumeAction(row: FileListItem): PlaygroundResumeAction {
  if ((row.upload_source ?? '') !== 'playground') return { kind: 'none' };
  if (row.job_id) return { kind: 'none' };
  return { kind: 'link', to: `/single?file_id=${encodeURIComponent(row.file_id)}` };
}
