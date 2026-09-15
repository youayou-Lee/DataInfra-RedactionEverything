import { describe, expect, it } from 'vitest';
import {
  PLAYGROUND_DRAFT_MAX_JSON_LENGTH,
  PLAYGROUND_DRAFT_VERSION,
  buildDraftSnapshot,
  needsSwitchConfirm,
  parseDraft,
  planResume,
  planServerCachedResume,
  serializeDraft,
  splitVirtualPages,
} from './playground-draft';
import type { DraftSnapshotInput } from './playground-draft';

const baseInput: DraftSnapshotInput = {
  stage: 'preview',
  fileInfo: { file_id: 'f1', filename: 'a.pdf', file_size: 123, file_type: 'pdf', is_scanned: false, page_count: 2, pages: ['张三于2024年借款。', '第二页'] },
  content: '张三于2024年借款。第二页',
  entities: [
    { id: 'e1', text: '张三', type: 'person', start: 0, end: 2, selected: true, source: 'llm' },
    { id: 'e2', text: '李四', type: 'person', start: 10, end: 12, selected: false, source: 'manual' },
  ],
  boundingBoxes: [],
  processingMode: 'replace',
  replacementMode: 'structured',
  watermarkText: '',
  pseudonymMap: { 张三: '化名一' },
  confirmedPseudonymMap: null,
  entityMap: {},
  redactedCount: 0,
  currentPage: 1,
};

describe('buildDraftSnapshot + serializeDraft + parseDraft', () => {
  it('roundtrip：序列化后解析得到等价快照', () => {
    const snapshot = buildDraftSnapshot(baseInput);
    expect(snapshot.version).toBe(PLAYGROUND_DRAFT_VERSION);
    const parsed = parseDraft(serializeDraft(snapshot));
    expect(parsed).toEqual(snapshot);
  });

  it('版本不匹配的快照被拒绝', () => {
    const snapshot = buildDraftSnapshot(baseInput);
    const raw = JSON.stringify({ ...snapshot, version: PLAYGROUND_DRAFT_VERSION + 1 });
    expect(parseDraft(raw)).toBeNull();
  });

  it('损坏的 JSON 被拒绝', () => {
    expect(parseDraft('not-json{')).toBeNull();
    expect(parseDraft(null)).toBeNull();
    expect(parseDraft(undefined)).toBeNull();
  });

  it('缺关键字段（file_id/entities/boundingBoxes）被拒绝', () => {
    const snapshot = buildDraftSnapshot(baseInput);
    expect(parseDraft(JSON.stringify({ ...snapshot, fileInfo: { filename: 'x' } }))).toBeNull();
    expect(parseDraft(JSON.stringify({ ...snapshot, entities: 'no' }))).toBeNull();
    expect(parseDraft(JSON.stringify({ ...snapshot, boundingBoxes: undefined }))).toBeNull();
    expect(parseDraft(JSON.stringify({ ...snapshot, stage: 'upload' }))).toBeNull();
  });

  it('文本型 preview 快照必须有非空 content；扫描件/图片/result 不要求', () => {
    const snapshot = buildDraftSnapshot(baseInput);
    expect(parseDraft(JSON.stringify({ ...snapshot, content: '' }))).toBeNull();

    const scanned = buildDraftSnapshot({ ...baseInput, fileInfo: { ...baseInput.fileInfo, is_scanned: true }, content: '' });
    expect(parseDraft(JSON.stringify(scanned))).not.toBeNull();

    const result = buildDraftSnapshot({ ...baseInput, stage: 'result', content: '' });
    expect(parseDraft(JSON.stringify(result))).not.toBeNull();
  });

  it('超过大小上限时 serializeDraft 返回 null（放弃持久化）', () => {
    const huge = buildDraftSnapshot({ ...baseInput, content: 'x'.repeat(PLAYGROUND_DRAFT_MAX_JSON_LENGTH) });
    expect(serializeDraft(huge)).toBeNull();
  });
});

describe('planResume', () => {
  it('草稿命中：file_id 一致 → draft', () => {
    const snapshot = buildDraftSnapshot(baseInput);
    expect(planResume({ targetFileId: 'f1', snapshot })).toEqual({ mode: 'draft', snapshot });
  });

  it('草稿属于其他文件或缺失 → rerun', () => {
    const snapshot = buildDraftSnapshot(baseInput);
    expect(planResume({ targetFileId: 'f2', snapshot })).toEqual({ mode: 'rerun', fileId: 'f2' });
    expect(planResume({ targetFileId: 'f2', snapshot: null })).toEqual({ mode: 'rerun', fileId: 'f2' });
  });

  it('空 file_id → unavailable', () => {
    expect(planResume({ targetFileId: '', snapshot: null })).toEqual({ mode: 'unavailable' });
  });
});

describe('needsSwitchConfirm', () => {
  it('无当前会话或同文件 → 不确认', () => {
    expect(needsSwitchConfirm(null, 'f1')).toBe(false);
    expect(needsSwitchConfirm('f1', 'f1')).toBe(false);
  });
  it('不同文件 → 确认', () => {
    expect(needsSwitchConfirm('f1', 'f2')).toBe(true);
  });
});

describe('planServerCachedResume', () => {
  it('服务端有实体 → cached，透传实体与当时的类型配置', () => {
    const info = {
      entities: [{ text: '张三', type: 'person', start: 0, end: 2 }],
      recognition_config: { entity_type_ids: ['person', 'phone'] },
    };
    expect(planServerCachedResume(info)).toEqual({
      mode: 'cached',
      entities: info.entities,
      entityTypeIds: ['person', 'phone'],
    });
  });

  it('实体为空（未识别过/扫描件）→ rerun', () => {
    expect(planServerCachedResume({ entities: [] })).toEqual({ mode: 'rerun' });
    expect(planServerCachedResume({})).toEqual({ mode: 'rerun' });
    expect(planServerCachedResume({ entities: 'bad' })).toEqual({ mode: 'rerun' });
  });

  it('无识别配置 → cached 且 entityTypeIds=null（前端保持当前选择）', () => {
    expect(planServerCachedResume({ entities: [{ id: 'e1' }] })).toEqual({
      mode: 'cached',
      entities: [{ id: 'e1' }],
      entityTypeIds: null,
    });
    expect(
      planServerCachedResume({ entities: [{ id: 'e1' }], recognition_config: null }),
    ).toEqual({ mode: 'cached', entities: [{ id: 'e1' }], entityTypeIds: null });
    expect(
      planServerCachedResume({ entities: [{ id: 'e1' }], recognition_config: { entity_type_ids: null } }),
    ).toEqual({ mode: 'cached', entities: [{ id: 'e1' }], entityTypeIds: null });
  });
});

describe('splitVirtualPages', () => {
  it('不超限 → 单页原样返回', () => {
    expect(splitVirtualPages('abc', 100)).toEqual(['abc']);
    expect(splitVirtualPages('', 100)).toEqual(['']);
    expect(splitVirtualPages('12345', 5)).toEqual(['12345']);
  });

  it('超限 → 均匀窗口切分，拼接无损', () => {
    const pages = splitVirtualPages('a'.repeat(11), 5);
    expect(pages).toEqual(['aaaaa', 'aaaaa', 'a']);
    expect(pages.join('')).toBe('a'.repeat(11));
    expect(pages.map((p) => p.length)).toEqual([5, 5, 1]);
  });

  it('窗口起点 = 前缀页长度之和（实体偏移映射依赖此性质）', () => {
    const content = 'abcdefghij';
    const pages = splitVirtualPages(content, 3);
    let offset = 0;
    for (const p of pages) {
      expect(content.slice(offset, offset + p.length)).toBe(p);
      offset += p.length;
    }
  });
});
