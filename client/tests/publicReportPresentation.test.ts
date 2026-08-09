import assert from 'node:assert/strict';
import test from 'node:test';

import {
  displayJerseyNumber,
  hasPlayerReadyMomentum,
} from '../src/lib/publicReportPresentation.ts';
import type { PublicMatchReport } from '../src/types.ts';


test('technical roster placeholders are not presented as jersey numbers', () => {
  assert.equal(displayJerseyNumber('goalkeeper'), null);
  assert.equal(displayJerseyNumber('player'), null);
  assert.equal(displayJerseyNumber('92'), '92');
});

test('low quality momentum stays out of the player-facing report', () => {
  const report = {
    ball: {
      attacking_momentum: {
        experimental: true,
        quality: 'low',
        warnings: [],
        timeline: [{ index: 0 }],
      },
    },
  } as unknown as PublicMatchReport;
  assert.equal(hasPlayerReadyMomentum(report), false);
  report.ball!.attacking_momentum!.quality = 'medium';
  assert.equal(hasPlayerReadyMomentum(report), true);
});
