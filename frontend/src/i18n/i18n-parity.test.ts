import { describe, expect, it } from 'vitest';
import { en } from './en';
import { zh } from './zh';

// zh/en 双语手工维护，键位漂移只会在运行时以 undefined 文案暴露——用契约测试堵住
describe('i18n zh/en key parity', () => {
  it('zh 与 en 键集合完全一致', () => {
    const missingInEn = Object.keys(zh).filter((key) => !(key in en));
    const missingInZh = Object.keys(en).filter((key) => !(key in zh));
    expect(missingInEn, `en 缺少: ${missingInEn.join(', ')}`).toEqual([]);
    expect(missingInZh, `zh 缺少: ${missingInZh.join(', ')}`).toEqual([]);
  });

  it('无空翻译', () => {
    for (const [key, value] of Object.entries(zh)) {
      expect(value.trim().length, `zh.${key} 为空`).toBeGreaterThan(0);
    }
    for (const [key, value] of Object.entries(en)) {
      expect(value.trim().length, `en.${key} 为空`).toBeGreaterThan(0);
    }
  });
});
