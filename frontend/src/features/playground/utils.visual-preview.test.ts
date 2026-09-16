// Copyright 2026 DataInfra-RedactionEverything Contributors

import { describe, expect, it } from 'vitest';
import { boxesForRedactPayload, isVisualPreviewMode } from './utils';

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
