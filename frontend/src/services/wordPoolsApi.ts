// Copyright 2026 DataInfra-RedactionEverything Contributors

import { get, post, put, del } from './api-client';

/** 词池替换词耗尽后的取词策略（与后端 word_pool_service 一致） */
export type WordPoolStrategy = 'numbered' | 'cycle' | 'generated';

export interface WordPool {
  words: string[];
  strategy: WordPoolStrategy;
  custom_map: Record<string, string>;
}

export interface WordPoolsResponse {
  defaults: Record<string, WordPool>;
  overrides: Record<string, WordPool>;
  merged: Record<string, WordPool>;
}

export interface WordPoolUpdate {
  words: string[];
  strategy: WordPoolStrategy;
  custom_map: Record<string, string>;
}

export interface WordPoolImportResult {
  message: string;
  count: number;
}

export async function fetchWordPools(): Promise<WordPoolsResponse> {
  return get<WordPoolsResponse>('/word-pools');
}

export async function exportWordPools(): Promise<Record<string, WordPool>> {
  const data = await get<Record<string, WordPool> | { overrides: Record<string, WordPool> }>(
    '/word-pools/export',
  );
  // 兼容裸 dict 与 { overrides: ... } 两种返回形状
  const maybe = data as { overrides?: Record<string, WordPool> } | null;
  if (maybe && typeof maybe === 'object' && maybe.overrides) return maybe.overrides;
  return (data as Record<string, WordPool> | null) ?? {};
}

export async function importWordPools(
  overrides: Record<string, WordPool>,
  merge: boolean,
): Promise<WordPoolImportResult> {
  return post<WordPoolImportResult>(
    '/word-pools/import',
    { overrides },
    {
      params: { merge },
    },
  );
}

export async function updateWordPool(typeId: string, body: WordPoolUpdate): Promise<WordPool> {
  return put<WordPool>(`/word-pools/${encodeURIComponent(typeId)}`, body);
}

export async function deleteWordPool(typeId: string): Promise<{ deleted: boolean }> {
  return del<{ deleted: boolean }>(`/word-pools/${encodeURIComponent(typeId)}`);
}
