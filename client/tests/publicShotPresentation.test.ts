import assert from 'node:assert/strict';
import { test } from 'node:test';

import { hasPublicShotMapLocation, publicShotSummary } from '../src/lib/publicShotPresentation.ts';

test('public shot summary counts goals as on target and keeps blocked only in the denominator', () => {
  const summary = publicShotSummary([
    { shot_id: 'goal', time_sec: 1, team_id: 'a', outcome: 'goal' },
    { shot_id: 'on', time_sec: 2, team_id: 'a', outcome: 'on_target' },
    { shot_id: 'off', time_sec: 3, team_id: 'a', outcome: 'off_target' },
    { shot_id: 'blocked', time_sec: 4, team_id: 'a', outcome: 'blocked' },
  ]);
  assert.deepEqual(summary, { total: 4, onTarget: 2, offTarget: 1, accuracyPercent: 50 });
  assert.equal(publicShotSummary([]).accuracyPercent, null);
});

test('only finite backend-normalized map coordinates are eligible for public markers', () => {
  assert.equal(hasPublicShotMapLocation({ shot_id: 'mapped', time_sec: 1, team_id: 'a', outcome: 'goal', location_m: { x: 1, y: 2 }, map_location: { x: 0, y: 1 } }), true);
  assert.equal(hasPublicShotMapLocation({ shot_id: 'raw-only', time_sec: 1, team_id: 'a', outcome: 'goal', location_m: { x: 1, y: 2 } }), false);
  assert.equal(hasPublicShotMapLocation({ shot_id: 'invalid', time_sec: 1, team_id: 'a', outcome: 'goal', map_location: { x: 1.01, y: -.01 } }), false);
});
