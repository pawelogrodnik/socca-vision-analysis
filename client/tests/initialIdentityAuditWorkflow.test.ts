import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import type {
  InitialIdentityAuditDocument,
  InitialIdentityAuditSeedStoreDocument,
  ReviewWorkflow,
} from '../src/types.ts';
import {
  buildInitialAuditIncompleteFinalizeEvidence,
  initialAuditFinalizeOutcome,
  initialAuditIdentityWorkIsComplete,
  initialAuditPendingRequiredKeys,
} from '../src/utils/initialIdentityAuditWorkflow.ts';

function workflow(
  phase: string,
  initialAudit?: ReviewWorkflow['initial_audit'],
): ReviewWorkflow {
  return {
    schema_version: '1.0.0',
    match_id: 'match-1',
    available: true,
    phase,
    status: 'action_required',
    current_step_id: 'initial_audit',
    review_complete: false,
    can_enter_report: false,
    can_publish: false,
    steps: [],
    required_action: null,
    initial_audit: initialAudit,
    issues: { blocking: 0, important: 0, optional: 0 },
    freshness: {
      reviewed_identity_current: true,
      reviewed_stats_current: false,
      reviewed_output_current: false,
      qa_approval_current: false,
    },
    blockers: [],
    allowed_actions: phase === 'initial_audit' ? ['identify_players'] : [],
  };
}

const auditDocument: InitialIdentityAuditDocument = {
  schema_version: '0.2.0',
  mode: 'initial_identity_audit',
  read_only: false,
  video: { fps: 30, frame_count: 300, width: 1920, height: 1080, duration_sec: 10 },
  summary: {
    selected_frames: 2,
    visible_observations: 2,
    maximum_frames: 8,
    target_actions: '8-12',
  },
  roster: [],
  frames: [
    {
      audit_frame_key: 'frame-a',
      frame_number: 30,
      time_sec: 1,
      full_frame_artifact: 'frame-a.jpg',
      thumbnail_artifact: 'frame-a-thumb.jpg',
      observations: [{
        observation_key: 'key-A',
        bbox_xyxy: [10, 10, 30, 60],
        team_label: 'A',
        role: 'field_player',
        provenance: {},
        display_order: 1,
      }],
    },
    {
      audit_frame_key: 'frame-b',
      frame_number: 180,
      time_sec: 6,
      full_frame_artifact: 'frame-b.jpg',
      thumbnail_artifact: 'frame-b-thumb.jpg',
      observations: [{
        observation_key: 'key-B',
        bbox_xyxy: [50, 10, 70, 60],
        team_label: 'B',
        role: 'field_player',
        provenance: {},
        display_order: 1,
      }],
    },
  ],
  actions: [],
  operator_contract: {
    certainty: 'certain_or_skip',
    finish_before_full_coverage: true,
    raw_coordinates_required: false,
    technical_ids_visible: false,
    decisions_persisted: true,
  },
  safety: {
    production_identity_untouched: true,
    candidate_identity_untouched: true,
    yolo_not_required: true,
    downstream_rebuild_triggered: false,
  },
};

const threeObservationAuditDocument: InitialIdentityAuditDocument = {
  ...auditDocument,
  summary: {
    ...auditDocument.summary,
    selected_frames: 3,
    visible_observations: 3,
  },
  frames: [
    ...auditDocument.frames,
    {
      audit_frame_key: 'frame-c',
      frame_number: 270,
      time_sec: 9,
      full_frame_artifact: 'frame-c.jpg',
      thumbnail_artifact: 'frame-c-thumb.jpg',
      observations: [{
        observation_key: 'key-C',
        bbox_xyxy: [90, 10, 110, 60],
        team_label: 'A',
        role: 'field_player',
        provenance: {},
        display_order: 1,
      }],
    },
  ],
};

test('audit panel stops offering identity mutations once its response leaves initial audit', () => {
  assert.equal(initialAuditIdentityWorkIsComplete(workflow('initial_audit')), false);
  assert.equal(initialAuditIdentityWorkIsComplete(workflow('exceptions')), true);

  const panel = readFileSync(
    new URL('../src/components/InitialIdentityAuditPanel.tsx', import.meta.url),
    'utf8',
  );
  assert.match(panel, /flushPendingAuditChanges/);
  assert.match(panel, /session_finished/);
  assert.match(panel, /auditIdentityWorkComplete/);
  assert.match(panel, /Wymagany audyt jest zakończony/);
  assert.match(panel, /initialAuditIdentityWorkIsComplete\(saved\.workflow\)/);
});

test('normal initial audit copy hides legacy H1, H2, and IA2 terminology', () => {
  const panel = readFileSync(
    new URL('../src/components/InitialIdentityAuditPanel.tsx', import.meta.url),
    'utf8',
  );
  assert.match(panel, /Osiągnięto limit aktywnych decyzji w szybkim audycie/);
  assert.match(panel, /Nie można zakończyć audytu — poprzedni zapis nie powiódł się/);
  assert.match(panel, /Sprawdzam wynik audytu/);
  assert.match(panel, /Szybki audyt zakończony/);
  assert.match(panel, /Audyt nie jest jeszcze zakończony/);
  assert.match(panel, /Odśwież szybki audyt/);
  assert.doesNotMatch(panel, /Audyt zakończony\. Sprawdzam/);
  assert.doesNotMatch(panel, /\bH1\b|\bH2\b|\bIA2\b/);
});

test('completed finalize outcome permits the modal to close', () => {
  const outcome = initialAuditFinalizeOutcome(
    workflow('exceptions', {
      completed: 12,
      total: 12,
      remaining: 0,
      required_case_observation_keys: ['key-A', 'key-B'],
    }),
    { 'key-A': {}, 'key-B': {} },
    auditDocument,
  );

  assert.equal(outcome.complete, true);
  assert.equal(outcome.remaining, 0);
  assert.equal(outcome.firstPendingTarget, null);
});

test('incomplete finalize outcome keeps six remaining cases actionable', () => {
  const outcome = initialAuditFinalizeOutcome(
    workflow('initial_audit', {
      completed: 6,
      total: 12,
      remaining: 6,
      required_case_observation_keys: ['key-A', 'key-B'],
    }),
    { 'key-A': {} },
    auditDocument,
  );

  assert.equal(outcome.complete, false);
  assert.equal(outcome.completed, 6);
  assert.equal(outcome.total, 12);
  assert.equal(outcome.remaining, 6);
  assert.deepEqual(outcome.pendingRequiredKeys, ['key-B']);
  assert.deepEqual(outcome.firstPendingTarget, {
    frameIndex: 1,
    observationKey: 'key-B',
  });
});

test('skip is an explicit decision and is not pending', () => {
  const pending = initialAuditPendingRequiredKeys(
    workflow('initial_audit', {
      required_case_observation_keys: ['key-A', 'key-B'],
    }),
    { 'key-A': { kind: 'skip' } },
  );

  assert.deepEqual(pending, ['key-B']);
});

test('missing required observations stay incomplete with a safe fallback', () => {
  const outcome = initialAuditFinalizeOutcome(
    workflow('initial_audit', {
      completed: 6,
      total: 12,
      remaining: 6,
      required_case_observation_keys: ['missing-key'],
    }),
    {},
    auditDocument,
  );

  assert.equal(outcome.complete, false);
  assert.equal(outcome.firstPendingTarget, null);
  assert.deepEqual(outcome.missingRequiredKeys, ['missing-key']);
});

test('incomplete finalize guidance survives a non-finalize save with stale evidence', () => {
  const authoritativeFinalizeWorkflow = workflow('initial_audit', {
    completed: 1,
    total: 3,
    remaining: 2,
    required_case_observation_keys: ['key-A', 'key-B', 'key-C'],
  });
  const decisionsAtFinalize = { 'key-A': { kind: 'skip' } };
  const preservedEvidence = buildInitialAuditIncompleteFinalizeEvidence(
    authoritativeFinalizeWorkflow,
    decisionsAtFinalize,
  );
  assert.ok(preservedEvidence);

  const finalizeOutcome = initialAuditFinalizeOutcome(
    authoritativeFinalizeWorkflow,
    decisionsAtFinalize,
    threeObservationAuditDocument,
    preservedEvidence,
  );
  assert.deepEqual(finalizeOutcome.pendingRequiredKeys, ['key-B', 'key-C']);
  assert.equal(finalizeOutcome.remaining, 2);

  const decisionsAfterNormalSave = {
    ...decisionsAtFinalize,
    'key-B': { kind: 'team_unknown', teamLabel: 'B' },
  };
  const staleIncrementalWorkflow = workflow('initial_audit');
  const outcomeAfterNormalSave = initialAuditFinalizeOutcome(
    staleIncrementalWorkflow,
    decisionsAfterNormalSave,
    threeObservationAuditDocument,
    preservedEvidence,
  );

  assert.deepEqual(outcomeAfterNormalSave.pendingRequiredKeys, ['key-C']);
  assert.equal(outcomeAfterNormalSave.remaining, 1);
  assert.equal(outcomeAfterNormalSave.completed, 2);
  assert.deepEqual(outcomeAfterNormalSave.firstPendingTarget, {
    frameIndex: 2,
    observationKey: 'key-C',
  });
});

test('refresh uses the dedicated workflow response when seed GET has no workflow', () => {
  const seedGetResponse: InitialIdentityAuditSeedStoreDocument = {
    schema_version: '1.0.0',
    mode: 'initial_identity_audit',
    status: 'fresh',
    decisions_fresh: true,
    decisions: [{
      observation_key: 'key-A',
      action: 'skip',
      team_assignment_corrected: false,
    }],
    operator_telemetry: {
      metrics: {
        audit_frames_shown: 0,
        audit_crops_clicked: 0,
        audit_actions: 1,
        active_operator_seconds: 0,
        unique_players_seeded: 0,
        team_assignments_corrected: 0,
        false_detections_marked: 0,
      },
    },
    safety: {},
  };
  const refreshedWorkflow = workflow('initial_audit', {
    completed: 1,
    total: 3,
    remaining: 2,
    required_case_observation_keys: ['key-A', 'key-B', 'key-C'],
  });
  assert.equal(seedGetResponse.workflow, undefined);

  const outcome = initialAuditFinalizeOutcome(
    refreshedWorkflow,
    { 'key-A': { kind: 'skip' } },
    threeObservationAuditDocument,
  );
  assert.equal(outcome.remaining, 2);
  assert.deepEqual(outcome.firstPendingTarget, {
    frameIndex: 1,
    observationKey: 'key-B',
  });

  const panel = readFileSync(
    new URL('../src/components/InitialIdentityAuditPanel.tsx', import.meta.url),
    'utf8',
  );
  const refreshAuditView = panel.slice(
    panel.indexOf('async function refreshAuditView'),
    panel.indexOf('function applyAction'),
  );
  assert.match(refreshAuditView, /getReviewWorkflow\(match\.id\)/);
  assert.match(refreshAuditView, /initialAuditFinalizeOutcome\(\s*nextWorkflow/);
  assert.doesNotMatch(refreshAuditView, /initialAuditFinalizeOutcome\(\s*nextStore\.workflow/);
});

test('manual refresh replaces an unresolvable old required-key baseline', () => {
  const oldWorkflow = workflow('initial_audit', {
    completed: 1,
    total: 2,
    remaining: 1,
    required_case_observation_keys: ['old-key'],
  });
  const oldEvidence = buildInitialAuditIncompleteFinalizeEvidence(oldWorkflow, {});
  assert.deepEqual(oldEvidence?.requiredKeys, ['old-key']);

  const refreshedWorkflow = workflow('initial_audit', {
    completed: 1,
    total: 2,
    remaining: 1,
    required_case_observation_keys: ['new-key'],
  });
  const refreshedDocument: InitialIdentityAuditDocument = {
    ...auditDocument,
    summary: {
      ...auditDocument.summary,
      selected_frames: 1,
      visible_observations: 1,
    },
    frames: [{
      ...auditDocument.frames[0],
      observations: [{
        ...auditDocument.frames[0].observations[0],
        observation_key: 'new-key',
      }],
    }],
  };
  const refreshedEvidence = buildInitialAuditIncompleteFinalizeEvidence(
    refreshedWorkflow,
    {},
  );
  const refreshedOutcome = initialAuditFinalizeOutcome(
    refreshedWorkflow,
    {},
    refreshedDocument,
    refreshedEvidence,
  );

  assert.deepEqual(refreshedEvidence?.requiredKeys, ['new-key']);
  assert.deepEqual(refreshedOutcome.pendingRequiredKeys, ['new-key']);
  assert.deepEqual(refreshedOutcome.firstPendingTarget, {
    frameIndex: 0,
    observationKey: 'new-key',
  });
  assert.deepEqual(refreshedOutcome.missingRequiredKeys, []);

  const panel = readFileSync(
    new URL('../src/components/InitialIdentityAuditPanel.tsx', import.meta.url),
    'utf8',
  );
  const refreshAuditView = panel.slice(
    panel.indexOf('async function refreshAuditView'),
    panel.indexOf('function applyAction'),
  );
  assert.match(
    refreshAuditView,
    /buildInitialAuditIncompleteFinalizeEvidence\(\s*nextWorkflow,\s*nextDecisions/,
  );
  assert.match(
    refreshAuditView,
    /initialAuditFinalizeOutcome\(\s*nextWorkflow,\s*nextDecisions,\s*nextDocument,\s*refreshedEvidence/,
  );
  assert.match(refreshAuditView, /setIncompleteFinalizeEvidence\(refreshedEvidence\)/);
  assert.doesNotMatch(
    refreshAuditView,
    /initialAuditFinalizeOutcome\([\s\S]*?nextDocument,\s*incompleteFinalizeEvidence/,
  );
});

test('finish synchronizes parent workflow in both outcomes and closes only when complete', () => {
  const panel = readFileSync(
    new URL('../src/components/InitialIdentityAuditPanel.tsx', import.meta.url),
    'utf8',
  );
  const finishAudit = panel.slice(
    panel.indexOf('async function finishAudit'),
    panel.indexOf('function clearSelectedDecision'),
  );

  assert.match(finishAudit, /onWorkflowChanged\?\.\(finalStore\.workflow\)/);
  assert.match(finishAudit, /if \(outcome\.complete\)/);
  assert.match(finishAudit, /setOpen\(false\)/);
  assert.match(finishAudit, /setCompletionAttempted\(true\)/);
  assert.match(finishAudit, /setFrameIndex\(incompleteOutcome\.firstPendingTarget\.frameIndex\)/);
  assert.match(finishAudit, /setSelectedObservationKey\(incompleteOutcome\.firstPendingTarget\.observationKey\)/);
  assert.match(finishAudit, /finally[\s\S]*setFinishing\(false\)/);
  assert.doesNotMatch(finishAudit, /setCurrentDecisions|frameBatcherRef\.current\.reset/);
});
