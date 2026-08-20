import assert from 'node:assert/strict';
import test from 'node:test';

import {
  formatOptionalCaseCount,
  formatReviewedIdentityPercent,
  formatReviewedIdentityPercentagePoints,
} from '../src/utils/reviewedIdentityMaxPresentation.ts';

test('MAX presentation keeps sub-percent precision and clamps invalid coverage', () => {
  assert.equal(formatReviewedIdentityPercent(0.9343), '93.4%');
  assert.equal(formatReviewedIdentityPercent(1), '100.0%');
  assert.equal(formatReviewedIdentityPercent(-0.01), '0.0%');
  assert.equal(formatReviewedIdentityPercent(1.1), '100.0%');
  assert.equal(formatReviewedIdentityPercentagePoints(0.278), '+0.3 pp');
  assert.equal(formatOptionalCaseCount(1), '1 opcjonalny przypadek');
  assert.equal(formatOptionalCaseCount(5), '5 opcjonalnych przypadków');
});
