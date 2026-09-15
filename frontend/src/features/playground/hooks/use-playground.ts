// Copyright 2026 DataInfra-RedactionEverything Contributors

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { showToast } from '@/components/Toast';
import { STORAGE_KEYS } from '@/constants/storage-keys';
import { useAuth } from '@/features/auth/auth-context';
import { t } from '@/i18n';
import { useServiceHealth, type ServicesHealth } from '@/hooks/use-service-health';
import {
  getScopedStorageItem,
  removeStorageItem,
  scopedStorageKey,
  setScopedStorageItem,
} from '@/lib/storage';
import { authFetch, downloadFile } from '@/services/api-client';
import type { VersionHistoryEntry } from '@/types';
import { localizeErrorMessage } from '@/utils/localizeError';
import {
  buildDraftSnapshot,
  parseDraft,
  planResume,
  serializeDraft,
  type PlaygroundDraftSnapshot,
} from '../lib/playground-draft';
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
  const { status } = useAuth();
  const ownerKey =
    status?.authenticated && status.username ? status.username.toLowerCase() : 'anonymous';
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
  const [pseudonymMapError, setPseudonymMapError] = useState<string | null>(null);
  const [pseudonymRetryTick, setPseudonymRetryTick] = useState(0);
  // 替换模式执行过（结果页据此展示「下载化名对照表」）；对照表内容用
  // 执行响应的 entity_map（后端真实替换结果，含 coref 复用），与成品天然一致
  const [confirmedPseudonymMap, setConfirmedPseudonymMap] = useState<Record<string, string> | null>(
    null,
  );
  const pseudonymEpochRef = useRef(0);
  // 用户在映射表手动改过的原文键：重新识别刷新自动映射时不覆盖
  const pseudonymUserEditedRef = useRef<Set<string>>(new Set());
  // 上次成功拉取映射时的实体集签名：变化（重新识别）则刷新全部自动映射
  const pseudonymEntitySigRef = useRef<string>('');

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
    setSelectedTypes: recognition.setSelectedTypes,
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
  const entitySignature = useMemo(
    () => selectedEntityTexts.join('\u0000'),
    [selectedEntityTexts],
  );
  useEffect(() => {
    if (recognition.processingMode !== 'replace') return;
    if (fileCtx.isImageMode) return;
    const sigChanged = pseudonymEntitySigRef.current !== entitySignature;
    // 全部行已补齐且实体集未变化（可能含失败后手动填全的情况）时清掉残留错误
    if (missingPseudonymKeys.length === 0 && !sigChanged) {
      setPseudonymMapError(null);
      return;
    }
    const epoch = ++pseudonymEpochRef.current;
    const controller = new AbortController();
    setPseudonymMapLoading(true);
    setPseudonymMapError(null);
    const run = async () => {
      try {
        const res = await authFetch('/api/v1/redaction/preview-map', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            // 完整透传实体（含 coref_id）：化名模式后端已改为严格按原文分配，
            // coref_id 仅作透传保留（结构化等模式仍按 coref 复用），保持请求契约不变
            entities: entityCtx.entities
              .filter((e) => e.selected !== false)
              .map((e) => ({ ...e, selected: true })),
            config: { replacement_mode: 'pseudonym' },
          }),
          signal: controller.signal,
        });
        if (!res.ok) throw new Error(t('playground.pseudonymLoadFailed'));
        const data = await safeJson<{ entity_map?: Record<string, string> }>(res);
        if (epoch !== pseudonymEpochRef.current) return;
        const incoming = data.entity_map ?? {};
        pseudonymEntitySigRef.current = entitySignature;
        setPseudonymMap((current) => {
          const next = { ...current };
          for (const [key, value] of Object.entries(incoming)) {
            // 只补缺；实体集变化（重新识别）时刷新全部「非用户手改」键，
            // 使服务端化名规则升级后旧自动映射能被纠正
            if (!(key in next) || (sigChanged && !pseudonymUserEditedRef.current.has(key))) {
              next[key] = value;
            }
          }
          return next;
        });
      } catch (err) {
        if (controller.signal.aborted) return;
        if (epoch !== pseudonymEpochRef.current) return;
        setPseudonymMapError(localizeErrorMessage(err, 'playground.pseudonymLoadFailed'));
      } finally {
        if (epoch === pseudonymEpochRef.current) setPseudonymMapLoading(false);
      }
    };
    void run();
    return () => controller.abort();
  }, [
    entitySignature,
    missingPseudonymKeys.length,
    recognition.processingMode,
    fileCtx.isImageMode,
    entityCtx.entities,
    pseudonymRetryTick,
  ]);

  const retryPseudonymLoad = useCallback(() => {
    setPseudonymMapError(null);
    setPseudonymRetryTick((tick) => tick + 1);
  }, []);

  const setPseudonymReplacement = useCallback((text: string, replacement: string) => {
    pseudonymUserEditedRef.current.add(text);
    setPseudonymMap((current) => ({ ...current, [text]: replacement }));
  }, []);

  // 已选实体的范围内，不同原文映射到同一非空化名 → 冲突（警告展示用）
  const pseudonymConflicts = useMemo(() => {
    const inScope = new Set(selectedEntityTexts);
    const byReplacement = new Map<string, string[]>();
    for (const [text, replacement] of Object.entries(pseudonymMap)) {
      if (!inScope.has(text)) continue;
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
  }, [pseudonymMap, selectedEntityTexts]);

  // 注：旧版这里有「共指组内替换词不一致禁执行」门槛。后端化名分配已改为严格
  // 按原文（不同原文即使被模型误标同组也不再共享化名，见 replacement_strategy
  // 的 PSEUDONYM 分支），不存在静默覆盖，别名统一由用户直接在映射表填同一个词，
  // 该门槛随之移除。

  // 替换模式执行门槛：默认化名仍在生成、生成失败、或有已选实体的映射被清空时，
  // 不允许执行——避免成品与用户在 UI 确认的映射不一致
  const replaceUnready = useMemo(
    () =>
      recognition.processingMode === 'replace' &&
      !fileCtx.isImageMode &&
      (pseudonymMapLoading ||
        Boolean(pseudonymMapError) ||
        selectedEntityTexts.some((text) => !(pseudonymMap[text] ?? '').trim())),
    [
      recognition.processingMode,
      fileCtx.isImageMode,
      pseudonymMapLoading,
      pseudonymMapError,
      selectedEntityTexts,
      pseudonymMap,
    ],
  );

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
    // 替换模式映射未确认完（生成中/失败/有空值）不允许执行，保证成品即所见
    if (replaceUnready) {
      showToast(t('playground.pseudonymConfirmRequired'), 'info');
      return;
    }

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

      const isPseudonym = recognition.processingMode === 'replace' && !fileCtx.isImageMode;
      // 双保险：打码分支永远不透传 pseudonym（防御残留状态），回落结构化标签
      const effectiveReplacementMode = isPseudonym
        ? 'pseudonym'
        : recognition.replacementMode === 'pseudonym'
          ? 'structured'
          : recognition.replacementMode;
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
            replacement_mode: effectiveReplacementMode,
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
    replaceUnready,
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
    // 与 applyDraftSnapshot 同享防护：取消在途识别，防止 R2（重置后重跑）
    // 与手动重置路径被旧文件识别回调污染。
    fileCtx.cancelProcessing(false);
    entityCtx.cancelRerunNerText();
    imageCtx.cancelRerunNerImage();
    setResetConfirmOpen(false);
    fileCtx.setStage('upload');
    fileCtx.setFileInfo(null);
    fileCtx.setContent('');
    entityCtx.setEntities([]);
    setRedactedCount(0);
    setEntityMap({});
    setPseudonymMap({});
    pseudonymUserEditedRef.current = new Set();
    pseudonymEntitySigRef.current = '';
    setPseudonymMapLoading(false);
    setPseudonymMapError(null);
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
    removeStorageItem(scopedStorageKey(STORAGE_KEYS.PLAYGROUND_DRAFT, ownerKey));
  }, [entityCtx, fileCtx, imageCtx, ownerKey, setRecognitionProcessingMode]);

  // 会话草稿（Issue #33）：有活动文件时防抖落盘；显式重置时清除。
  // 上传新文件后本 effect 随 fileInfo 变化自然覆盖旧草稿。
  useEffect(() => {
    if (!fileCtx.fileInfo) return;
    const timer = setTimeout(() => {
      const snapshot = buildDraftSnapshot({
        stage: fileCtx.stage,
        fileInfo: fileCtx.fileInfo!,
        content: fileCtx.content,
        entities: entityCtx.entities,
        boundingBoxes: imageCtx.boundingBoxes,
        processingMode: recognition.processingMode,
        replacementMode: recognition.replacementMode,
        watermarkText: recognition.watermarkText,
        pseudonymMap,
        pseudonymUserEditedKeys: [...pseudonymUserEditedRef.current],
        confirmedPseudonymMap,
        entityMap,
        redactedCount,
        currentPage: imageCtx.currentPage,
      });
      const json = serializeDraft(snapshot);
      if (json === null) return; // 超限：放弃持久化，内存会话不受影响
      setScopedStorageItem(STORAGE_KEYS.PLAYGROUND_DRAFT, json, ownerKey);
    }, 400);
    return () => clearTimeout(timer);
  }, [
    fileCtx.fileInfo,
    fileCtx.stage,
    fileCtx.content,
    entityCtx.entities,
    imageCtx.boundingBoxes,
    imageCtx.currentPage,
    recognition.processingMode,
    recognition.replacementMode,
    recognition.watermarkText,
    pseudonymMap,
    confirmedPseudonymMap,
    entityMap,
    redactedCount,
    ownerKey,
  ]);

  // 把草稿快照整体恢复为当前会话（挂载恢复与历史页「回到现场」共用）。
  // 恢复是幂等的：undo 栈重置、dialog 态一律回到关闭，epoch 前进使在途异步结果失效。
  const applyDraftSnapshot = useCallback(
    (snapshot: PlaygroundDraftSnapshot) => {
      asyncResultEpochRef.current += 1;
      latestFileIdRef.current = snapshot.fileInfo.file_id;
      redactionAbortRef.current?.abort();
      redactionInFlightRef.current = false;
      // 取消在途识别（Provider 全局化后可跨页在途）：否则旧文件的
      // pendingFile 识别/重跑/图片检测完成后会无条件 setEntities+setStage，
      // 把旧文件实体灌进新恢复的会话（跨文件串染）。
      fileCtx.cancelProcessing(false);
      entityCtx.cancelRerunNerText();
      imageCtx.cancelRerunNerImage();
      fileCtx.setFileInfo(snapshot.fileInfo);
      fileCtx.setContent(snapshot.content);
      fileCtx.setStage(snapshot.stage);
      entityCtx.setEntities(snapshot.entities);
      entityCtx.entityHistory.reset();
      imageCtx.setBoundingBoxes(snapshot.boundingBoxes);
      imageCtx.imageHistory.reset();
      imageCtx.setCurrentPage(snapshot.currentPage);
      setEntityMap(snapshot.entityMap);
      setRedactedCount(snapshot.redactedCount);
      setRedactionVersion((version) => version + 1); // 触发 result 阶段脱敏预览图重取
      setPseudonymMap(snapshot.pseudonymMap);
      pseudonymUserEditedRef.current = new Set(snapshot.pseudonymUserEditedKeys ?? []);
      pseudonymEntitySigRef.current = '';
      setPseudonymMapLoading(false);
      setPseudonymMapError(null);
      setConfirmedPseudonymMap(snapshot.confirmedPseudonymMap);
      // 顺序约束：setReplacementMode 对非 'pseudonym' 值会连带置 processingMode='mask'，
      // 故必须先调它、最后调 setProcessingMode，否则替换模式会话会被恢复成打码模式。
      recognition.setReplacementMode(snapshot.replacementMode);
      recognition.setProcessingMode(snapshot.processingMode);
      recognition.setWatermarkText(snapshot.watermarkText);
      setResetConfirmOpen(false);
      setReportOpen(false);
      setVersionHistoryOpen(false);
      // result 阶段恢复时报告/版本历史不入草稿，需要重取（失败静默回落）。
      // 守卫用 latestFileIdRef 而非 canApplyAsyncResult：file_id effect
      // （[fileCtx.fileInfo?.file_id]）在恢复时必然再 bump epoch，epoch 守卫
      // 恒 false 会击穿应用；改判「本会话仍是这个文件」——恢复时 ref 已指向
      // 目标文件，响应返回即应用；用户又切走则 ref 已变 → 丢弃陈旧响应。
      if (snapshot.stage === 'result') {
        const resultFileId = snapshot.fileInfo.file_id;
        const applyResult = async <T,>(
          url: string,
          apply: (data: T) => void,
          fallback: () => void,
        ) => {
          try {
            const res = await authFetch(url);
            if (!res.ok) throw new Error(String(res.status));
            const data = await safeJson<T>(res);
            if (latestFileIdRef.current === resultFileId) apply(data);
          } catch {
            if (latestFileIdRef.current === resultFileId) fallback();
          }
        };
        void applyResult<Record<string, unknown>>(
          `/api/v1/redaction/${resultFileId}/report`,
          setRedactionReport,
          () => setRedactionReport(null),
        );
        void applyResult<{ versions?: VersionHistoryEntry[] }>(
          `/api/v1/redaction/${resultFileId}/versions`,
          (data) => setVersionHistory(data.versions || []),
          () => setVersionHistory([]),
        );
      }
    },
    [entityCtx, fileCtx, imageCtx, recognition],
  );

  // 挂载恢复（R1）：每个应用生命周期只做一次；幂等，StrictMode 双挂载无害。
  // URL 带 ?file_id=（历史页「回到现场」的明确意图）时跳过：避免先展示旧草稿现场
  // 再弹「切换处理文件」确认框——用户点 A 却先看到 B，观感即「跳错文件」。
  const draftRestoreDoneRef = useRef(false);
  useEffect(() => {
    if (draftRestoreDoneRef.current) return;
    draftRestoreDoneRef.current = true;
    if (new URLSearchParams(window.location.search).get('file_id')) return;
    const snapshot = parseDraft(
      getScopedStorageItem<string | null>(STORAGE_KEYS.PLAYGROUND_DRAFT, null, ownerKey),
    );
    if (!snapshot) return;
    applyDraftSnapshot(snapshot);
    showToast(t('playground.restored'), 'info');
  }, [applyDraftSnapshot, ownerKey]);

  // 历史页「回到现场」入口（R2 + 草稿恢复）：?file_id= 协议统一走这里。
  // 有本文件的草稿 → 直接恢复现场；无草稿 → 重置后按当前识别配置重新识别。
  const resumeFromFile = useCallback(
    async (targetFileId: string) => {
      const snapshot = parseDraft(
        getScopedStorageItem<string | null>(STORAGE_KEYS.PLAYGROUND_DRAFT, null, ownerKey),
      );
      const decision = planResume({ targetFileId, snapshot });
      if (decision.mode === 'unavailable') return;
      if (decision.mode === 'draft') {
        applyDraftSnapshot(decision.snapshot);
        showToast(t('playground.restored'), 'info');
        return;
      }
      // R2：无草稿（或草稿属于其他文件）→ 重置后恢复：命中服务端识别缓存则秒回
      // （loadExistingFile 内 toast「已从服务端恢复」），未识别过才走识别 loading——
      // 不在此处预告文案，避免「未找到现场」+「已从服务端恢复」双 toast 矛盾
      performReset();
      await fileCtx.loadExistingFile(decision.fileId);
    },
    [applyDraftSnapshot, fileCtx, ownerKey, performReset],
  );

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

  // 化名对照表 csv（替换模式执行成功后可用）：用执行响应的 entity_map
  // （后端真实替换结果，含 coref 复用）生成，与成品天然一致
  const handleDownloadPseudonymCsv = useCallback(() => {
    if (!fileCtx.fileInfo || !confirmedPseudonymMap) return;
    const csv = buildPseudonymCsv(entityCtx.entities, entityMap, {
      headers: [
        t('playground.pseudonymCsvColOriginal'),
        t('playground.pseudonymCsvColType'),
        t('playground.pseudonymCsvColReplacement'),
        t('playground.pseudonymCsvColCount'),
      ],
      typeLabel: (type) => recognition.getTypeConfig(type)?.name ?? type,
    });
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const base = fileCtx.fileInfo.filename.replace(/\.[^.]+$/, '');
    triggerDownload(blob, `${t('playground.pseudonymCsvFilePrefix')}_${base}.csv`);
  }, [confirmedPseudonymMap, entityCtx.entities, entityMap, fileCtx.fileInfo, recognition]);

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
    pseudonymMapError,
    retryPseudonymLoad,
    replaceUnready,
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
    resumeFromFile,
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
