// Copyright 2026 DataInfra-RedactionEverything Contributors

import { describe, expect, it } from 'vitest';
import { buildPseudonymCsv, getModePreview } from './utils';
import type { Entity } from './types';

function entity(partial: Partial<Entity>): Entity {
  return {
    id: String(partial.id ?? 'e1'),
    text: partial.text ?? '',
    type: partial.type ?? 'PERSON',
    start: 0,
    end: 0,
    selected: partial.selected ?? true,
    source: 'llm',
    page: 1,
    ...partial,
  } as Entity;
}

describe('buildPseudonymCsv', () => {
  it('生成 BOM + 表头 + 映射行，并统计出现次数', () => {
    const entities = [
      entity({ text: '陈明飞', type: 'PERSON' }),
      entity({ text: '陈明飞', type: 'PERSON' }),
      entity({ text: '某公司', type: 'INSTITUTION_NAME' }),
    ];
    const csv = buildPseudonymCsv(entities, { 陈明飞: '张某1', 某公司: '某公司1' });
    expect(csv.startsWith('\uFEFF')).toBe(true);
    const lines = csv.slice(1).trimEnd().split('\r\n');
    expect(lines[0]).toBe('原文,类型,化名,出现次数');
    expect(lines).toContain('陈明飞,PERSON,张某1,2');
    expect(lines).toContain('某公司,INSTITUTION_NAME,某公司1,1');
  });

  it('忽略不在实体列表中的映射项（未勾选/已删除）', () => {
    const csv = buildPseudonymCsv([entity({ text: '甲' })], { 甲: 'A', 乙: 'B' });
    const lines = csv.slice(1).trimEnd().split('\r\n');
    expect(lines).toHaveLength(2);
    expect(lines[1].startsWith('甲,')).toBe(true);
  });

  it('包含逗号/引号/换行的值按 CSV 规则转义', () => {
    const csv = buildPseudonymCsv([entity({ text: 'a,b' })], { 'a,b': 'x"y' });
    expect(csv.slice(1).trimEnd().split('\r\n')[1]).toBe('"a,b",PERSON,"x""y",1');
  });
});

describe('getModePreview (pseudonym)', () => {
  it('有映射时显示实际替换词', () => {
    const sample = entity({ text: '陈明飞' });
    expect(getModePreview('pseudonym', sample, { 陈明飞: '张某1' })).toBe('陈明飞 -> 张某1');
  });

  it('无映射时回退到示例化名', () => {
    const sample = entity({ text: '陈明飞' });
    expect(getModePreview('pseudonym', sample, {})).toBe('陈明飞 -> 化名甲');
  });
});
