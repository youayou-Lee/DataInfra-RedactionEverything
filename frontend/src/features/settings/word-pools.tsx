// Copyright 2026 DataInfra-RedactionEverything Contributors

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useT } from '@/i18n';
import { showToast } from '@/components/Toast';
import { localizeErrorMessage } from '@/utils/localizeError';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { fetchRecognitionEntityTypes } from '@/services/recognition-config';
import {
  deleteWordPool,
  exportWordPools,
  fetchWordPools,
  importWordPools,
  updateWordPool,
  type WordPool,
  type WordPoolStrategy,
} from '@/services/wordPoolsApi';
import { triggerDownload } from '@/features/batch/hooks/use-batch-wizard-utils';

const STRATEGIES: WordPoolStrategy[] = ['numbered', 'cycle', 'generated'];

interface PoolDraft {
  wordsText: string;
  strategy: WordPoolStrategy;
  customMap: Array<{ orig: string; repl: string }>;
}

function draftFromPool(pool: WordPool | undefined): PoolDraft {
  return {
    wordsText: (pool?.words ?? []).join('\n'),
    strategy: pool?.strategy ?? 'numbered',
    customMap: Object.entries(pool?.custom_map ?? {}).map(([orig, repl]) => ({ orig, repl })),
  };
}

function draftToPayload(draft: PoolDraft) {
  const words = draft.wordsText
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const customMap: Record<string, string> = {};
  draft.customMap.forEach(({ orig, repl }) => {
    const key = orig.trim();
    const value = repl.trim();
    if (key && value) customMap[key] = value;
  });
  return { words, strategy: draft.strategy, custom_map: customMap };
}

function draftEqualsPool(draft: PoolDraft, pool: WordPool | undefined): boolean {
  if (!pool) return false;
  const payload = draftToPayload(draft);
  return (
    JSON.stringify(payload.words) === JSON.stringify(pool.words) &&
    payload.strategy === pool.strategy &&
    JSON.stringify(payload.custom_map) === JSON.stringify(pool.custom_map ?? {})
  );
}

export function WordPoolsSettings() {
  const t = useT();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [merged, setMerged] = useState<Record<string, WordPool>>({});
  const [overrides, setOverrides] = useState<Record<string, WordPool>>({});
  const [typeNames, setTypeNames] = useState<Record<string, string>>({});
  const [drafts, setDrafts] = useState<Record<string, PoolDraft>>({});
  const [savingTypeId, setSavingTypeId] = useState<string | null>(null);
  const [importReplace, setImportReplace] = useState(false);
  const importInputRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await fetchWordPools();
      setMerged(data.merged ?? {});
      setOverrides(data.overrides ?? {});
      setDrafts(
        Object.fromEntries(
          Object.entries(data.merged ?? {}).map(([typeId, pool]) => [typeId, draftFromPool(pool)]),
        ),
      );
    } catch (error) {
      setLoadError(localizeErrorMessage(error, 'wordPools.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    fetchRecognitionEntityTypes(false)
      .then((types) => {
        if (cancelled) return;
        const names: Record<string, string> = {};
        (types ?? []).forEach((type) => {
          names[String(type.id)] = type.name ?? String(type.id);
        });
        setTypeNames(names);
      })
      .catch(() => {
        /* 名称缺失时回退显示 type id */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const sortedTypeIds = useMemo(
    () =>
      Object.keys(merged).sort((a, b) => {
        const nameA = typeNames[a] ?? a;
        const nameB = typeNames[b] ?? b;
        return nameA.localeCompare(nameB, 'zh-Hans');
      }),
    [merged, typeNames],
  );

  const patchDraft = (typeId: string, patch: Partial<PoolDraft>) => {
    setDrafts((current) => ({
      ...current,
      [typeId]: { ...(current[typeId] ?? draftFromPool(merged[typeId])), ...patch },
    }));
  };

  const savePool = async (typeId: string) => {
    const draft = drafts[typeId];
    if (!draft) return;
    setSavingTypeId(typeId);
    try {
      const normalized = await updateWordPool(typeId, draftToPayload(draft));
      setMerged((current) => ({ ...current, [typeId]: normalized }));
      setOverrides((current) => ({ ...current, [typeId]: normalized }));
      setDrafts((current) => ({ ...current, [typeId]: draftFromPool(normalized) }));
      showToast(t('wordPools.saved'), 'success');
    } catch (error) {
      showToast(localizeErrorMessage(error, 'wordPools.saveFailed'), 'error');
    } finally {
      setSavingTypeId(null);
    }
  };

  const resetPool = async (typeId: string) => {
    setSavingTypeId(typeId);
    try {
      await deleteWordPool(typeId);
      const data = await fetchWordPools();
      setMerged(data.merged ?? {});
      setOverrides(data.overrides ?? {});
      setDrafts((current) => ({
        ...current,
        [typeId]: draftFromPool((data.merged ?? {})[typeId]),
      }));
      showToast(t('wordPools.resetDone'), 'success');
    } catch (error) {
      showToast(localizeErrorMessage(error, 'wordPools.resetFailed'), 'error');
    } finally {
      setSavingTypeId(null);
    }
  };

  const handleExport = async () => {
    try {
      const data = await exportWordPools();
      const blob = new Blob([JSON.stringify({ overrides: data }, null, 2)], {
        type: 'application/json',
      });
      triggerDownload(blob, 'word-pools.json');
    } catch (error) {
      showToast(localizeErrorMessage(error, 'wordPools.exportFailed'), 'error');
    }
  };

  const handleImportFile = async (file: File) => {
    try {
      const text = await file.text();
      const parsed = JSON.parse(text) as unknown;
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        showToast(t('wordPools.importInvalid'), 'error');
        return;
      }
      const container = parsed as Record<string, unknown>;
      const overridesPayload = (
        'overrides' in container &&
        container.overrides &&
        typeof container.overrides === 'object' &&
        !Array.isArray(container.overrides)
          ? container.overrides
          : parsed
      ) as Record<string, WordPool>;
      const entries = Object.entries(overridesPayload);
      const valid = entries.every(
        ([, pool]) =>
          pool &&
          typeof pool === 'object' &&
          !Array.isArray(pool) &&
          Array.isArray((pool as WordPool).words),
      );
      if (!entries.length || !valid) {
        showToast(t('wordPools.importInvalid'), 'error');
        return;
      }
      const result = await importWordPools(overridesPayload, !importReplace);
      const count = result.count ?? 0;
      showToast(
        t('wordPools.importDone').replace('{count}', String(count)),
        count > 0 ? 'success' : 'error',
      );
      await load();
    } catch (error) {
      showToast(localizeErrorMessage(error, 'wordPools.importFailed'), 'error');
    }
  };

  if (loading) {
    return (
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background">
        <div className="page-shell !max-w-[min(100%,1920px)] !px-3 !py-2 sm:!px-4 sm:!py-3">
          <Card className="overflow-hidden">
            <CardContent className="flex flex-col gap-3 pt-6">
              <Skeleton className="h-5 w-48" />
              <Skeleton className="h-4 w-96 max-w-full" />
              <div className="grid gap-3 md:grid-cols-2">
                <Skeleton className="h-64 w-full rounded-xl" />
                <Skeleton className="h-64 w-full rounded-xl" />
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    );
  }

  return (
    <div className="saas-page flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background">
      <div className="page-shell !max-w-[min(100%,1920px)] !px-3 !py-2 sm:!px-4 sm:!py-3">
        <div className="page-stack gap-3 overflow-hidden">
          {loadError && (
            <Alert variant="destructive">
              <AlertDescription>{loadError}</AlertDescription>
            </Alert>
          )}

          <header className="flex flex-none flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 space-y-1">
              <span className="saas-kicker">{t('nav.wordPools.sub')}</span>
              <h1 className="text-2xl font-semibold tracking-tight text-foreground">
                {t('wordPools.title')}
              </h1>
              <p className="max-w-4xl text-sm leading-6 text-muted-foreground">
                {t('wordPools.desc')}
              </p>
            </div>
            <div className="flex shrink-0 flex-wrap items-center gap-2">
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={importReplace}
                  onChange={(e) => setImportReplace(e.target.checked)}
                  className="size-3.5 accent-[var(--primary)]"
                />
                {t('wordPools.importReplace')}
              </label>
              <input
                ref={importInputRef}
                type="file"
                accept="application/json,.json"
                className="sr-only"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = '';
                  if (file) void handleImportFile(file);
                }}
              />
              <Button size="sm" variant="outline" onClick={() => importInputRef.current?.click()}>
                {t('wordPools.import')}
              </Button>
              <Button size="sm" variant="outline" onClick={() => void handleExport()}>
                {t('wordPools.export')}
              </Button>
            </div>
          </header>

          {sortedTypeIds.length === 0 && !loadError && (
            <Card>
              <CardContent className="py-8 text-center text-sm text-muted-foreground">
                {t('wordPools.empty')}
              </CardContent>
            </Card>
          )}

          <div className="grid min-h-0 gap-3 overflow-y-auto pr-0.5 md:grid-cols-2">
            {sortedTypeIds.map((typeId) => {
              const draft = drafts[typeId] ?? draftFromPool(merged[typeId]);
              const dirty = !draftEqualsPool(draft, merged[typeId]);
              const overridden = Boolean(overrides[typeId]);
              return (
                <Card
                  key={typeId}
                  className="min-h-0 rounded-xl border-border/70 !bg-white shadow-[var(--shadow-sm)]"
                  data-testid={`word-pool-card-${typeId}`}
                >
                  <CardContent className="flex flex-col gap-2.5 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-2">
                        <span className="truncate text-sm font-semibold">
                          {typeNames[typeId] ?? typeId}
                        </span>
                        <Badge
                          variant="outline"
                          className="shrink-0 rounded-full px-1.5 text-[10px]"
                        >
                          {typeId}
                        </Badge>
                      </div>
                      {overridden && (
                        <Badge
                          className="shrink-0 rounded-full px-2 text-[10px]"
                          variant="secondary"
                        >
                          {t('wordPools.customized')}
                        </Badge>
                      )}
                    </div>

                    <div className="flex flex-col gap-1.5">
                      <Label className="text-xs text-muted-foreground" htmlFor={`words-${typeId}`}>
                        {t('wordPools.words')}
                      </Label>
                      <Textarea
                        id={`words-${typeId}`}
                        value={draft.wordsText}
                        onChange={(e) => patchDraft(typeId, { wordsText: e.target.value })}
                        placeholder={t('wordPools.wordsPlaceholder')}
                        className="min-h-24 resize-y font-mono text-xs"
                        data-testid={`word-pool-words-${typeId}`}
                      />
                    </div>

                    <div className="flex flex-col gap-1.5">
                      <Label className="text-xs text-muted-foreground">
                        {t('wordPools.strategy')}
                      </Label>
                      <Select
                        value={draft.strategy}
                        onValueChange={(value: WordPoolStrategy) =>
                          patchDraft(typeId, { strategy: value })
                        }
                      >
                        <SelectTrigger
                          className="text-xs"
                          data-testid={`word-pool-strategy-${typeId}`}
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {STRATEGIES.map((strategy) => (
                            <SelectItem key={strategy} value={strategy}>
                              {t(`wordPools.strategy_${strategy}`)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>

                    <div className="flex flex-col gap-1.5">
                      <Label className="text-xs text-muted-foreground">
                        {t('wordPools.customMap')}
                      </Label>
                      <div className="flex flex-col gap-1.5">
                        {draft.customMap.map((row, index) => (
                          <div key={index} className="flex items-center gap-1.5">
                            <Input
                              value={row.orig}
                              onChange={(e) => {
                                const next = [...draft.customMap];
                                next[index] = { ...row, orig: e.target.value };
                                patchDraft(typeId, { customMap: next });
                              }}
                              placeholder={t('wordPools.customMapOrig')}
                              className="h-8 text-xs"
                            />
                            <span className="shrink-0 text-xs text-muted-foreground">→</span>
                            <Input
                              value={row.repl}
                              onChange={(e) => {
                                const next = [...draft.customMap];
                                next[index] = { ...row, repl: e.target.value };
                                patchDraft(typeId, { customMap: next });
                              }}
                              placeholder={t('wordPools.customMapRepl')}
                              className="h-8 text-xs"
                            />
                            <Button
                              size="icon"
                              variant="ghost"
                              className="size-7 shrink-0 text-muted-foreground"
                              onClick={() =>
                                patchDraft(typeId, {
                                  customMap: draft.customMap.filter((_, i) => i !== index),
                                })
                              }
                              aria-label={t('wordPools.customMapRemove')}
                            >
                              ×
                            </Button>
                          </div>
                        ))}
                        <Button
                          size="sm"
                          variant="outline"
                          className="self-start text-xs"
                          onClick={() =>
                            patchDraft(typeId, {
                              customMap: [...draft.customMap, { orig: '', repl: '' }],
                            })
                          }
                        >
                          {t('wordPools.customMapAdd')}
                        </Button>
                      </div>
                    </div>

                    <div className="mt-auto flex items-center justify-end gap-2">
                      {overridden && (
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={savingTypeId === typeId}
                          onClick={() => void resetPool(typeId)}
                        >
                          {t('wordPools.reset')}
                        </Button>
                      )}
                      <Button
                        size="sm"
                        disabled={!dirty || savingTypeId === typeId}
                        onClick={() => void savePool(typeId)}
                        data-testid={`word-pool-save-${typeId}`}
                      >
                        {savingTypeId === typeId ? t('wordPools.saving') : t('wordPools.save')}
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
