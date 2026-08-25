import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ApiRequestError,
  isReviewQueueConflict,
} from '../src/lib/apiErrors.ts';

test('recognizes only recoverable Review queue conflicts', () => {
  assert.equal(isReviewQueueConflict(new ApiRequestError(409, 'stale', 'review_state_stale')), true);
  assert.equal(isReviewQueueConflict(new ApiRequestError(409, 'saved', 'review_unit_already_decided')), true);
  assert.equal(isReviewQueueConflict(new ApiRequestError(409, 'different', 'source_ownership_mismatch')), false);
  assert.equal(isReviewQueueConflict(new ApiRequestError(400, 'stale', 'review_state_stale')), false);
  assert.equal(isReviewQueueConflict(new Error('409: stale')), false);
});
