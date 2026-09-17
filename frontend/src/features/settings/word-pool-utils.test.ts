// Copyright 2026 DataInfra-RedactionEverything Contributors

import { describe, expect, it } from 'vitest';
import {
  dedupeCustomMap,
  extractImportOverrides,
  nextCustomMapRowId,
  parseWordsText,
} from './word-pool-utils';

describe('dedupeCustomMap', () => {
  it('无重复时原样保留', () => {
    const { map, duplicates } = dedupeCustomMap([
      { orig: '陈明飞', repl: '张某1' },
      { orig: '某公司', repl: '某公司1' },
    ]);
    expect(map).toEqual({ 陈明飞: '张某1', 某公司: '某公司1' });
    expect(duplicates).toEqual([]);
  });

  it('原词重复时 last-wins 并报告重复项', () => {
    const { map, duplicates } = dedupeCustomMap([
      { orig: '陈明飞', repl: '张某1' },
      { orig: '陈明飞', repl: '张某2' },
      { orig: '陈明飞', repl: '张某3' },
    ]);
    expect(map).toEqual({ 陈明飞: '张某3' });
    expect(duplicates).toEqual(['陈明飞']);
  });

  it('忽略空原词/空替换词行', () => {
    const { map, duplicates } = dedupeCustomMap([
      { orig: '', repl: 'X' },
      { orig: '甲', repl: '' },
      { orig: '  ', repl: 'Y' },
      { orig: '乙', repl: 'B' },
    ]);
    expect(map).toEqual({ 乙: 'B' });
    expect(duplicates).toEqual([]);
  });

  it('先忽略空行再判重（同原词一空一实不算重复）', () => {
    const { map, duplicates } = dedupeCustomMap([
      { orig: '甲', repl: '' },
      { orig: '甲', repl: 'A' },
    ]);
    expect(map).toEqual({ 甲: 'A' });
    expect(duplicates).toEqual([]);
  });
});

describe('parseWordsText', () => {
  it('按行拆分、去空白、滤空行', () => {
    expect(parseWordsText('张三\n 李四 \r\n\r\n王五')).toEqual(['张三', '李四', '王五']);
  });

  it('空文本返回空数组', () => {
    expect(parseWordsText('')).toEqual([]);
  });
});

describe('extractImportOverrides', () => {
  it('解析 { overrides: ... } 包裹形状', () => {
    const payload = {
      overrides: { person: { words: ['张三'], strategy: 'numbered', custom_map: {} } },
    };
    expect(extractImportOverrides(payload)).toEqual(payload.overrides);
  });

  it('裸 dict 形状也可解析（导入侧需要兼容导出旧文件）', () => {
    const payload = { person: { words: ['张三'], strategy: 'cycle', custom_map: {} } };
    expect(extractImportOverrides(payload)).toEqual(payload);
  });

  it('custom_map 非空时豁免 words 缺失（对齐后端 _normalize_pool）', () => {
    const payload = { org: { strategy: 'numbered', custom_map: { 某局: '某机关1' } } };
    expect(extractImportOverrides(payload)).toEqual({
      org: { words: [], strategy: 'numbered', custom_map: { 某局: '某机关1' } },
    });
  });

  it('words 与 custom_map 均缺失/为空时拒绝', () => {
    expect(extractImportOverrides({ org: { strategy: 'numbered', custom_map: {} } })).toBeNull();
    expect(extractImportOverrides({ org: { words: [] } })).toBeNull();
    expect(extractImportOverrides({})).toBeNull();
  });

  it('words/custom_map 类型非法时拒绝', () => {
    expect(extractImportOverrides({ org: { words: '张三', custom_map: {} } })).toBeNull();
    expect(
      extractImportOverrides({ org: { words: ['张三'], custom_map: ['a'] } }),
    ).toBeNull();
    expect(extractImportOverrides({ org: null })).toBeNull();
    expect(extractImportOverrides({ org: ['x'] })).toBeNull();
  });

  it('非对象/数组输入拒绝', () => {
    expect(extractImportOverrides(null)).toBeNull();
    expect(extractImportOverrides('x')).toBeNull();
    expect(extractImportOverrides([1])).toBeNull();
  });

  it('非法 strategy 回退 numbered，缺省 strategy 同样回退', () => {
    const out = extractImportOverrides({
      a: { words: ['x'], strategy: 'bogus', custom_map: {} },
      b: { words: ['y'], custom_map: {} },
    });
    expect(out?.a.strategy).toBe('numbered');
    expect(out?.b.strategy).toBe('numbered');
  });
});

describe('nextCustomMapRowId', () => {
  it('返回单调递增的稳定 id', () => {
    const a = nextCustomMapRowId();
    const b = nextCustomMapRowId();
    expect(b).toBeGreaterThan(a);
  });
});
