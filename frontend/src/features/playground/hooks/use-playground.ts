// Copyright 2026 DataInfra-RedactionEverything Contributors

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { showToast } from '@/components/Toast';
import { t } from '@/i18n';
import { useServiceHealth, type ServicesHealth } from '@/hooks/use-service-health';
import { authFetch, downloadFile } from '@/services/api-client';
import type { VersionHistoryEntry } from '@/types';
import { localizeErrorMessage } from '@/utils/localizeError';
import { safeJson, buildPseudonymCsv, triggerDownload } from '../utils';
import type { RedactionResult } from '../types';
import { usePlaygroundEntities } from './use-playground-entities';
import { usePlaygroundFile } from './use-playground-file';
import { usePlaygroundHistory } from './use-playground-history';
import { usePlaygroundImage } from './use-playground-image';
import { usePlaygroundRecognition } from './use-playground-recognition';

type ServiceKey = keyof ServicesHealth['services'];

const BLOCKING_SERVICE_STATUSES = new Set(['offline', 'degraded']);

function isServiceBlocked(health: ServicesHealth | null, key: ServiceKey) {
  const status = health?.services[key]?.status;
  return typeof status === 'string' && BLOCKING_SERVICE_STATUSES.has(status);
}

function serviceLabel(health: ServicesHealth, key: ServiceKey) {
  const service = health.services[key];
  if (!service) return String(key);
  return `${t(`health.service.${key}`)}：${t(`health.${service.status}`)}`;
}

export function usePlayground() {
  const recognition = usePlaygroundRecognition();
  const { health, checking: healthChecking } = useServiceHealth();
  const { setProcessingMode: setRecognitionProcessingMode } = recognition;

  const latestOcrHasTypesRef = useRef(recognition.selectedOcrHasTypes);
  const latestVisualFeatureTypesRef = useRef(recognition.selectedVisualFeatureTypes);
  const latestSelectedTypesRef = recognition.selectedTypesRef;
  latestOcrHasTypesRef.current = recognition.selectedOcrHasTypes;
  latestVisualFeatureTypesRef.current = recognition.selectedVisualFeatureTypes;

  const entityCtx = usePlaygroundEntities();

  const [redactionReport, setRedactionReport] = useState<Record<string, unknown> | null>(null);
  const [reportOpen, setReportOpen] = useState(false);
  const [versionHistory, setVersionHistory] = useState<VersionHistoryEntry[]>([]);
  const [versionHistoryOpen, setVersionHistoryOpen] = useState(false);
  const [redactedCount, setRedactedCount] = useState(0);
  const [entityMap, setEntityMap] = useState<Record<string, string>>({});
  const [redactionVersion, setRedactionVersion] = useState(0);
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false);
  const latestFileIdRef = useRef<string | null>(null);
  const asyncResultEpochRef = useRef(0);
  const redactionAbortRef = useRef<AbortController | null>(null);
  const redactionInFlightRef = useRef(false);

  // 化名映射确认（替换模式）：原文 → 化名。用户编辑过的行不被自动补全覆盖。
  const [pseudonymMap, setPseudonymMap] = useState<Record<string, string>>({});
  const [pseudonymMapLoading, setPseudonymMapLoading] = useState(false);
  // 执行成功后快照 + 开关：结果页据此展示「下载化名对照表」
  const [confirmedPseudonymMap, setConfirmedPseudonymMap] = useState<Record<string, string> | null>(
    null,
  );
  const pseudonymEpochRef = useRef(0);

  const getRecognitionBlocker = useCallback(
    (file: { fileType: string; isScanned: boolean; content: string }) => {
      if (!health || healthChecking) return null;

      const requiredServices = new Set<ServiceKey>();
      const isImage = file.fileType === 'image' || file.isScanned;
      if (isImage) {
        if (latestOcrHasTypesRef.current.length > 0) {
          requiredServices.add('paddle_ocr');
          requiredServices.add('has_ner');
        }
        if (latestVisualFeatureTypesRef.current.length > 0) {
          requiredServices.add('visual_features');
        }
      } else if (file.content && latestSelectedTypesRef.current.length > 0) {
        requiredServices.add('has_ner');
      }

      const blocked = [...requiredServices].filter((key) => isServiceBlocked(health, key));
      if (blocked.length === 0) return null;

      return t('playground.recognitionPausedModelServices').replace(
        '{services}',
        blocked.map((key) => serviceLabel(health, key)).join(', '),
      );
    },
    [health, healthChecking, latestSelectedTypesRef],
  );

  const fileCtx = usePlaygroundFile({
    latestOcrHasTypesRef,
    latestVisualFeatureTypesRef,
    latestSelectedTypesRef,
    resetEntityHistory: entityCtx.entityHistory.reset,
    resetImageHistory: () => imageCtx.imageHistory.reset(),
    setEntities: entityCtx.setEntities,
    setBoundingBoxes: (val) => imageCtx.setBoundingBoxes(val),
    getRecognitionBlocker,
  });

  const imageCtx = usePlaygroundImage({
    fileInfo: fileCtx.fileInfo,
    redactionVersion,
    showRedactedPreview: fileCtx.stage === 'result',
  });

  const { setTypeTab } = recognition;
  useEffect(() => {
    setTypeTab(fileCtx.isImageMode ? 'vision' : 'text');
  }, [fileCtx.isImageMode, setTypeTab]);

  useEffect(() => {
    latestFileIdRef.current = fileCtx.fileInfo?.file_id ?? null;
    asyncResultEpochRef.current += 1;
  }, [fileCtx.fileInfo?.file_id]);

  useEffect(
    () => () => {
      redactionAbortRef.current?.abort();
    },
    [],
  );

  const allSelectedVisionTypes = useMemo(
    () => [...recognition.selectedOcrHasTypes, ...recognition.selectedVisualFeatureTypes],
    [recognition.selectedOcrHasTypes, recognition.selectedVisualFeatureTypes],
  );

  const historyCtx = usePlaygroundHistory({
    isImageMode: fileCtx.isImageMode,
    entities: entityCtx.entities,
    setEntities: entityCtx.setEntities,
    boundingBoxes: imageCtx.boundingBoxes,
    visibleBoxes: imageCtx.visibleBoxes,
    setBoundingBoxes: imageCtx.setBoundingBoxes,
    entityHistory: entityCtx.entityHistory,
    imageHistory: imageCtx.imageHistory,
    allSelectedVisionTypes,
  });

  const canApplyAsyncResult = useCallback((fileId: string, epoch: number) => {
    return latestFileIdRef.current === fileId && asyncResultEpochRef.current === epoch;
  }, []);

  // Destructured so handleRerunNer can depend on the exact fields it uses
  // instead of the whole (per-render) ctx objects.
  const { setRecognitionIssue } = fileCtx;
  const { handleRerunNerImage } = imageCtx;
  const { handleRerunNerText } = entityCtx;

  const handleRerunNer = useCallback(async () => {
    if (!fileCtx.fileInfo) return;
    const blocker = getRecognitionBlocker({
      fileType: fileCtx.fileInfo.file_type || '',
      isScanned: Boolean(fileCtx.fileInfo.is_scanned),
      content: fileCtx.content,
    });
    if (blocker) {
      setRecognitionIssue(blocker);
      showToast(blocker, 'info');
      return;
    }
    setRecognitionIssue(null);
    if (fileCtx.isImageMode) {
      await handleRerunNerImage(
        fileCtx.fileInfo.file_id,
        recognition.selectedOcrHasTypes,
        recognition.selectedVisualFeatureTypes,
        fileCtx.setIsLoading,
        fileCtx.setLoadingMessage,
      );
    } else {
      await handleRerunNerText(
        fileCtx.fileInfo.file_id,
        recognition.selectedTypesRef.current,
        fileCtx.setIsLoading,
        fileCtx.setLoadingMessage,
      );
    }
  }, [
    fileCtx.content,
    fileCtx.fileInfo,
    fileCtx.isImageMode,
    fileCtx.setIsLoading,
    fileCtx.setLoadingMessage,
    getRecognitionBlocker,
    handleRerunNerImage,
    handleRerunNerText,
    recognition.selectedOcrHasTypes,
    recognition.selectedTypesRef,
    recognition.selectedVisualFeatureTypes,
    setRecognitionIssue,
  ]);

  // 替换模式下自动补默认化名：仅对缺失的原文 key 请求 preview-map，
  // 合并时不覆盖已有（可能已被用户编辑）的行。
  const selectedEntityTexts = useMemo(
    () =>
      Array.from(
        new Set(entityCtx.entities.filter((e) => e.selected !== false).map((e) => e.text)),
      ).filter(Boolean),
    [entityCtx.entities],
  );
  const missingPseudonymKeys = useMemo(
    () => selectedEntityTexts.filter((text) => !(text in pseudonymMap)),
    [selectedEntityTexts, pseudonymMap],
  );
  useEffect(() => {
    if (recognition.processingMode !== 'replace') return;
    if (fileCtx.isImageMode) return;
    if (missingPseudonymKeys.length === 0) return;
    const epoch = ++pseudonymEpochRef.current;
    const controller = new AbortController();
    setPseudonymMapLoading(true);
    const run = async () => {
      try {
        const res = await authFetch('/api/v1/redaction/preview-map', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            entities: entityCtx.entities
              .filter((e) => e.selected !== false)
              .map((e) => ({
                id: e.id,
                text: e.text,
                type: e.type,
                start: e.start,
                end: e.end,
                page: e.page ?? 1,
                selected: true,
              })),
            config: { replacement_mode: 'pseudonym' },
          }),
          signal: controller.signal,
        });
        if (!res.ok) throw new Error('preview-map failed');
        const data = await safeJson<{ entity_map?: Record<string, string> }>(res);
        if (epoch !== pseudonymEpochRef.current) return;
        const incoming = data.entity_map ?? {};
        setPseudonymMap((current) => {
          const next = { ...current };
          for (const [key, value] of Object.entries(incoming)) {
            if (!(key in next)) next[key] = value;
          }
          return next;
        });
      } catch {
        /* 静默失败：保留已有映射，用户可重试（重新进入替换模式会再触发） */
      } finally {
        if (epoch === pseudonymEpochRef.current) setPseudonymMapLoading(false);
      }
    };
    void run();
    return () => controller.abort();
  }, [
    missingPseudonymKeys.length,
    recognition.processingMode,
    fileCtx.isImageMode,
    entityCtx.entities,
  ]);

  const setPseudonymReplacement = useCallback((text: string, replacement: string) => {
    setPseudonymMap((current) => ({ ...current, [text]: replacement }));
  }, []);

  // 不同原文映射到同一非空化名 → 冲突（警告展示用）
  const pseudonymConflicts = useMemo(() => {
    const byReplacement = new Map<string, string[]>();
    for (const [text, replacement] of Object.entries(pseudonymMap)) {
      const key = replacement.trim();
      if (!key) continue;
      const list = byReplacement.get(key) ?? [];
      list.push(text);
      byReplacement.set(key, list);
    }
    const conflicted = new Set<string>();
    for (const texts of byReplacement.values()) {
      if (texts.length > 1) texts.forEach((text) => conflicted.add(text));
    }
    return conflicted;
  }, [pseudonymMap]);

  const presetSeqRef = useRef(recognition.presetApplySeq);
  useEffect(() => {
    if (recognition.presetApplySeq === presetSeqRef.current) return;
    presetSeqRef.current = recognition.presetApplySeq;
    if (!fileCtx.fileInfo || fileCtx.isLoading) return;
    if (fileCtx.stage !== 'preview') return;
    void handleRerunNer();
  }, [
    recognition.presetApplySeq,
    fileCtx.fileInfo,
    fileCtx.isLoading,
    fileCtx.stage,
    handleRerunNer,
  ]);

  const handleRedact = useCallback(async () => {
    if (!fileCtx.fileInfo) return;
    if (redactionInFlightRef.current) return;

    redactionAbortRef.current?.abort();
    const controller = new AbortController();
    redactionAbortRef.current = controller;
    redactionInFlightRef.current = true;
    const { signal } = controller;

    const fileId = fileCtx.fileInfo.file_id;
    fileCtx.setIsLoading(true);
    fileCtx.setLoadingMessage(t('playground.redacting'));

    try {
      const selectedEntities = entityCtx.entities.filter((e) => e.selected !== false);
      const selectedBoxes = imageCtx.boundingBoxes.filter((b) => b.selected !== false);
      const requestedRedactionItemCount = fileCtx.isImageMode
        ? selectedBoxes.length
        : selectedEntities.length;

      const isPseudonym = recognition.processingMode === 'replace';
      const pseudonymReplacements: Record<string, string> = {};
      if (isPseudonym) {
        for (const entity of selectedEntities) {
          const replacement = (pseudonymMap[entity.text] ?? '').trim();
          if (replacement) pseudonymReplacements[entity.text] = replacement;
        }
      }

      const res = await authFetch('/api/v1/redaction/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          file_id: fileId,
          entities: entityCtx.entities,
          bounding_boxes: imageCtx.boundingBoxes,
          config: {
            replacement_mode: isPseudonym ? 'pseudonym' : recognition.replacementMode,
            entity_types: [],
            custom_replacements: pseudonymReplacements,
            watermark_text: recognition.watermarkText.trim() || undefined,
          },
        }),
        signal,
      });
      if (signal.aborted) return;

      if (!res.ok) throw new Error(t('playground.redactFailed'));
      const result = await safeJson<RedactionResult>(res);
      if (signal.aborted) return;
      const completedCount = requestedRedactionItemCount;
      setEntityMap(result.entity_map || {});
      setRedactedCount(completedCount);
      setConfirmedPseudonymMap(isPseudonym ? { ...pseudonymReplacements } : null);
      setRedactionVersion((version) => version + 1);
      fileCtx.setStage('result');

      latestFileIdRef.current = fileId;
      const asyncResultEpoch = asyncResultEpochRef.current + 1;
      asyncResultEpochRef.current = asyncResultEpoch;

      const loadAsyncResult = async <T>(url: string): Promise<T> => {
        const response = await authFetch(url, { signal });
        if (signal.aborted) {
          throw new DOMException('Aborted', 'AbortError');
        }
        if (!response.ok) {
          throw new Error(`Failed to load ${url}`);
        }
        return safeJson<T>(response);
      };

      loadAsyncResult<Record<string, unknown>>(`/api/v1/redaction/${fileId}/report`)
        .then((data) => {
          if (canApplyAsyncResult(fileId, asyncResultEpoch)) {
            setRedactionReport(data);
          }
        })
        .catch(() => {
          if (canApplyAsyncResult(fileId, asyncResultEpoch)) {
            setRedactionReport(null);
          }
        });

      loadAsyncResult<{ versions?: VersionHistoryEntry[] }>(`/api/v1/redaction/${fileId}/versions`)
        .then((data) => {
          if (canApplyAsyncResult(fileId, asyncResultEpoch)) {
            setVersionHistory(data.versions || []);
          }
        })
        .catch(() => {
          if (canApplyAsyncResult(fileId, asyncResultEpoch)) {
            setVersionHistory([]);
          }
        });

      showToast(
        t('playground.toast.redactDone').replace('{count}', String(completedCount)),
        'success',
      );
    } catch (err) {
      if (signal.aborted) return;
      showToast(localizeErrorMessage(err, 'playground.redactFailed'), 'error');
    } finally {
      if (redactionAbortRef.current === controller) {
        redactionAbortRef.current = null;
      }
      redactionInFlightRef.current = false;
      if (!signal.aborted) {
        fileCtx.setIsLoading(false);
        fileCtx.setLoadingMessage('');
      }
    }
  }, [
    canApplyAsyncResult,
    entityCtx.entities,
    fileCtx,
    imageCtx.boundingBoxes,
    recognition.replacementMode,
    recognition.processingMode,
    pseudonymMap,
  ]);

  const cancelProcessing = useCallback(() => {
    asyncResultEpochRef.current += 1;
    redactionAbortRef.current?.abort();
    redactionAbortRef.current = null;
    redactionInFlightRef.current = false;
    fileCtx.cancelProcessing(false);
    entityCtx.cancelRerunNerText();
    imageCtx.cancelRerunNerImage();
    fileCtx.setIsLoading(false);
    fileCtx.setLoadingMessage('');
    showToast(t('playground.cancelled'), 'info');
  }, [entityCtx, fileCtx, imageCtx]);

  const hasResetRisk = useMemo(
    () =>
      fileCtx.stage !== 'upload' ||
      fileCtx.fileInfo !== null ||
      fileCtx.content.length > 0 ||
      entityCtx.entities.length > 0 ||
      imageCtx.boundingBoxes.length > 0 ||
      redactedCount > 0 ||
      Object.keys(entityMap).length > 0 ||
      redactionReport !== null ||
      versionHistory.length > 0,
    [
      entityCtx.entities.length,
      entityMap,
      fileCtx.content.length,
      fileCtx.fileInfo,
      fileCtx.stage,
      imageCtx.boundingBoxes.length,
      redactedCount,
      redactionReport,
      versionHistory.length,
    ],
  );

  const performReset = useCallback(() => {
    asyncResultEpochRef.current += 1;
    pseudonymEpochRef.current += 1;
    latestFileIdRef.current = null;
    redactionAbortRef.current?.abort();
    redactionAbortRef.current = null;
    redactionInFlightRef.current = false;
    setResetConfirmOpen(false);
    fileCtx.setStage('upload');
    fileCtx.setFileInfo(null);
    fileCtx.setContent('');
    entityCtx.setEntities([]);
    setRedactedCount(0);
    setEntityMap({});
    setPseudonymMap({});
    setPseudonymMapLoading(false);
    setConfirmedPseudonymMap(null);
    // 新文件回到默认处理方式（打码），与"识别后默认匿名化"的既有行为一致
    setRecognitionProcessingMode('mask');
    setRedactionVersion(0);
    setRedactionReport(null);
    setReportOpen(false);
    entityCtx.entityHistory.reset();
    imageCtx.setBoundingBoxes([]);
    imageCtx.imageHistory.reset();
    setVersionHistory([]);
    setVersionHistoryOpen(false);
  }, [entityCtx, fileCtx, imageCtx, setRecognitionProcessingMode]);

  const handleReset = useCallback(() => {
    if (hasResetRisk) {
      setResetConfirmOpen(true);
      return;
    }
    performReset();
  }, [hasResetRisk, performReset]);

  const confirmReset = useCallback(() => {
    performReset();
  }, [performReset]);

  const cancelReset = useCallback(() => {
    setResetConfirmOpen(false);
  }, []);

  const handleDownload = useCallback(() => {
    if (!fileCtx.fileInfo) return;
    // Returns the promise so callers can show a busy state while fetching.
    return downloadFile(
      `/api/v1/files/${fileCtx.fileInfo.file_id}/download?redacted=true`,
      `redacted_${fileCtx.fileInfo.filename}`,
    ).catch((err) => {
      showToast(localizeErrorMessage(err, 'common.downloadFailed'), 'error');
    });
  }, [fileCtx.fileInfo]);

  // 化名对照表 csv（替换模式执行成功后可用）：前端从确认时的映射快照生成
  const handleDownloadPseudonymCsv = useCallback(() => {
    if (!fileCtx.fileInfo || !confirmedPseudonymMap) return;
    const csv = buildPseudonymCsv(entityCtx.entities, confirmedPseudonymMap);
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const base = fileCtx.fileInfo.filename.replace(/\.[^.]+$/, '');
    triggerDownload(blob, `化名对照表_${base}.csv`);
  }, [confirmedPseudonymMap, entityCtx.entities, fileCtx.fileInfo]);

  const openPopout = useCallback(() => {
    imageCtx.openPopout(recognition.visionTypes);
  }, [imageCtx, recognition.visionTypes]);

  return {
    stage: fileCtx.stage,
    setStage: fileCtx.setStage,
    fileInfo: fileCtx.fileInfo,
    content: fileCtx.content,
    isImageMode: fileCtx.isImageMode,
    entities: entityCtx.entities,
    setEntities: entityCtx.setEntities,
    applyEntities: entityCtx.applyEntities,
    boundingBoxes: imageCtx.boundingBoxes,
    setBoundingBoxes: imageCtx.setBoundingBoxes,
    visibleBoxes: imageCtx.visibleBoxes,
    isLoading: fileCtx.isLoading,
    loadingMessage: fileCtx.loadingMessage,
    uploadIssue: fileCtx.uploadIssue,
    recognitionIssue: fileCtx.recognitionIssue,
    entityMap,
    redactedCount,
    processingMode: recognition.processingMode,
    setProcessingMode: recognition.setProcessingMode,
    pseudonymMap,
    setPseudonymReplacement,
    pseudonymMapLoading,
    pseudonymConflicts,
    confirmedPseudonymMap,
    handleDownloadPseudonymCsv,
    redactionReport,
    reportOpen,
    setReportOpen,
    versionHistory,
    versionHistoryOpen,
    setVersionHistoryOpen,
    selectedCount: historyCtx.selectedCount,
    canUndo: historyCtx.canUndo,
    canRedo: historyCtx.canRedo,
    handleUndo: historyCtx.handleUndo,
    handleRedo: historyCtx.handleRedo,
    entityHistory: entityCtx.entityHistory,
    imageHistory: imageCtx.imageHistory,
    selectAll: historyCtx.selectAll,
    deselectAll: historyCtx.deselectAll,
    toggleBox: imageCtx.toggleBox,
    removeEntity: entityCtx.removeEntity,
    handleRerunNer,
    handleRedact,
    cancelProcessing,
    handleReset,
    resetConfirmOpen,
    confirmReset,
    cancelReset,
    handleDownload,
    dropzone: fileCtx.dropzone,
    imageUrl: imageCtx.imageUrl,
    redactedImageUrl: imageCtx.redactedImageUrl,
    redactedImageError: imageCtx.redactedImageError,
    currentPage: imageCtx.currentPage,
    setCurrentPage: imageCtx.setCurrentPage,
    totalPages: imageCtx.totalPages,
    mergeVisibleBoxes: imageCtx.mergeVisibleBoxes,
    openPopout,
    recognition,
  };
}
