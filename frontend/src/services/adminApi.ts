// Copyright 2026 DataInfra-RedactionEverything Contributors

import { get } from './api-client';

/** 管理控制台（Issue #77）——super_admin 跨用户查看注册用户与上传文件。 */

export interface AdminUserSummary {
  username: string;
  role: string;
  created_at?: string | null;
  disabled: boolean;
  file_count: number;
}

export interface AdminUserFile {
  file_id: string;
  original_filename: string | null;
  file_type: string | null;
  file_size: number;
  created_at?: string | null;
  upload_source: string;
  has_output: boolean;
}

export interface AdminUserFilesPage {
  items: AdminUserFile[];
  total: number;
  page: number;
  page_size: number;
}

export async function fetchAdminUsers(): Promise<AdminUserSummary[]> {
  return get<AdminUserSummary[]>('/admin/users');
}

export async function fetchAdminUserFiles(
  username: string,
  page: number,
  pageSize: number,
): Promise<AdminUserFilesPage> {
  return get<AdminUserFilesPage>(
    `/admin/users/${encodeURIComponent(username)}/files`,
    {
      params: { page, page_size: pageSize },
    },
  );
}

/** 供 downloadFile 使用的完整 API 路径（cookie 鉴权由 fetch credentials 带上）。 */
export function adminUserFileDownloadUrl(username: string, fileId: string): string {
  return `/api/v1/admin/users/${encodeURIComponent(username)}/files/${encodeURIComponent(fileId)}/download`;
}
