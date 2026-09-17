// Copyright 2026 DataInfra-RedactionEverything Contributors

import type { WordPool, WordPoolStrategy } from '@/services/wordPoolsApi';

const STRATEGIES: readonly WordPoolStrategy[] = ['derived', 'numbered', 'cycle', 'generated'];

export interface CustomMapRow {
  id: number;
  orig: string;
  repl: string;
}

let rowIdCounter = 0;

/** customMap 行稳定 id（受控行删除/重排时避免按 index 复用） */
export function nextCustomMapRowId(): number {
  rowIdCounter += 1;
  return rowIdCounter;
}

export interface DedupeResult {
  map: Record<string, string>;
  /** 重复原词列表（后者覆盖前者，last-wins） */
  duplicates: string[];
}

/**
 * custom_map 去重：原词重复时保留最后出现的替换词，并返回重复项供 UI 提示。
 * 空原词/空替换词的行被忽略。
 */
export function dedupeCustomMap(
  entries: ReadonlyArray<{ orig: string; repl: string }>,
): DedupeResult {
  const map: Record<string, string> = {};
  const duplicates: string[] = [];
  entries.forEach(({ orig, repl }) => {
    const key = orig.trim();
    const value = repl.trim();
    if (!key || !value) return;
    if (key in map) {
      if (!duplicates.includes(key)) duplicates.push(key);
    }
    map[key] = value;
  });
  return { map, duplicates };
}

export function parseWordsText(wordsText: string): string[] {
  return wordsText
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

/**
 * 解析导入文件：支持 { overrides: {...} } 包裹形状（导出恒为该形状），
 * 校验宽容度对齐后端 _normalize_pool：custom_map 非空时豁免 words 缺失。
 * 非法返回 null。
 */
export function extractImportOverrides(parsed: unknown): Record<string, WordPool> | null {
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
  const container = parsed as Record<string, unknown>;
  const overridesPayload =
    'overrides' in container &&
    container.overrides &&
    typeof container.overrides === 'object' &&
    !Array.isArray(container.overrides)
      ? container.overrides
      : parsed;
  const entries = Object.entries(overridesPayload as Record<string, unknown>);
  if (!entries.length) return null;
  const result: Record<string, WordPool> = {};
  for (const [typeId, pool] of entries) {
    if (!pool || typeof pool !== 'object' || Array.isArray(pool)) return null;
    const { words, strategy, custom_map } = pool as Record<string, unknown>;
    const wordsOk = words === undefined || Array.isArray(words);
    const customMapOk =
      custom_map === undefined ||
      (typeof custom_map === 'object' && !Array.isArray(custom_map));
    const hasContent =
      (Array.isArray(words) && words.length > 0) ||
      (typeof custom_map === 'object' &&
        custom_map !== null &&
        !Array.isArray(custom_map) &&
        Object.keys(custom_map).length > 0);
    if (!wordsOk || !customMapOk || !hasContent) return null;
    result[typeId] = {
      words: Array.isArray(words) ? (words as string[]) : [],
      strategy:
        typeof strategy === 'string' && STRATEGIES.includes(strategy as WordPoolStrategy)
          ? (strategy as WordPoolStrategy)
          : 'numbered',
      custom_map: typeof custom_map === 'object' && custom_map ? (custom_map as Record<string, string>) : {},
    };
  }
  return result;
}
