// Copyright 2026 DataInfra-RedactionEverything Contributors

// Issue #92：.doc 解禁——#46 判「转换链不可用」实为当时实例宿主未装 LibreOffice，
// 2026-09-18 实例探针（含 WPS 真实样本）转换/解析/脱敏 0 残留；.rtf 仍不予受理
// （解析毁 CJK 转义、成品残留原文）。
export const ACCEPTED_UPLOAD_FILE_TYPES = {
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
  'application/msword': ['.doc'],
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
