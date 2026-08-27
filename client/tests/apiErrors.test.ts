import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ApiRequestError,
  isRecoverableConcurrentLaneConflict,
  isRecoverableReviewQueueConflict,
} from '../src/lib/apiErrors.ts';
import { request } from '../src/api.ts';

test('recognizes exactly the safe Review queue recovery conflicts', () => {
  for (const code of [
    'review_state_stale',
    'review_unit_already_decided',
    'review_unit_not_actionable',
    'review_queue_stale',
    'review_target_stale',
  ]) {
    assert.equal(isRecoverableReviewQueueConflict(new ApiRequestError(409, code, code)), true, code);
  }

  assert.equal(isRecoverableReviewQueueConflict(new ApiRequestError(409, 'ownership', 'source_ownership_mismatch')), false);
  assert.equal(isRecoverableReviewQueueConflict(new ApiRequestError(409, 'scope', 'invalid_action_scope')), false);
  assert.equal(isRecoverableReviewQueueConflict(new ApiRequestError(400, 'stale', 'review_state_stale')), false);
  assert.equal(isRecoverableReviewQueueConflict(new Error('409: stale')), false);
});

test('recognizes only explicit concurrent lane refresh conflicts', () => {
  for (const code of [
    'mixed_player_case_stale',
    'review_target_stale',
    'material_continuity_target_stale',
    'concurrent_lane_topology_stale',
    'concurrent_lane_set_stale',
    'concurrent_lane_source_stale',
    'concurrent_lane_target_stale',
    'concurrent_lane_resolution_conflict',
  ]) {
    assert.equal(isRecoverableConcurrentLaneConflict(new ApiRequestError(409, code, code)), true, code);
  }

  assert.equal(isRecoverableConcurrentLaneConflict(new ApiRequestError(409, 'ownership', 'source_ownership_mismatch')), false);
  assert.equal(isRecoverableConcurrentLaneConflict(new ApiRequestError(409, 'scope', 'invalid_action_scope')), false);
  assert.equal(isRecoverableConcurrentLaneConflict(new ApiRequestError(400, 'stale', 'concurrent_lane_set_stale')), false);
  assert.equal(isRecoverableConcurrentLaneConflict(new Error('409: stale')), false);
});

test('request preserves structured FastAPI conflict code', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(
    JSON.stringify({
      detail: {
        code: 'review_queue_stale',
        message: 'Kolejka Review zmieniła się.',
      },
    }),
    {
      status: 409,
      headers: { 'Content-Type': 'application/json' },
    },
  );

  try {
    await assert.rejects(
      request('/api/test-review-conflict'),
      (error: unknown) => error instanceof ApiRequestError
        && error.status === 409
        && error.code === 'review_queue_stale',
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
