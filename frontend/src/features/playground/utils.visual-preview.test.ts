// Copyright 2026 DataInfra-RedactionEverything Contributors

import { describe, expect, it } from 'vitest';
import { isVisualPreviewMode } from './utils';

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
