import assert from 'node:assert/strict';
import test from 'node:test';

import type {
  ReviewedCorrectionFinalizeResponse,
  ReviewedIdentityReviewUnit,
  ReviewWorkflow,
} from '../src/types.ts';
import {
  finalizeDeferredReviewBatch,
  removeResolvedReviewCase,
  resolveReviewPageNavigation,
  reviewUnitKey,
  shouldAutoFinalizeDeferredQueue,
  shouldFinalizeDeferredReview,
} from '../src/utils/identityExceptionQueue.ts';


function unit(subject: string, target?: string): ReviewedIdentityReviewUnit {
  return {
    candidate_subject_id: subject,
    review_target_id: target,
    tracklet_ids: [],
    tracklet_count: 0,
    source_team_label: 'A',
    effective_team_label: 'A',
    frame_start: null,
    frame_end: null,
    detected_frame_count: 0,
    detected_observation_count: 0,
    detected_time_sec: 0,
    current_resolution_status: 'pending_high_priority',
    priority: 'high',
    reason_codes: [],
  };
}


function workflow(phase: ReviewWorkflow['phase']): ReviewWorkflow {
  return {
    schema_version: '1.0.0',
    match_id: 'm1',
    available: true,
    phase,
    status: 'action_required',
    current_step_id: 'remaining_issues',
    review_complete: false,
    can_enter_report: false,
    can_publish: false,
    steps: [],
    required_action: null,
    issues: { blocking: phase === 'exceptions' ? 1 : 0, important: 0, optional: 0 },
    freshness: {
      reviewed_identity_current: true,
      reviewed_stats_current: false,
      reviewed_output_current: false,
      qa_approval_current: false,
    },
    blockers: [],
    allowed_actions: [],
  };
}


function finalizeResponse(phase: ReviewWorkflow['phase']): ReviewedCorrectionFinalizeResponse {
  return {
    workflow: workflow(phase),
    reviewed_identity: {} as ReviewedCorrectionFinalizeResponse['reviewed_identity'],
    review_progress: {} as ReviewedCorrectionFinalizeResponse['review_progress'],
    recompute_deferred: false,
  };
}


test('saved whole-subject and segment cases are removed by stable identity', () => {
  const cases = [{ unit: unit('A') }, { unit: unit('B', 'target-B') }, { unit: unit('C') }];
  const next = removeResolvedReviewCase(cases, 1, reviewUnitKey(cases[1].unit));
  assert.deepEqual(next.cases.map(({ unit: row }) => row.candidate_subject_id), ['A', 'C']);
  assert.equal(next.index, 1);
  assert.equal(next.cases[next.index].unit.candidate_subject_id, 'C');
  assert.equal(shouldFinalizeDeferredReview(next.cases), false);
});


test('optional Save + Next stays in optional audit and never auto-finalizes an empty queue', () => {
  const optionalCases = [{ unit: unit('optional-1') }, { unit: unit('optional-2') }];
  const next = removeResolvedReviewCase(
    optionalCases,
    0,
    reviewUnitKey(optionalCases[0].unit),
  );
  assert.deepEqual(
    next.cases.map(({ unit: row }) => row.candidate_subject_id),
    ['optional-2'],
  );
  assert.equal(shouldAutoFinalizeDeferredQueue('optional_audit', next.cases), false);
  assert.equal(shouldAutoFinalizeDeferredQueue('optional_audit', [], true), false);
  assert.equal(shouldAutoFinalizeDeferredQueue('required', []), true);
});


test('last local case triggers one finalize and advances without queue reload', async () => {
  let finalizeCalls = 0;
  let reloadCalls = 0;
  let workflowUpdates = 0;
  const result = await finalizeDeferredReviewBatch(
    async () => {
      finalizeCalls += 1;
      return finalizeResponse('ready_to_finalize');
    },
    async () => {
      reloadCalls += 1;
      return [];
    },
    () => { workflowUpdates += 1; },
  );
  assert.equal(finalizeCalls, 1);
  assert.equal(reloadCalls, 0);
  assert.equal(workflowUpdates, 1);
  assert.deepEqual(result.cases, []);
});


test('finalize with new exceptions reloads authoritative queue once', async () => {
  let reloadCalls = 0;
  const remaining = [{ unit: unit('new-case') }];
  const result = await finalizeDeferredReviewBatch(
    async () => finalizeResponse('exceptions'),
    async () => {
      reloadCalls += 1;
      return remaining;
    },
    () => undefined,
  );
  assert.equal(reloadCalls, 1);
  assert.equal(result.cases[0].unit.candidate_subject_id, 'new-case');
});


test('failed finalize can retry without recreating saved local cases', async () => {
  let calls = 0;
  const finalize = async () => {
    calls += 1;
    if (calls === 1) throw new Error('temporary');
    return finalizeResponse('ready_to_finalize');
  };
  await assert.rejects(
    finalizeDeferredReviewBatch(finalize, async () => [], () => undefined),
    /temporary/,
  );
  const retried = await finalizeDeferredReviewBatch(
    finalize,
    async () => [],
    () => undefined,
  );
  assert.equal(calls, 2);
  assert.deepEqual(retried.cases, []);
  assert.equal(shouldFinalizeDeferredReview([]), true);
});


test('reload with a dirty recompute marker finalizes before showing stale cases', () => {
  const staleCases = [{ unit: unit('already-saved') }, { unit: unit('still-pending') }];
  assert.equal(shouldFinalizeDeferredReview(staleCases, true), true);
  assert.equal(shouldFinalizeDeferredReview(staleCases, false), false);
});


test('an empty team filter never masquerades as global review completion', () => {
  assert.equal(shouldFinalizeDeferredReview([], false, 180), false);
  assert.equal(shouldFinalizeDeferredReview([], false, 0), true);
});


test('an empty global queue does not finalize when canonical coverage readiness blocks', () => {
  assert.equal(shouldFinalizeDeferredReview([], false, 0, false), false);
  assert.equal(shouldFinalizeDeferredReview([], false, 0, true), true);
});


test('a dirty deferred decision remains authoritative when switching filters', () => {
  assert.equal(shouldFinalizeDeferredReview([], true, 180, false), true);
});


test('review navigation crosses page boundaries without hiding remaining cases', () => {
  assert.deepEqual(resolveReviewPageNavigation({
    direction: 'next',
    currentIndex: 19,
    pageLength: 20,
    pageOffset: 0,
    pageSize: 20,
    hasMore: true,
  }), { kind: 'page', offset: 20, index: 0 });
  assert.deepEqual(resolveReviewPageNavigation({
    direction: 'previous',
    currentIndex: 0,
    pageLength: 20,
    pageOffset: 20,
    pageSize: 20,
    hasMore: true,
  }), { kind: 'page', offset: 0, index: 19 });
  assert.deepEqual(resolveReviewPageNavigation({
    direction: 'next',
    currentIndex: 19,
    pageLength: 20,
    pageOffset: 520,
    pageSize: 20,
    hasMore: false,
  }), { kind: 'none' });
});
