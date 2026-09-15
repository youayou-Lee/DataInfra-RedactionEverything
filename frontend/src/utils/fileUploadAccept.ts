// Copyright 2026 DataInfra-RedactionEverything Contributors

// Issue #50（#46 实测收口）：.doc 转换链不可用、.rtf 解析毁 CJK 转义且成品残留原文，
// 均不予受理；支持面以实测报告 eval/reports/20260915-164256-*-format-matrix 为准。
export const ACCEPTED_UPLOAD_FILE_TYPES = {
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
  'application/pdf': ['.pdf'],
  'text/plain': ['.txt', '.md'],
  'text/html': ['.html', '.htm'],
  'image/jpeg': ['.jpg', '.jpeg'],
  'image/png': ['.png'],
  'image/bmp': ['.bmp'],
  'image/gif': ['.gif'],
  'image/webp': ['.webp'],
  'image/tiff': ['.tif', '.tiff'],
};
