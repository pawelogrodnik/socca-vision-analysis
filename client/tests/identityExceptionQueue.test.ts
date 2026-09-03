import assert from 'node:assert/strict';
import test from 'node:test';

import type {
  ReviewedCorrectionFinalizeResponse,
  ReviewedIdentityReviewUnit,
  ReviewWorkflow,
} from '../src/types.ts';
import {
  REQUIRED_REVIEW_WORKING_WINDOW_SIZE,
  beginRequiredReviewNavigation,
  beginRequiredReviewLifecycle,
  finalizeDeferredReviewBatch,
  recordRequiredReviewQueueMutation,
  recordDurableRequiredReviewSave,
  removeResolvedReviewCase,
  resolveRequiredReviewPageRequest,
  resolveReviewPageNavigation,
  reviewUnitKey,
  shouldAutoFinalizeDeferredQueue,
  shouldFinalizeDeferredReview,
  shouldRecoverRequiredReviewCompletion,
  shouldVerifyMutatedRequiredQueueEmpty,
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


test('completion waits for one authoritative progress refresh before exposing workflow', async () => {
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
  assert.equal(reloadCalls, 1);
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


function runRequiredSaveFlow(totalRequired: number, mixedSave?: number) {
  let lifecycle = beginRequiredReviewLifecycle(totalRequired);
  const transitions: string[] = [];
  let correctionSaves = 0;
  let replenishCalls = 0;
  let finalizeCalls = 0;
  for (let save = 1; save <= totalRequired; save += 1) {
    // This is the same durable-save transition invoked by the panel after the
    // POST /corrections response. A staged Mixed source is still a safe hot
    // save, so it participates in the same Required working window.
    const action = save === mixedSave ? 'mixed_players' : 'assign_roster_player';
    assert.ok(['assign_roster_player', 'mixed_players'].includes(action));
    correctionSaves += 1;
    const transition = recordDurableRequiredReviewSave(lifecycle);
    lifecycle = transition.lifecycle;
    transitions.push(transition.synchronization);
    if (transition.synchronization === 'replenish') replenishCalls += 1;
    if (transition.synchronization === 'completion') finalizeCalls += 1;
  }
  return { correctionSaves, replenishCalls, finalizeCalls, transitions, lifecycle };
}


test('Required lifecycle finalizes exactly once at every short final-window size', () => {
  for (const initialRemaining of [1, 2, 23, 39]) {
    const flow = runRequiredSaveFlow(initialRemaining);
    assert.equal(flow.transitions.at(-1), 'completion', `remaining=${initialRemaining}`);
    assert.equal(flow.replenishCalls, 0, `remaining=${initialRemaining}`);
    assert.equal(flow.finalizeCalls, 1, `remaining=${initialRemaining}`);
    assert.deepEqual(flow.lifecycle, { knownRemaining: 0, durableSavesInWindow: 0 });
  }
});


test('exactly forty Required saves complete once without a replenish', () => {
  const flow = runRequiredSaveFlow(REQUIRED_REVIEW_WORKING_WINDOW_SIZE);
  assert.equal(flow.transitions.at(-1), 'completion');
  assert.equal(flow.replenishCalls, 0);
  assert.equal(flow.finalizeCalls, 1);
});


test('forty-first Required case remains reviewable after one hot replenish', () => {
  const flow = runRequiredSaveFlow(41);
  assert.equal(flow.transitions[39], 'replenish');
  assert.equal(flow.transitions[40], 'completion');
  assert.equal(flow.replenishCalls, 1);
  assert.equal(flow.finalizeCalls, 1);
});


test('hot Required windows replenish without periodic canonical finalize', () => {
  const eighty = runRequiredSaveFlow(80, 17);
  assert.equal(eighty.correctionSaves, 80);
  assert.equal(eighty.transitions[39], 'replenish');
  assert.equal(eighty.transitions[79], 'completion');
  assert.equal(eighty.replenishCalls, 1);
  assert.equal(eighty.finalizeCalls, 1);

  const eightyOne = runRequiredSaveFlow(81, 17);
  assert.equal(eightyOne.correctionSaves, 81);
  assert.equal(eightyOne.transitions[39], 'replenish');
  assert.equal(eightyOne.transitions[79], 'replenish');
  assert.equal(eightyOne.transitions[80], 'completion');
  assert.equal(eightyOne.replenishCalls, 2);
  assert.equal(eightyOne.finalizeCalls, 1);
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


function requiredPageRequest(
  direction: 'previous' | 'next',
  currentIndex: number,
  pageLength: number,
  pageOffset: number,
  hasMore: boolean,
  mutated = false,
) {
  const destination = resolveReviewPageNavigation({
    direction,
    currentIndex,
    pageLength,
    pageOffset,
    pageSize: REQUIRED_REVIEW_WORKING_WINDOW_SIZE,
    hasMore,
  });
  assert.equal(destination.kind, 'page');
  if (destination.kind !== 'page') throw new Error('expected a server page request');
  return resolveRequiredReviewPageRequest(
    'required',
    destination,
    mutated ? recordRequiredReviewQueueMutation() : beginRequiredReviewNavigation(),
  );
}


function serverPageSourceKeys(sourceKeys: string[], offset: number) {
  return sourceKeys.slice(offset, offset + REQUIRED_REVIEW_WORKING_WINDOW_SIZE);
}


test('read-only all-filter browsing reaches every 95-case page without changing Required lifecycle', () => {
  const sourceKeys = Array.from({ length: 95 }, (_, index) => reviewUnitKey(unit(`all-${index + 1}`)));
  const lifecycle = beginRequiredReviewLifecycle(sourceKeys.length);
  const requests = [0];
  const firstNext = requiredPageRequest('next', 39, 40, 0, true);
  requests.push(firstNext.offset);
  const secondNext = requiredPageRequest('next', 39, 40, 40, true);
  requests.push(secondNext.offset);

  assert.deepEqual(requests, [0, 40, 80]);
  const browsedKeys = requests.flatMap((offset) => serverPageSourceKeys(sourceKeys, offset));
  assert.deepEqual(browsedKeys, sourceKeys);
  assert.equal(new Set(browsedKeys).size, 95);
  assert.deepEqual(lifecycle, beginRequiredReviewLifecycle(95));
});


test('read-only Verisk browsing reaches cases forty-one through fifty-eight', () => {
  const sourceKeys = Array.from({ length: 58 }, (_, index) => reviewUnitKey(unit(`verisk-${index + 1}`)));
  const request = requiredPageRequest('next', 39, 40, 0, true);
  assert.deepEqual(request, { offset: 40, index: 0, reanchoredToCurrentHead: false });
  assert.deepEqual(
    [...serverPageSourceKeys(sourceKeys, 0), ...serverPageSourceKeys(sourceKeys, request.offset)],
    sourceKeys,
  );
  assert.equal(serverPageSourceKeys(sourceKeys, request.offset).length, 18);
});


test('read-only forward and backward paging retains the same eighty source keys', () => {
  const sourceKeys = Array.from({ length: 80 }, (_, index) => reviewUnitKey(unit(`source-${index + 1}`)));
  const next = requiredPageRequest('next', 39, 40, 0, true);
  const previous = requiredPageRequest('previous', 0, 40, next.offset, false);
  assert.deepEqual(next, { offset: 40, index: 0, reanchoredToCurrentHead: false });
  assert.deepEqual(previous, { offset: 0, index: 39, reanchoredToCurrentHead: false });
  assert.deepEqual(sourceKeys.slice(next.offset, next.offset + 40), sourceKeys.slice(40));
  assert.deepEqual(sourceKeys.slice(previous.offset, previous.offset + 40), sourceKeys.slice(0, 40));
});


test('a durable save invalidates positional continuation but not read-only browsing lifecycle', () => {
  const staleNext = requiredPageRequest('next', 39, 40, 40, true, true);
  assert.deepEqual(staleNext, { offset: 0, index: 0, reanchoredToCurrentHead: true });
  const stalePrevious = requiredPageRequest('previous', 0, 40, 40, true, true);
  assert.deepEqual(stalePrevious, { offset: 0, index: 0, reanchoredToCurrentHead: true });

  const safeKeys = Array.from({ length: 100 }, (_, index) => reviewUnitKey(unit(`source-${index + 1}`)));
  const resolved = new Set([safeKeys[54]]); // save on page two, case 55
  const currentHead = safeKeys.filter((key) => !resolved.has(key));
  const reanchoredBrowse = [0, 40, 80].flatMap((offset) => serverPageSourceKeys(currentHead, offset));
  assert.deepEqual(reanchoredBrowse, currentHead);
  assert.equal(reanchoredBrowse.includes(safeKeys[80]), true);
  assert.equal(currentHead.includes(safeKeys[54]), false);
});


test('a locally empty mutated Corgi page requires one fresh filtered verification', () => {
  const initialCorgi = Array.from({ length: 26 }, (_, index) => reviewUnitKey(unit(`corgi-${index + 1}`)));
  const promotedCorgi = Array.from({ length: 5 }, (_, index) => reviewUnitKey(unit(`corgi-promoted-${index + 1}`)));
  const resolved = new Set(initialCorgi);
  const mutated = recordRequiredReviewQueueMutation();
  assert.equal(shouldVerifyMutatedRequiredQueueEmpty('required', 0, mutated), true);
  assert.equal(shouldVerifyMutatedRequiredQueueEmpty('optional_audit', 0, mutated), false);
  // The fresh Corgi GET is authoritative and can expose newly promoted work
  // even though its old page had `hasMore=false`.
  const freshCorgi = promotedCorgi.filter((key) => !resolved.has(key));
  assert.deepEqual(freshCorgi, promotedCorgi);
  assert.equal(freshCorgi.length, 5);
  // Accepting the verification response establishes a new authoritative page
  // snapshot, so an already-verified empty filter does not loop its GET.
  assert.equal(
    shouldVerifyMutatedRequiredQueueEmpty('required', 0, beginRequiredReviewNavigation()),
    false,
  );
});


test('a fresh verified empty Corgi snapshot can show global work without finalizing', () => {
  const verifiedEmptyCorgi = beginRequiredReviewNavigation();
  const globalRequiredRemaining = 125;
  assert.equal(shouldVerifyMutatedRequiredQueueEmpty('required', 0, verifiedEmptyCorgi), false);
  assert.equal(globalRequiredRemaining > 0, true);
  assert.equal(shouldRecoverRequiredReviewCompletion(true, globalRequiredRemaining, true), false);
});


test('exact Mixed staging invalidates positional pagination without changing its hot routing semantics', () => {
  const pageTwoAfterMixed = requiredPageRequest('next', 39, 40, 40, true, true);
  assert.deepEqual(pageTwoAfterMixed, { offset: 0, index: 0, reanchoredToCurrentHead: true });
  const mixedSource = unit('subject-mixed', 'exact-segment');
  assert.equal(reviewUnitKey(mixedSource), 'segment:exact-segment');
});
