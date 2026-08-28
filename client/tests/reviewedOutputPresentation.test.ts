import assert from 'node:assert/strict';
import test from 'node:test';

import { formatReviewTime } from '../src/utils/reviewedOutputPresentation.ts';

test('formatReviewTime carries rounded tenths into the next minute', () => {
  assert.equal(formatReviewTime(119.96), '02:00.0');
  assert.equal(formatReviewTime(59.96), '01:00.0');
  assert.equal(formatReviewTime(119.94), '01:59.9');
});
