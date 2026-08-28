import assert from 'node:assert/strict';
import test from 'node:test';

import {
  reviewRecomputeMessage,
  teamAttributionBlockerMessage,
} from '../src/utils/reviewedIdentityBlockerPresentation';

test('explains unavailable team-attribution evidence with operator-facing counts', () => {
  const message = teamAttributionBlockerMessage({
    status: 'incomplete',
    policy_version: 'test',
    allows_finalize: false,
    roster_scope: {},
    blockers: [{
      code: 'team_attribution_residual_exceeds_tolerance',
      units: 27,
      observations: 809,
    }],
  });

  assert.equal(
    message,
    'Pozostało 809 obserwacji bez przypisanej drużyny w 27 jednostkach Review. System nie ma dla nich bezpiecznych widoków do decyzji.',
  );
});

test('does not claim more cases after recompute when the queue is empty', () => {
  assert.equal(
    reviewRecomputeMessage(0, true),
    'Review zostało przeliczone, ale nadal nie można go zakończyć: brakuje bezpiecznych widoków dla nierozstrzygniętych obserwacji.',
  );
  assert.equal(
    reviewRecomputeMessage(4, false),
    'Po przeliczeniu pozostały 4 przypadki do sprawdzenia.',
  );
});
