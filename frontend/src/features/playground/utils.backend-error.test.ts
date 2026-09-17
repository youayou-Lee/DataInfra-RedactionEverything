// Issue #51：后端错误信封的原因提取——空对象 detail 不得挡住 message
import { describe, expect, it } from 'vitest';
import { extractBackendErrorMessage } from './utils';

describe('extractBackendErrorMessage', () => {
  it('空对象 detail 不挡住 message（#51 实测信封形状）', () => {
    const data = {
      error_code: 'HTTP_400',
      message: '文件损坏或格式异常，无法解析为 Word 文档',
      detail: {},
      request_id: 'f52bcf543dd9',
    };
    expect(extractBackendErrorMessage(data)).toBe('文件损坏或格式异常，无法解析为 Word 文档');
  });

  it('detail 为字符串时优先使用', () => {
    expect(extractBackendErrorMessage({ detail: '不支持的照片格式' })).toBe('不支持的照片格式');
  });

  it('全部缺失或非字符串时返回 null（调用方走本地化兜底文案）', () => {
    expect(extractBackendErrorMessage({ detail: {}, message: 42 })).toBeNull();
    expect(extractBackendErrorMessage({})).toBeNull();
    expect(extractBackendErrorMessage(null)).toBeNull();
    expect(extractBackendErrorMessage({ detail: '   ' })).toBeNull();
  });
});
