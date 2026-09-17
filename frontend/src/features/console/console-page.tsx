// Copyright 2026 DataInfra-RedactionEverything Contributors

import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Download, Search, ShieldAlert } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { EmptyState } from '@/components/EmptyState';
import { showToast } from '@/components/Toast';
import { useAuth } from '@/features/auth/auth-context';
import { useT } from '@/i18n';
import { queryKeys } from '@/lib/query-keys';
import { downloadFile } from '@/services/api-client';
import {
  adminUserFileDownloadUrl,
  fetchAdminUserFiles,
  fetchAdminUsers,
  type AdminUserFile,
  type AdminUserSummary,
} from '@/services/adminApi';
import { localizeErrorMessage } from '@/utils/localizeError';
import { cn } from '@/lib/utils';

const PAGE_SIZE = 20;

export function ConsolePage() {
  const t = useT();
  const { status } = useAuth();
  const [filter, setFilter] = useState('');
  const [selectedUser, setSelectedUser] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const usersQuery = useQuery<AdminUserSummary[]>({
    queryKey: queryKeys.admin.users(),
    queryFn: fetchAdminUsers,
  });

  const filesQuery = useQuery({
    queryKey: queryKeys.admin.userFiles(selectedUser ?? '', page),
    queryFn: () => fetchAdminUserFiles(selectedUser as string, page, PAGE_SIZE),
    enabled: Boolean(selectedUser),
  });

  if (!status?.is_super_admin) {
    return (
      <div className="saas-page flex min-h-0 min-w-0 flex-1 flex-col bg-background">
        <div className="page-shell !max-w-[min(100%,1920px)] !px-3 !py-2 sm:!px-4 sm:!py-3">
          <Alert variant="destructive">
            <AlertDescription>{t('console.forbidden')}</AlertDescription>
          </Alert>
        </div>
      </div>
    );
  }

  const users = usersQuery.data ?? [];
  const filteredUsers = filter.trim()
    ? users.filter((u) => u.username.toLowerCase().includes(filter.trim().toLowerCase()))
    : users;

  const filesData = filesQuery.data;
  const total = filesData?.total ?? 0;
  const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const selectUser = (username: string) => {
    if (selectedUser !== username) {
      setSelectedUser(username);
      setPage(1);
    }
  };

  // 数据收缩（并行操作删文件/换用户）导致当前页越界时收敛回最后一页，
  // 避免「第 2 / 1 页」+ 误导性空态。
  useEffect(() => {
    if (filesData && page > lastPage) {
      setPage(lastPage);
    }
  }, [filesData, page, lastPage]);

  const handleDownload = async (file: AdminUserFile) => {
    if (!selectedUser) return;
    setDownloadingId(file.file_id);
    try {
      await downloadFile(
        adminUserFileDownloadUrl(selectedUser, file.file_id),
        file.original_filename || file.file_id,
      );
    } catch {
      showToast(t('console.error.download'), 'error');
    } finally {
      setDownloadingId(null);
    }
  };

  return (
    <div className="saas-page flex min-h-0 min-w-0 flex-1 flex-col bg-background">
      <div className="page-shell !max-w-[min(100%,1920px)] !px-3 !py-2 sm:!px-4 sm:!py-3">
        <div className="page-stack gap-3 overflow-hidden" data-testid="admin-console-page">
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight text-foreground">
                {t('console.title')}
              </h1>
              <p className="text-sm text-muted-foreground">{t('console.subtitle')}</p>
            </div>
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                placeholder={t('console.searchPlaceholder')}
                className="w-56 pl-9"
                data-testid="console-user-filter"
              />
            </div>
          </div>

          {usersQuery.isError && (
            <Alert variant="destructive">
              <AlertDescription>
                {localizeErrorMessage(usersQuery.error, 'console.error.loadUsers')}
              </AlertDescription>
            </Alert>
          )}

          {/* ── 用户清单 ─────────────────────────────────────────────── */}
          <section className="surface-subtle space-y-3 p-4">
            <h2 className="text-sm font-semibold text-foreground">{t('console.usersTitle')}</h2>
            <div className="overflow-hidden rounded-lg border border-border bg-background">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t('console.col.username')}</TableHead>
                    <TableHead>{t('console.col.role')}</TableHead>
                    <TableHead>{t('console.col.registeredAt')}</TableHead>
                    <TableHead>{t('console.col.status')}</TableHead>
                    <TableHead className="text-right">{t('console.col.fileCount')}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {usersQuery.isLoading &&
                    Array.from({ length: 3 }).map((_, i) => (
                      <TableRow key={`skeleton-${i}`}>
                        <TableCell colSpan={5}>
                          <Skeleton className="h-5 w-full" />
                        </TableCell>
                      </TableRow>
                    ))}
                  {filteredUsers.map((user) => (
                    <TableRow
                      key={user.username}
                      onClick={() => selectUser(user.username)}
                      data-testid={`console-user-row-${user.username}`}
                      className={cn(
                        'cursor-pointer',
                        selectedUser === user.username && 'bg-primary/5',
                      )}
                    >
                      <TableCell className="max-w-[16rem] truncate font-medium" title={user.username}>
                        {user.username}
                      </TableCell>
                      <TableCell>
                        <Badge variant={user.role === 'super_admin' ? 'default' : 'secondary'}>
                          {t(`console.role.${user.role}`)}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {formatDate(user.created_at)}
                      </TableCell>
                      <TableCell>
                        {user.disabled ? (
                          <Badge variant="destructive">{t('console.status.disabled')}</Badge>
                        ) : (
                          <Badge variant="outline">{t('console.status.active')}</Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{user.file_count}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {!usersQuery.isLoading && !usersQuery.isError && !filteredUsers.length && (
                <div className="py-6">
                  <EmptyState
                    title={t('console.empty.users')}
                    description={t('console.empty.usersHint')}
                  />
                </div>
              )}
            </div>
          </section>

          {/* ── 选中用户的文件清单 ───────────────────────────────────── */}
          <section className="surface-subtle space-y-3 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-foreground">
                {selectedUser
                  ? `${t('console.filesTitle')} · ${selectedUser}`
                  : t('console.filesTitle')}
              </h2>
              {selectedUser && Boolean(total) && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-7 px-2"
                    disabled={page <= 1}
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                  >
                    {t('console.prevPage')}
                  </Button>
                  <span className="tabular-nums">
                    {t('console.pageOf')
                      .replace('{page}', String(page))
                      .replace('{total}', String(lastPage))}
                  </span>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-7 px-2"
                    disabled={page >= lastPage}
                    onClick={() => setPage((p) => Math.min(lastPage, p + 1))}
                  >
                    {t('console.nextPage')}
                  </Button>
                </div>
              )}
            </div>

            {!selectedUser ? (
              <div className="flex items-center gap-2 rounded-lg border border-dashed border-border px-4 py-6 text-sm text-muted-foreground">
                <ShieldAlert className="size-4 shrink-0" />
                {t('console.selectUserHint')}
              </div>
            ) : filesQuery.isError ? (
              <Alert variant="destructive">
                <AlertDescription>
                  {localizeErrorMessage(filesQuery.error, 'console.error.loadFiles')}
                </AlertDescription>
              </Alert>
            ) : (
              <div className="overflow-hidden rounded-lg border border-border bg-background">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t('console.col.filename')}</TableHead>
                      <TableHead>{t('console.col.fileType')}</TableHead>
                      <TableHead>{t('console.col.fileSize')}</TableHead>
                      <TableHead>{t('console.col.uploadedAt')}</TableHead>
                      <TableHead>{t('console.col.source')}</TableHead>
                      <TableHead className="text-right">{t('console.col.actions')}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filesQuery.isLoading &&
                      Array.from({ length: 3 }).map((_, i) => (
                        <TableRow key={`file-skeleton-${i}`}>
                          <TableCell colSpan={6}>
                            <Skeleton className="h-5 w-full" />
                          </TableCell>
                        </TableRow>
                      ))}
                    {(filesData?.items ?? []).map((file) => (
                      <TableRow key={file.file_id} data-testid={`console-file-row-${file.file_id}`}>
                        <TableCell className="max-w-[22rem]">
                          <span className="flex items-center gap-2">
                            <span className="truncate" title={file.original_filename ?? file.file_id}>
                              {file.original_filename || file.file_id}
                            </span>
                            {file.has_output && (
                              <Badge variant="secondary" className="shrink-0">
                                {t('console.hasOutput')}
                              </Badge>
                            )}
                          </span>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {file.file_type || '-'}
                        </TableCell>
                        <TableCell className="text-xs tabular-nums text-muted-foreground">
                          {formatBytes(file.file_size)}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {formatDate(file.created_at)}
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline">
                            {file.upload_source === 'batch'
                              ? t('console.source.batch')
                              : t('console.source.playground')}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            className="h-7 gap-1 px-2 text-xs"
                            disabled={downloadingId === file.file_id}
                            onClick={() => void handleDownload(file)}
                            data-testid={`console-download-${file.file_id}`}
                          >
                            <Download className="size-3.5" />
                            {downloadingId === file.file_id
                              ? t('console.downloading')
                              : t('console.download')}
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                {!filesQuery.isLoading && !(filesData?.items ?? []).length && (
                  <div className="py-6">
                    <EmptyState
                      title={t('console.empty.files')}
                      description={t('console.empty.filesHint')}
                    />
                  </div>
                )}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

function formatDate(value?: string | null): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const exp = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** exp;
  return `${value >= 100 || exp === 0 ? Math.round(value) : value.toFixed(1)} ${units[exp]}`;
}
