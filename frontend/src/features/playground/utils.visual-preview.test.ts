// Copyright 2026 DataInfra-RedactionEverything Contributors

import { describe, expect, it } from 'vitest';
import {
  boxesForRedactPayload,
  isVisualPreviewMode,
  mergeNerBoxes,
  syncEntitiesWithNerBoxes,
} from './utils';

// Issue #66：预览范式跟随处理模式
describe('isVisualPreviewMode', () => {
  it('扫描件/图片恒为图像范式（与模式无关）', () => {
    expect(isVisualPreviewMode('pdf_scanned', true, 'mask')).toBe(true);
    expect(isVisualPreviewMode('pdf_scanned', true, 'replace')).toBe(true);
    expect(isVisualPreviewMode('image', false, 'replace')).toBe(true);
    expect(isVisualPreviewMode('image', false, 'mask')).toBe(true);
  });

  it('文本型 PDF：打码=图像工作台，替换=文本范式', () => {
    expect(isVisualPreviewMode('pdf', false, 'mask')).toBe(true);
    expect(isVisualPreviewMode('pdf', false, 'replace')).toBe(false);
  });

  it('docx/txt 等文本格式恒为文本范式（打码被 #59 门控不存在）', () => {
    expect(isVisualPreviewMode('docx', false, 'mask')).toBe(false);
    expect(isVisualPreviewMode('txt', false, 'replace')).toBe(false);
  });

  it('无文件类型时为 false', () => {
    expect(isVisualPreviewMode(undefined, false, 'mask')).toBe(false);
  });
});

// Issue #66 A 案：替换模式不携带拉框（防后端误路由图像管线）
describe('boxesForRedactPayload', () => {
  const boxes = [{ id: 'b1' } as never, { id: 'b2' } as never];

  it('文本型文件 + 替换模式：不携带（框不参与替换执行）', () => {
    expect(boxesForRedactPayload(false, 'replace', boxes)).toEqual([]);
  });

  it('文本型 PDF + 打码模式：携带（拉框参与栅格化）', () => {
    expect(boxesForRedactPayload(false, 'mask', boxes)).toEqual(boxes);
  });

  it('扫描件/图片：两种模式都携带（既有行为不变）', () => {
    expect(boxesForRedactPayload(true, 'mask', boxes)).toEqual(boxes);
    expect(boxesForRedactPayload(true, 'replace', boxes)).toEqual(boxes);
  });
});

// Issue #66：识别实体自动成框的合并与执行同步
describe('mergeNerBoxes', () => {
  it('重新定位替换全部 ner 框、保留手拉框', () => {
    const manual = { id: 'm1', source: 'manual', text: '手工框' } as never;
    const oldNer = { id: 'n1', source: 'ner', text: '旧实体' } as never;
    const newNer = [{ id: 'n2', source: 'ner', text: '新实体' } as never];
    const merged = mergeNerBoxes([manual, oldNer], newNer);
    expect(merged.map((b: never) => (b as { id: string }).id)).toEqual(['m1', 'n2']);
  });
});

describe('syncEntitiesWithNerBoxes', () => {
  const entities = [
    { text: '张三', selected: true },
    { text: '李四', selected: true }, // 无框实体（定位失败）
  ];
  it('实体选中跟随其 ner 框；无框实体保持原选中（后端兜底）', () => {
    const boxes = [
      { text: '张三', source: 'ner', selected: false } as never,
    ];
    const synced = syncEntitiesWithNerBoxes(entities, boxes);
    expect(synced[0].selected).toBe(false); // 框被取消勾选 → 实体不选
    expect(synced[1].selected).toBe(true); // 无框 → 保持
  });
  it('manual 框不影响实体同步', () => {
    const boxes = [{ text: '张三', source: 'manual', selected: false } as never];
    const synced = syncEntitiesWithNerBoxes(entities, boxes);
    expect(synced[0].selected).toBe(true); // manual 框不算实体的 UI
  });
});

describe('mergeNerBoxes 勾选继承（增量评审 I3）', () => {
  const located = [{ id: 'ner_new', source: 'ner', text: '张三', selected: true } as never];

  it('实体侧取消勾选 → 新框保持未选（替换模式的选择不被翻转）', () => {
    const entityByText = new Map([['张三', { selected: false }]]);
    const merged = mergeNerBoxes([], located, entityByText);
    expect(merged[0].selected).toBe(false);
  });

  it('旧 ner 框未选（用户在打码模式取消）→ 重定位后保持未选', () => {
    const prev = [{ id: 'ner_old', source: 'ner', text: '张三', selected: false } as never];
    const merged = mergeNerBoxes(prev, located);
    expect(merged[0].selected).toBe(false);
  });

  it('两来源都未明确取消 → 默认选中', () => {
    const merged = mergeNerBoxes([], located, new Map());
    expect(merged[0].selected).toBe(true);
  });
});
