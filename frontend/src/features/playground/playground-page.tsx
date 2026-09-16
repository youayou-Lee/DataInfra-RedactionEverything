// Copyright 2026 DataInfra-RedactionEverything Contributors

import { useCallback, useEffect, useRef, useState, type FC, type ReactNode, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useT } from '@/i18n';
import { getEntityTypeName } from '@/config/entityTypes';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import ImageBBoxEditor from '@/components/ImageBBoxEditor';
import { PaginationRail } from '@/components/PaginationRail';
import { PlaygroundUpload } from './components/playground-upload';
import { PlaygroundToolbar } from './components/playground-toolbar';
import { PlaygroundEntityPanel } from './components/playground-entity-panel';
import { PlaygroundResult } from './components/playground-result';
import { PlaygroundLoading } from './components/playground-loading';
import { PlaygroundTextSelectionPopover } from './components/playground-text-selection-popover';
import { PlaygroundEntityPopover } from './components/playground-entity-popover';
import {
  usePlaygroundContext,
  usePlaygroundUIContext,
} from './playground-context';
import { needsSwitchConfirm, splitVirtualPages } from './lib/playground-draft';
import {
  isMaskAllowedForFile,
  isVisualPreviewMode,
  previewEntityHoverRingClass,
  previewEntityMarkStyle,
} from './utils';
import { buildEntityCoverageMap, buildTextSegments } from '@/utils/textRedactionSegments';

/** 超长单页文本的虚拟分页窗口大小（字符） */
const VIRTUAL_PAGE_CHAR_LIMIT = 20_000;

/** Inner component that consumes the playground context. */
const PlaygroundInner: FC = () => {
  const t = useT();
  const ctx = usePlaygroundContext();
  const ui = usePlaygroundUIContext();

  const {
    stage,
    setStage,
    fileInfo,
    content,
    isImageMode,
    entities,
    setBoundingBoxes,
    visibleBoxes,
    isLoading,
    loadingMessage,
    recognitionIssue,
    entityMap,
    redactedCount,
    processingMode,
    setProcessingMode,
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
    resetConfirmOpen,
    versionHistory,
    versionHistoryOpen,
    setVersionHistoryOpen,
    selectedCount,
    canUndo,
    canRedo,
    handleUndo,
    handleRedo,
    selectAll,
    deselectAll,
    toggleBox,
    removeEntity,
    handleRerunNer,
    handleRedact,
    cancelProcessing,
    resumeFromFile,
    handleReset,
    confirmReset,
    cancelReset,
    handleDownload,
    imageUrl,
    staticPageUrl,
    redactedImageUrl,
    redactedImageError,
    currentPage,
    setCurrentPage,
    totalPages,
    mergeVisibleBoxes,
    openPopout,
    recognition,
    imageHistory,
  } = ctx;

  const { entityTypes } = recognition;
  const visionTypes = useMemo(
    () =>
      (recognition.pipelines ?? []).flatMap((pipeline) =>
        (pipeline.types ?? []).map((type) => ({ id: type.id, name: type.name })),
      ),
    [recognition.pipelines],
  );

  // 历史页「回到现场」跳转入口（?file_id= 协议）：读到参数即恢复/重跑对应文件会话
  const [searchParams, setSearchParams] = useSearchParams();
  const resumeFileId = searchParams.get('file_id');
  const [switchConfirmTarget, setSwitchConfirmTarget] = useState<string | null>(null);
  const resumeHandledRef = useRef<string | null>(null);

  const startResume = useCallback(
    (target: string) => {
      resumeHandledRef.current = target;
      void resumeFromFile(target);
      setSearchParams({}, { replace: true }); // 清参数，防刷新/回退重复触发
    },
    [resumeFromFile, setSearchParams],
  );

  useEffect(() => {
    if (!resumeFileId || resumeHandledRef.current === resumeFileId) return;
    if (needsSwitchConfirm(fileInfo?.file_id ?? null, resumeFileId)) {
      setSwitchConfirmTarget(resumeFileId); // 已有其他会话：先确认再覆盖
      return;
    }
    startResume(resumeFileId);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- startResume 稳定引用由 useCallback 保证
  }, [resumeFileId, fileInfo?.file_id]);

  // Issue #57: 打码（MASK）模式仅对 PDF 开放；文本格式自动切回「替换」
  const maskAllowed = isMaskAllowedForFile(fileInfo?.file_type);
  useEffect(() => {
    if (!maskAllowed && processingMode === 'mask') {
      setProcessingMode('replace');
    }
  }, [maskAllowed, processingMode, setProcessingMode]);

  // Issue #66：预览范式跟随处理模式——文本型 PDF 打码=图像工作台（页面图+
  // 拉框，与扫描件一致），替换=文本范式。手拉框存于 boundingBoxes 草稿态，
  // 切换模式不清理（A 案：打码/替换各管各的，切回打码框原样恢复）。
  const visualMaskPreview =
    !isImageMode &&
    isVisualPreviewMode(fileInfo?.file_type, Boolean(fileInfo?.is_scanned), processingMode);
  const isVisualPreview = isImageMode || visualMaskPreview;

  const pagesArr = fileInfo?.pages;
  // 虚拟分页（Issue #33 验收反馈）：MinerU 转出的 markdown 单页可达数十万字符、
  // 上千实体，全文一次性渲染（且每次交互全量重建）会冻结主线程。超过阈值时按
  // 字符窗口分页，复用既有分页渲染与实体偏移机制；实体按 start 落入窗口归属。
  const virtualPages = useMemo(() => {
    if (isImageMode || content.length <= VIRTUAL_PAGE_CHAR_LIMIT) return null;
    return splitVirtualPages(content, VIRTUAL_PAGE_CHAR_LIMIT);
  }, [isImageMode, content]);
  const isVirtualPaginated = virtualPages !== null;
  const activePages: string[] | undefined = virtualPages ?? (Array.isArray(pagesArr) ? pagesArr : undefined);
  const activeTotalPages = isVirtualPaginated ? virtualPages.length : totalPages;
  const hasTextPagination =
    !isImageMode && activeTotalPages > 1 && activePages !== undefined && activePages.length === activeTotalPages;
  // 草稿恢复的 currentPage 可能超过当前文件的页数（如从 18 页文件切到更短文件），钳制到有效域
  const effectiveCurrentPage = Math.max(1, Math.min(currentPage, Math.max(1, activeTotalPages)));
  const pageStartOffset = isVirtualPaginated
    ? (effectiveCurrentPage - 1) * VIRTUAL_PAGE_CHAR_LIMIT
    : hasTextPagination
      ? activePages!.slice(0, effectiveCurrentPage - 1).reduce((sum, page) => sum + (page?.length || 0) + 2, 0)
      : 0;
  const previewContent = hasTextPagination ? (activePages![effectiveCurrentPage - 1] ?? '') : content;
  const pageFilteredEntities = isVirtualPaginated
    ? entities.filter(
        (entity) => entity.start >= pageStartOffset && entity.start < pageStartOffset + VIRTUAL_PAGE_CHAR_LIMIT,
      )
    : hasTextPagination
      ? entities.filter((entity) => Number(entity.page || 1) === effectiveCurrentPage)
      : entities;
  const previewEntities = hasTextPagination
    ? pageFilteredEntities.map((entity) => ({
        ...entity,
        start: entity.start - pageStartOffset,
        end: entity.end - pageStartOffset,
      }))
    : entities;
  const entityByText = useMemo(() => {
    return buildEntityCoverageMap(entities);
  }, [entities]);
  const previewCoverageMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const text of entityByText.keys()) {
      map[text] = text;
    }
    return map;
  }, [entityByText]);
  const previewCoverageSegments = useMemo(
    () => buildTextSegments(previewContent, previewCoverageMap),
    [previewContent, previewCoverageMap],
  );
  const previewCoverageStats = useMemo(() => {
    const stats: Record<string, { total: number; selected: number }> = {};
    for (const segment of previewCoverageSegments) {
      if (!segment.isMatch) continue;
      const entity = entities.find((candidate) => candidate.text === segment.origKey);
      if (!entity) continue;
      if (!stats[entity.type]) stats[entity.type] = { total: 0, selected: 0 };
      stats[entity.type].total += 1;
      if (entities.some((candidate) => candidate.text === segment.origKey && candidate.selected)) {
        stats[entity.type].selected += 1;
      }
    }
    return stats;
  }, [entities, previewCoverageSegments]);
  const previewCoverageTotalCount = useMemo(
    () => Object.values(previewCoverageStats).reduce((sum, item) => sum + item.total, 0),
    [previewCoverageStats],
  );
  const previewCoverageSelectedCount = useMemo(
    () => Object.values(previewCoverageStats).reduce((sum, item) => sum + item.selected, 0),
    [previewCoverageStats],
  );

  const renderMarkedContent = () => {
    if (!previewContent) {
      return <p className="text-muted-foreground">{t('playground.noContent')}</p>;
    }

    const hasCoverageMarks = previewCoverageSegments.some((segment) => segment.isMatch);
    if (hasCoverageMarks) {
      return previewCoverageSegments.map((segment, index) => {
        if (!segment.isMatch) return <span key={`coverage-text-${index}`}>{segment.text}</span>;
        const entity = entityByText.get(segment.origKey);
        if (!entity) return <span key={`coverage-text-${index}`}>{segment.text}</span>;
        const typeName = getEntityTypeName(entity.type);
        const sourceLabel =
          entity.source === 'regex'
            ? t('playground.sourceRegex')
            : entity.source === 'manual'
              ? t('playground.sourceManual')
              : t('playground.sourceAi');
        return (
          <mark
            key={`coverage-${segment.safeKey}-${segment.matchIdx}-${index}`}
            data-entity-id={entity.id}
            data-entity-occurrence-id={`occ-${segment.safeKey}-${segment.matchIdx}`}
            onClick={(event) => ui.handleEntityClick(entity, event)}
            style={previewEntityMarkStyle(entity)}
            className={`inline cursor-pointer rounded-sm px-0.5 py-[1px] transition-all hover:brightness-95 hover:ring-2 hover:ring-offset-1 hover:shadow-sm ${previewEntityHoverRingClass(entity.source)}`}
            title={`${typeName} [${sourceLabel}]`}
          >
            {segment.text}
          </mark>
        );
      });
    }

    const sortedEntities = [...previewEntities].sort((left, right) => left.start - right.start);
    const segments: ReactNode[] = [];
    let lastEnd = 0;

    sortedEntities.forEach((entity) => {
      if (entity.start < 0 || entity.end > previewContent.length) return;
      if (entity.start < lastEnd) return;

      if (entity.start > lastEnd) {
        segments.push(
          <span key={`text-${lastEnd}`}>{previewContent.slice(lastEnd, entity.start)}</span>,
        );
      }

      const typeName = getEntityTypeName(entity.type);
      const sourceLabel =
        entity.source === 'regex'
          ? t('playground.sourceRegex')
          : entity.source === 'manual'
            ? t('playground.sourceManual')
            : t('playground.sourceAi');

      segments.push(
        <mark
          key={entity.id}
          data-entity-id={entity.id}
          onClick={(event) => ui.handleEntityClick(entity, event)}
          style={previewEntityMarkStyle(entity)}
          className={`inline cursor-pointer rounded-sm px-0.5 py-[1px] transition-all hover:brightness-95 hover:ring-2 hover:ring-offset-1 hover:shadow-sm ${previewEntityHoverRingClass(entity.source)}`}
          title={`${typeName} [${sourceLabel}]`}
        >
          {previewContent.slice(entity.start, entity.end)}
        </mark>,
      );

      lastEnd = entity.end;
    });

    if (lastEnd < previewContent.length) {
      segments.push(<span key="content-end">{previewContent.slice(lastEnd)}</span>);
    }

    return segments;
  };

  return (
    <div
      className="playground-root saas-page flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-background"
      data-testid="playground"
    >
      {stage === 'upload' && (
        <div className="page-shell !max-w-[min(100%,2048px)] !px-3 !py-3 sm:!px-5 sm:!py-4 2xl:!px-8">
          <div className="page-stack gap-3 overflow-hidden">
            <section className="flex flex-none flex-wrap items-end justify-between gap-3">
              <div className="min-w-0 space-y-1">
                <span className="saas-kicker">{t('playground.upload.kicker')}</span>
                <h1 className="text-2xl font-semibold tracking-tight text-foreground">
                  {t('playground.title')}
                </h1>
                <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
                  {t('page.playground.sub')}
                </p>
              </div>
            </section>
            <PlaygroundUpload ctx={ctx} />
          </div>
        </div>
      )}

      {stage === 'preview' && (
        <div className="page-shell !max-w-[min(100%,2048px)] !px-3 !py-3 sm:!px-4 sm:!py-4 2xl:!px-6">
          <div className="grid min-h-0 flex-1 gap-3 overflow-hidden lg:grid-cols-[minmax(0,1fr)_20.75rem] xl:grid-cols-[minmax(0,1fr)_21.5rem]">
            <div className="saas-panel flex min-w-0 flex-1 flex-col overflow-hidden">
              <PlaygroundToolbar
                filename={fileInfo?.filename}
                isImageMode={isVisualPreview}
                canUndo={canUndo}
                canRedo={canRedo}
                onUndo={handleUndo}
                onRedo={handleRedo}
                onReset={handleReset}
                hintText={
                  visualMaskPreview
                    ? t('playground.previewHint.pdfMask')
                    : isImageMode
                      ? t('playground.previewHint.image')
                      : t('playground.previewHint.text')
                }
                onPopout={isVisualPreview ? openPopout : undefined}
              />

              <div
                ref={ui.contentRef}
                onMouseUp={ui.handleTextSelect}
                onKeyUp={ui.handleTextSelect}
                className="flex min-h-0 flex-1 flex-col overflow-hidden select-text"
              >
                {isVisualPreview ? (
                  <div className="flex-1 min-h-0">
                    {fileInfo && (
                      <ImageBBoxEditor
                        imageSrc={visualMaskPreview ? staticPageUrl : imageUrl}
                        boxes={visibleBoxes}
                        onBoxesChange={(nextBoxes) =>
                          setBoundingBoxes(mergeVisibleBoxes(nextBoxes))
                        }
                        onBoxesCommit={(previousBoxes, nextBoxes) => {
                          imageHistory.save(mergeVisibleBoxes(previousBoxes, nextBoxes));
                          setBoundingBoxes(mergeVisibleBoxes(nextBoxes, previousBoxes));
                        }}
                        getTypeConfig={recognition.getVisionTypeConfig}
                        viewportTopSlot={
                          totalPages > 1 ? (
                            <div className="w-full min-w-[320px]">
                              <PaginationRail
                                page={currentPage}
                                pageSize={1}
                                totalItems={totalPages}
                                totalPages={totalPages}
                                compact
                                onPageChange={(nextPage) => setCurrentPage(nextPage)}
                              />
                            </div>
                          ) : null
                        }
                      />
                    )}
                  </div>
                ) : (
                  <div className="flex min-h-0 flex-1 flex-col">
                    {hasTextPagination && (
                      <div className="flex-shrink-0 px-3 pt-2 sm:px-4">
                        <PaginationRail
                          page={effectiveCurrentPage}
                          pageSize={1}
                          totalItems={activeTotalPages}
                          totalPages={activeTotalPages}
                          compact
                          onPageChange={(nextPage) => setCurrentPage(nextPage)}
                        />
                      </div>
                    )}
                    <div ref={ui.textScrollRef} className="flex min-h-0 flex-1 overflow-auto">
                      <div className="p-4 font-[system-ui] text-sm leading-relaxed whitespace-pre-wrap">
                        {renderMarkedContent()}
                      </div>
                    </div>
                  </div>
                )}

                {!isVisualPreview && <PlaygroundTextSelectionPopover entityTypes={entityTypes} />}
                {!isVisualPreview && <PlaygroundEntityPopover />}
              </div>
            </div>

            <PlaygroundEntityPanel
              isImageMode={isImageMode}
              isLoading={isLoading}
              recognitionIssue={recognitionIssue}
              entities={pageFilteredEntities}
              mappingEntities={entities}
              entityTypes={entityTypes}
              visionTypes={visionTypes}
              visibleBoxes={visibleBoxes}
              selectedCount={selectedCount}
              displaySelectedCount={isImageMode ? undefined : previewCoverageSelectedCount}
              displayTotalCount={isImageMode ? undefined : previewCoverageTotalCount}
              displayStats={
                Object.keys(previewCoverageStats).length > 0 ? previewCoverageStats : undefined
              }
              replacementMode={recognition.replacementMode}
              setReplacementMode={recognition.setReplacementMode}
              processingMode={processingMode}
              setProcessingMode={setProcessingMode}
              maskDisabled={!maskAllowed}
              pseudonymMap={pseudonymMap}
              onPseudonymChange={setPseudonymReplacement}
              pseudonymMapLoading={pseudonymMapLoading}
              pseudonymMapError={pseudonymMapError}
              onRetryPseudonymLoad={retryPseudonymLoad}
              replaceUnready={replaceUnready}
              pseudonymConflicts={pseudonymConflicts}
              watermarkText={recognition.watermarkText}
              setWatermarkText={recognition.setWatermarkText}
              clearPlaygroundTextPresetTracking={recognition.clearPlaygroundTextPresetTracking}
              onRerunNer={handleRerunNer}
              onRedact={handleRedact}
              onSelectAll={selectAll}
              onDeselectAll={deselectAll}
              onToggleBox={toggleBox}
              onEntityClick={ui.handleEntityClick}
              onRemoveEntity={removeEntity}
            />
          </div>
        </div>
      )}

      {stage === 'result' && (
        <div className="page-shell !max-w-[min(100%,2048px)] !px-3 !py-3 sm:!px-4 sm:!py-4 2xl:!px-6">
          <PlaygroundResult
            fileInfo={fileInfo}
            content={content}
            entities={entities}
            entityMap={entityMap}
            redactedCount={redactedCount}
            redactionReport={redactionReport}
            reportOpen={reportOpen}
            setReportOpen={setReportOpen}
            versionHistory={versionHistory}
            versionHistoryOpen={versionHistoryOpen}
            setVersionHistoryOpen={setVersionHistoryOpen}
            isImageMode={isImageMode}
            imageUrl={imageUrl}
            redactedImageUrl={redactedImageUrl}
            redactedImageError={redactedImageError}
            currentPage={currentPage}
            totalPages={totalPages}
            onPageChange={setCurrentPage}
            visibleBoxes={visibleBoxes}
            visionTypes={recognition.visionTypes}
            getVisionTypeConfig={recognition.getVisionTypeConfig}
            onBackToEdit={() => setStage('preview')}
            onReset={handleReset}
            onDownload={handleDownload}
            onDownloadPseudonymCsv={confirmedPseudonymMap ? handleDownloadPseudonymCsv : undefined}
          />
        </div>
      )}

      {isLoading && (
        <PlaygroundLoading
          loadingMessage={loadingMessage}
          isImageMode={isImageMode}
          onCancel={cancelProcessing}
        />
      )}

      <ConfirmDialog
        open={resetConfirmOpen}
        title={t('playground.resetConfirmTitle')}
        message={t('playground.resetConfirmMessage')}
        confirmText={t('playground.resetConfirmCta')}
        danger
        onConfirm={confirmReset}
        onCancel={cancelReset}
      />

      <ConfirmDialog
        open={switchConfirmTarget !== null}
        title={t('playground.switchSessionTitle')}
        message={t('playground.switchSessionMessage')}
        confirmText={t('playground.switchSessionConfirm')}
        danger
        onConfirm={() => {
          const target = switchConfirmTarget;
          setSwitchConfirmTarget(null);
          if (target) startResume(target);
        }}
        onCancel={() => {
          setSwitchConfirmTarget(null);
          setSearchParams({}, { replace: true });
        }}
      />
    </div>
  );
};

/** Playground 页面——Provider 已提升到 Layout 层，切页不再丢失会话状态。 */
export const Playground: FC = () => <PlaygroundInner />;
