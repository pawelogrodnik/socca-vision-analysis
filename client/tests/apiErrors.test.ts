import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ApiRequestError,
  isRecoverableConcurrentLaneConflict,
  isRecoverableReviewQueueConflict,
  isReviewProgressStale,
} from '../src/lib/apiErrors.ts';
import { isRequestAbortError, request } from '../src/api.ts';

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

test('recognizes only the global Review-progress stale conflict for Mixed recovery', () => {
  assert.equal(isReviewProgressStale(new ApiRequestError(409, 'stale', 'review_progress_stale')), true);
  assert.equal(isReviewProgressStale(new ApiRequestError(409, 'other', 'review_queue_stale')), false);
  assert.equal(isReviewProgressStale(new ApiRequestError(400, 'stale', 'review_progress_stale')), false);
  assert.equal(isReviewProgressStale(new Error('409: review_progress_stale')), false);
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

test('request coalesces only identical pending GETs and clears them afterwards', async () => {
  const originalFetch = globalThis.fetch;
  let resolve!: (response: Response) => void;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    if (calls > 1) {
      return new Response(JSON.stringify({ ok: true }), { headers: { 'Content-Type': 'application/json' } });
    }
    return new Promise<Response>((done) => { resolve = done; });
  };
  try {
    const first = request<{ ok: boolean }>('/api/same');
    const second = request<{ ok: boolean }>('/api/same');
    assert.equal(calls, 1);
    resolve(new Response(JSON.stringify({ ok: true }), { headers: { 'Content-Type': 'application/json' } }));
    assert.deepEqual(await first, { ok: true });
    assert.deepEqual(await second, { ok: true });
    await request('/api/same');
    assert.equal(calls, 2);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('request never coalesces mutations', async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response(JSON.stringify({ ok: true }), { headers: { 'Content-Type': 'application/json' } });
  };
  try {
    await Promise.all([
      request('/api/mutation', { method: 'POST' }),
      request('/api/mutation', { method: 'POST' }),
    ]);
    assert.equal(calls, 2);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('one aborted GET consumer detaches while another shares and completes the same request', async () => {
  const originalFetch = globalThis.fetch;
  let resolveResponse!: (response: Response) => void;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Promise<Response>((resolve) => { resolveResponse = resolve; });
  };
  const firstController = new AbortController();
  const secondController = new AbortController();
  try {
    const first = request<{ ok: boolean }>('/api/shared-component-read', {
      signal: firstController.signal,
    });
    const second = request<{ ok: boolean }>('/api/shared-component-read', {
      signal: secondController.signal,
    });
    assert.equal(calls, 1);

    firstController.abort();
    await assert.rejects(first, (error: unknown) => isRequestAbortError(error));

    resolveResponse(new Response(JSON.stringify({ ok: true }), {
      headers: { 'Content-Type': 'application/json' },
    }));
    assert.deepEqual(await second, { ok: true });
    assert.equal(calls, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('aborted safe GET remains an abort instead of an operator-facing network error', async () => {
  const originalFetch = globalThis.fetch;
  const controller = new AbortController();
  let resolveResponse!: (response: Response) => void;
  globalThis.fetch = async () => new Promise<Response>((resolve) => { resolveResponse = resolve; });
  try {
    const requestPromise = request('/api/component-owned-read', { signal: controller.signal });
    controller.abort();
    await assert.rejects(
      requestPromise,
      (error: unknown) => isRequestAbortError(error),
    );
    resolveResponse(new Response(JSON.stringify({ ok: true }), {
      headers: { 'Content-Type': 'application/json' },
    }));
  } finally {
    globalThis.fetch = originalFetch;
  }
});
