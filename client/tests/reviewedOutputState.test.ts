import assert from 'node:assert/strict';
import test from 'node:test';

import { clearReviewedDerivedOutput } from '../src/utils/reviewedOutputState.ts';

test('finalize clears every output derived from the previous snapshot', () => {
  assert.deepEqual(clearReviewedDerivedOutput(), {
    job: null,
    stats: null,
    atTime: null,
  });
});
