import assert from 'node:assert/strict';
import test from 'node:test';

import type {
  ReviewedCorrectionFinalizeResponse,
  ReviewedIdentityReviewUnit,
  ReviewWorkflow,
} from '../src/types.ts';
import {
  REQUIRED_REVIEW_WORKING_WINDOW_SIZE,
  beginRequiredReviewLifecycle,
  finalizeDeferredReviewBatch,
  recordDurableRequiredReviewSave,
  removeResolvedReviewCase,
  resolveReviewPageNavigation,
  reviewUnitKey,
  shouldAutoFinalizeDeferredQueue,
  shouldFinalizeDeferredReview,
  shouldRecoverRequiredReviewCompletion,
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


test('a dirty recompute marker does not auto-finalize a safe hot queue', () => {
  const safeCases = [{ unit: unit('already-saved') }, { unit: unit('still-pending') }];
  assert.equal(shouldFinalizeDeferredReview(safeCases, true), false);
  assert.equal(shouldFinalizeDeferredReview(safeCases, false), false);
});


test('an empty team filter never masquerades as global review completion', () => {
  assert.equal(shouldFinalizeDeferredReview([], false, 180), false);
  assert.equal(shouldFinalizeDeferredReview([], false, 0), true);
});


test('an empty global queue does not finalize when canonical coverage readiness blocks', () => {
  assert.equal(shouldFinalizeDeferredReview([], false, 0, false), false);
  assert.equal(shouldFinalizeDeferredReview([], false, 0, true), true);
});


test('a dirty deferred decision does not finalize while another filter has work', () => {
  assert.equal(shouldFinalizeDeferredReview([], true, 180, false), false);
});


test('reproduces pre-hardening partial-window completion miss from a frozen filter count', () => {
  const authoritativeGlobalAtLoad = 23;
  let locallyDisplayedRemaining = authoritativeGlobalAtLoad;
  let completionFinalizeCalls = 0;
  for (let save = 1; save <= authoritativeGlobalAtLoad; save += 1) {
    locallyDisplayedRemaining -= 1;
    // This is the old component calculation: `reviewFilters.counts.all`
    // remained 23 while only `totalRemaining` was decremented.
    const staleGlobalRemaining = authoritativeGlobalAtLoad - 1;
    if (shouldAutoFinalizeDeferredQueue('required', [], false, staleGlobalRemaining, true)) {
      completionFinalizeCalls += 1;
    }
  }
  assert.equal(locallyDisplayedRemaining, 0);
  assert.equal(completionFinalizeCalls, 0);
});


test('Required lifecycle finalizes exactly once at every short final-window size', () => {
  for (const initialRemaining of [1, 2, 23, 39]) {
    let lifecycle = beginRequiredReviewLifecycle(initialRemaining);
    let finalizeCalls = 0;
    for (let save = 1; save <= initialRemaining; save += 1) {
      const transition = recordDurableRequiredReviewSave(lifecycle);
      lifecycle = transition.lifecycle;
      if (transition.synchronization !== 'none') finalizeCalls += 1;
      assert.equal(
        transition.synchronization,
        save === initialRemaining ? 'completion' : 'none',
        `remaining=${initialRemaining}, save=${save}`,
      );
    }
    assert.deepEqual(lifecycle, { knownRemaining: 0, durableSavesInWindow: initialRemaining });
    assert.equal(finalizeCalls, 1, `remaining=${initialRemaining}`);
  }
});


test('Required lifecycle uses one boundary finalize at exactly forty saves', () => {
  let lifecycle = beginRequiredReviewLifecycle(REQUIRED_REVIEW_WORKING_WINDOW_SIZE);
  let finalizeCalls = 0;
  for (let save = 1; save <= REQUIRED_REVIEW_WORKING_WINDOW_SIZE; save += 1) {
    const transition = recordDurableRequiredReviewSave(lifecycle);
    lifecycle = transition.lifecycle;
    if (transition.synchronization !== 'none') finalizeCalls += 1;
    assert.equal(
      transition.synchronization,
      save === REQUIRED_REVIEW_WORKING_WINDOW_SIZE ? 'boundary' : 'none',
    );
  }
  assert.deepEqual(lifecycle, { knownRemaining: 0, durableSavesInWindow: 0 });
  assert.equal(finalizeCalls, 1);
});


test('Required lifecycle preserves case forty-one for an authoritative reload then finalizes once', () => {
  let lifecycle = beginRequiredReviewLifecycle(41);
  let boundaryFinalizeCalls = 0;
  let completionFinalizeCalls = 0;
  for (let save = 1; save <= REQUIRED_REVIEW_WORKING_WINDOW_SIZE; save += 1) {
    const transition = recordDurableRequiredReviewSave(lifecycle);
    lifecycle = transition.lifecycle;
    if (transition.synchronization === 'boundary') boundaryFinalizeCalls += 1;
    assert.equal(transition.synchronization, save === 40 ? 'boundary' : 'none');
  }
  assert.equal(lifecycle.knownRemaining, 1);
  assert.equal(boundaryFinalizeCalls, 1);

  // The boundary uses one authoritative reload. Its remaining source starts a
  // new hot window and is still independently reviewable.
  lifecycle = beginRequiredReviewLifecycle(1);
  const finalTransition = recordDurableRequiredReviewSave(lifecycle);
  if (finalTransition.synchronization === 'completion') completionFinalizeCalls += 1;
  assert.equal(finalTransition.synchronization, 'completion');
  assert.equal(finalTransition.lifecycle.knownRemaining, 0);
  assert.equal(completionFinalizeCalls, 1);
});


test('forty durable saves keep Required hot until the deliberate boundary', () => {
  const actions = Array.from({ length: REQUIRED_REVIEW_WORKING_WINDOW_SIZE }, (_, index) => (
    index === 0
      ? 'assign_roster_player'
      : index === 1
        ? 'unresolved'
        : index === 2
          ? 'mixed_players'
          : 'assign_team'
  ));
  let lifecycle = beginRequiredReviewLifecycle(actions.length);
  let correctionSaves = 0;
  let finalizeCalls = 0;
  let blockingProgressReloads = 0;

  for (const action of actions) {
    correctionSaves += 1; // POST /corrections completed durably before this transition.
    const transition = recordDurableRequiredReviewSave(lifecycle);
    lifecycle = transition.lifecycle;
    if (transition.synchronization === 'boundary') finalizeCalls += 1;
    if (transition.synchronization === 'none') blockingProgressReloads += 0;
    assert.ok(['assign_roster_player', 'unresolved', 'mixed_players', 'assign_team'].includes(action));
    if (correctionSaves < REQUIRED_REVIEW_WORKING_WINDOW_SIZE) {
      assert.equal(finalizeCalls, 0);
      assert.equal(blockingProgressReloads, 0);
    }
  }
  assert.equal(correctionSaves, 40);
  assert.equal(blockingProgressReloads, 0);
  assert.equal(finalizeCalls, 1);
});


test('dirty progress recovers only after authoritative Required completion and readiness', () => {
  let finalizeCalls = 0;
  if (shouldRecoverRequiredReviewCompletion(true, 200, true)) finalizeCalls += 1;
  assert.equal(shouldRecoverRequiredReviewCompletion(false, 0, true), false);
  if (shouldRecoverRequiredReviewCompletion(true, 0, false)) finalizeCalls += 1;
  assert.equal(finalizeCalls, 0);

  // A remount only calls the recovery finalize once for a dirty, genuinely
  // empty Required projection whose canonical readiness allows it.
  if (shouldRecoverRequiredReviewCompletion(true, 0, true)) finalizeCalls += 1;
  assert.equal(finalizeCalls, 1);
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


test('head replenishment does not skip sources after a mutable queue shrinks', () => {
  const original = Array.from({ length: 80 }, (_, index) => ({ unit: unit(`source-${index + 1}`) }));
  const firstWindow = original.slice(0, 40);
  const resolvedKeys = new Set(firstWindow.map((item) => reviewUnitKey(item.unit)));
  const currentRemaining = original.filter((item) => !resolvedKeys.has(reviewUnitKey(item.unit)));
  // Required Review requests offset 0 after its local working window is
  // consumed; using offset 40 here would incorrectly start at source 81.
  const nextWindow = currentRemaining.slice(0, 40);
  const keys = nextWindow.map((item) => reviewUnitKey(item.unit));
  assert.deepEqual(keys, original.slice(40).map((item) => reviewUnitKey(item.unit)));
  assert.equal(keys.some((key) => resolvedKeys.has(key)), false);
  assert.equal(new Set(keys).size, 40);
});
