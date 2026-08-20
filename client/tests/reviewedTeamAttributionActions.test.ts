import assert from 'node:assert/strict';
import test from 'node:test';

import { TEAM_ATTRIBUTION_ONLY_ACTIONS } from '../src/utils/reviewedTeamAttributionActions';

test('Team-U attribution exposes only team-neutral resolution actions', () => {
  assert.deepEqual(
    TEAM_ATTRIBUTION_ONLY_ACTIONS.map((card) => card.action),
    ['referee', 'false_detection', 'team_unknown', 'unresolved'],
  );
  assert.equal(
    TEAM_ATTRIBUTION_ONLY_ACTIONS.some((card) => (
      ['assign_roster_player', 'assign_existing_slot', 'create_new_stable_player'].includes(card.action)
    )),
    false,
  );
});
