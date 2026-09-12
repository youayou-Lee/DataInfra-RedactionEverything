import { describe, expect, it } from 'vitest';
import { buildPlaygroundResumeAction } from './playgroundResume';
import type { FileListItem } from '@/types';

function row(overrides: Partial<FileListItem>): FileListItem {
  return {
    file_id: 'f1',
    original_filename: 'a.pdf',
    file_size: 1,
    file_type: 'pdf',
    has_output: false,
    entity_count: 0,
    upload_source: 'playground',
    ...overrides,
  } as FileListItem;
}

describe('buildPlaygroundResumeAction', () => {
  it('playground 单文件行 → 链接到 /single?file_id=', () => {
    expect(buildPlaygroundResumeAction(row({}))).toEqual({
      kind: 'link',
      to: '/single?file_id=f1',
    });
  });

  it('file_id 需要编码', () => {
    const action = buildPlaygroundResumeAction(row({ file_id: 'a b/c' }));
    expect(action.kind === 'link' && action.to === '/single?file_id=a%20b%2Fc').toBe(true);
  });

  it('批量行（有 job_id）→ none，走既有 continue-review', () => {
    expect(buildPlaygroundResumeAction(row({ job_id: 'j1' })).kind).toBe('none');
  });

  it('非 playground 来源 → none', () => {
    expect(buildPlaygroundResumeAction(row({ upload_source: 'batch' })).kind).toBe('none');
  });
});
