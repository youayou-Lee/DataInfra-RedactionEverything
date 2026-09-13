// Copyright 2026 DataInfra-RedactionEverything Contributors

import { describe, expect, it } from 'vitest';

import {
  applyTextPresetFields,
  defaultConfig,
  deriveReviewConfirmed,
  isJobConfigLockedError,
  mapBackendStatus,
  mergeJobConfigIntoWizardCfg,
  sanitizeBatchReplacementMode,
} from './use-batch-wizard-utils';

describe('mapBackendStatus', () => {
  it('maps terminal and legacy statuses onto the wizard state machine', () => {
    expect(mapBackendStatus('failed')).toBe('failed');
    expect(mapBackendStatus('cancelled')).toBe('failed');
    expect(mapBackendStatus('awaiting_review')).toBe('awaiting_review');
    expect(mapBackendStatus('reviewing')).toBe('awaiting_review');
    expect(mapBackendStatus('review_approved')).toBe('review_approved');
    expect(mapBackendStatus('redacting')).toBe('redacting');
    expect(mapBackendStatus('completed')).toBe('completed');
    expect(mapBackendStatus('reviewed')).toBe('completed');
    expect(mapBackendStatus('redacted')).toBe('completed');
    expect(mapBackendStatus('exported')).toBe('completed');
    expect(mapBackendStatus('processing')).toBe('analyzing');
    expect(mapBackendStatus('parsing')).toBe('analyzing');
    expect(mapBackendStatus('ner')).toBe('analyzing');
    expect(mapBackendStatus('vision')).toBe('analyzing');
  });

  it('falls back to pending for unknown statuses', () => {
    expect(mapBackendStatus('')).toBe('pending');
    expect(mapBackendStatus('queued')).toBe('pending');
    expect(mapBackendStatus('garbage')).toBe('pending');
  });
});

describe('deriveReviewConfirmed', () => {
  it('treats completed-with-output and in-flight redaction states as confirmed', () => {
    expect(deriveReviewConfirmed({ status: 'completed' })).toBe(true);
    expect(deriveReviewConfirmed({ status: 'completed', has_output: true })).toBe(true);
    expect(deriveReviewConfirmed({ status: 'review_approved' })).toBe(true);
    expect(deriveReviewConfirmed({ status: 'redacting' })).toBe(true);
  });

  it('completed without output means the redaction was invalidated', () => {
    expect(deriveReviewConfirmed({ status: 'completed', has_output: false })).toBe(false);
    expect(deriveReviewConfirmed({ status: 'awaiting_review' })).toBe(false);
    expect(deriveReviewConfirmed({ status: 'pending' })).toBe(false);
  });
});

describe('isJobConfigLockedError', () => {
  it('recognises 409 and locked-config messages', () => {
    expect(isJobConfigLockedError({ status: 409 })).toBe(true);
    expect(isJobConfigLockedError({ message: 'Job config is locked' })).toBe(true);
    expect(isJobConfigLockedError({ detail: 'config locked after submit' })).toBe(true);
  });

  it('rejects unrelated errors and non-objects', () => {
    expect(isJobConfigLockedError(null)).toBe(false);
    expect(isJobConfigLockedError('locked')).toBe(false);
    expect(isJobConfigLockedError({ status: 500, message: 'boom' })).toBe(false);
  });
});

describe('sanitizeBatchReplacementMode（批量×化名门控）', () => {
  it('keeps non-pseudonym modes and defaults everything else to structured', () => {
    expect(sanitizeBatchReplacementMode('smart')).toBe('smart');
    expect(sanitizeBatchReplacementMode('mask')).toBe('mask');
    expect(sanitizeBatchReplacementMode('structured')).toBe('structured');
    expect(sanitizeBatchReplacementMode('pseudonym')).toBe('structured');
    expect(sanitizeBatchReplacementMode('garbage')).toBe('structured');
    expect(sanitizeBatchReplacementMode(null)).toBe('structured');
    expect(sanitizeBatchReplacementMode(undefined)).toBe('structured');
  });
});

describe('批量×化名门控的配置恢复路径', () => {
  it('mergeJobConfigIntoWizardCfg drops pseudonym from job config and keeps other modes', () => {
    const base = defaultConfig();
    expect(mergeJobConfigIntoWizardCfg(base, { replacement_mode: 'pseudonym' }).replacementMode).toBe(
      'structured',
    );
    expect(mergeJobConfigIntoWizardCfg(base, { replacement_mode: 'mask' }).replacementMode).toBe('mask');
    expect(mergeJobConfigIntoWizardCfg(base, { replacement_mode: 'garbage' }).replacementMode).toBe(
      'structured',
    );
  });

  it('applyTextPresetFields sanitizes preset replacement mode', () => {
    const fields = applyTextPresetFields(
      { kind: 'full', replacementMode: 'pseudonym', selectedEntityTypeIds: ['PERSON'] } as never,
      [],
    );
    expect(fields.replacementMode).toBe('structured');
  });
});
