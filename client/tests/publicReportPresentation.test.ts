import assert from 'node:assert/strict';
import test from 'node:test';

import {
  displayJerseyNumber,
  hasAdvancedPlayerMetrics,
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

test('reviewed speed metrics restore the max-speed player chart option', () => {
  const players = [
    { player_id: 'p1', player_name: 'Paweł', peak_speed_kmh: 18.45 },
  ] as PublicMatchReport['players'];
  assert.equal(hasAdvancedPlayerMetrics(players), true);
  players[0].peak_speed_kmh = 0;
  assert.equal(hasAdvancedPlayerMetrics(players), false);
});
