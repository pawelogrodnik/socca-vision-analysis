import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createReviewedCorrectionContextCache,
} from '../src/utils/reviewedCorrectionContextCache.ts';

test('deduplicates concurrent correction-context loads and invalidates only stale state versions', async () => {
  let calls = 0;
  const cache = createReviewedCorrectionContextCache(async () => {
    calls += 1;
    return {
      candidate_subject_id: 'subject',
      team_label: 'A', source_team_label: 'A', effective_team_label: 'A',
      available_team_labels: ['A'], tracklet_ids: [], review_card_key: null,
      roster_options: [], slot_options: [], current_decision: null,
      semantic_decision_digest: 'digest', action_capabilities: {}, review_state_version: calls,
    };
  });
  const [first, second] = await Promise.all([
    cache.load('m1', 'subject'),
    cache.load('m1', 'subject'),
  ]);
  assert.equal(calls, 1);
  assert.equal(first.candidate_subject_id, second.candidate_subject_id);
  cache.invalidate('m1', 'subject');
  await cache.load('m1', 'subject');
  assert.equal(calls, 2);
  await cache.load('m1', 'other');
  assert.equal(calls, 3);
  cache.invalidateOlderState('m1', 3);
  await cache.load('m1', 'subject');
  assert.equal(calls, 4);
  await cache.load('m1', 'other');
  assert.equal(calls, 4);
});

test('does not restore an invalidated in-flight context after a newer request starts', async () => {
  let resolveFirst: ((value: any) => void) | undefined;
  let calls = 0;
  const cache = createReviewedCorrectionContextCache(async () => {
    calls += 1;
    if (calls === 1) {
      return new Promise((resolve) => { resolveFirst = resolve; });
    }
    return {
      candidate_subject_id: 'subject',
      team_label: 'A', source_team_label: 'A', effective_team_label: 'A',
      available_team_labels: ['A'], tracklet_ids: [], review_card_key: null,
      roster_options: [], slot_options: [], current_decision: null,
      semantic_decision_digest: 'new', action_capabilities: {}, review_state_version: 2,
    };
  });
  const first = cache.load('m1', 'subject');
  cache.invalidate('m1', 'subject');
  const latest = await cache.load('m1', 'subject');
  resolveFirst?.({
    candidate_subject_id: 'subject',
    team_label: 'A', source_team_label: 'A', effective_team_label: 'A',
    available_team_labels: ['A'], tracklet_ids: [], review_card_key: null,
    roster_options: [], slot_options: [], current_decision: null,
    semantic_decision_digest: 'old', action_capabilities: {}, review_state_version: 1,
  });
  await first;
  const cached = await cache.load('m1', 'subject');
  assert.equal(calls, 2);
  assert.equal(latest.review_state_version, 2);
  assert.equal(cached.review_state_version, 2);
});

test('structural split invalidation removes current and prefetched match contexts', async () => {
  let calls = 0;
  const cache = createReviewedCorrectionContextCache(async (_matchId, subjectId) => {
    calls += 1;
    return {
      candidate_subject_id: subjectId,
      team_label: 'A', source_team_label: 'A', effective_team_label: 'A',
      available_team_labels: ['A'], tracklet_ids: [], review_card_key: null,
      roster_options: [], slot_options: [], current_decision: null,
      semantic_decision_digest: `state-${calls}`, action_capabilities: {}, review_state_version: calls,
    };
  });
  await Promise.all([
    cache.load('m1', 'current'),
    cache.load('m1', 'prefetched-next'),
  ]);
  cache.invalidate('m1');
  const next = await cache.load('m1', 'prefetched-next');
  const current = await cache.load('m1', 'current');
  assert.equal(calls, 4);
  assert.equal(next.review_state_version, 3);
  assert.equal(current.review_state_version, 4);
});
