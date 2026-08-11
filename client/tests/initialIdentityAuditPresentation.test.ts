import assert from 'node:assert/strict';
import test from 'node:test';

import type { InitialIdentityAuditObservation } from '../src/types.ts';
import {
  initialIdentityAuditObservationBoxClassName,
  initialIdentityAuditObservationTeam,
  initialIdentityAuditTeamClass,
} from '../src/utils/initialIdentityAudit.ts';

function observation(teamLabel: InitialIdentityAuditObservation['team_label']): InitialIdentityAuditObservation {
  return {
    observation_key: `frame-1:${teamLabel}`,
    bbox_xyxy: [10, 20, 30, 60],
    team_label: teamLabel,
    role: 'field_player',
    provenance: {},
    display_order: 1,
  };
}

test('detected team selects a deterministic bbox presentation class', () => {
  assert.equal(initialIdentityAuditObservationTeam(observation('A')), 'A');
  assert.equal(initialIdentityAuditTeamClass(observation('A')), 'team-a');
  assert.equal(initialIdentityAuditTeamClass(observation('B')), 'team-b');
  assert.equal(initialIdentityAuditTeamClass(observation('U')), 'team-unknown');
});

test('unknown or malformed detected team stays neutral', () => {
  assert.equal(initialIdentityAuditObservationTeam({ team_label: 'U' }), 'U');
  assert.equal(initialIdentityAuditObservationTeam({ team_label: null as never }), 'U');
  assert.equal(initialIdentityAuditObservationTeam({ team_label: 'unexpected' as never }), 'U');
});

test('manual decision state, selection, and detected team class coexist', () => {
  const detectedTeamA = observation('A');
  const className = initialIdentityAuditObservationBoxClassName(detectedTeamA, {
    selected: true,
    decided: true,
  });

  // A manual roster decision may identify a Team B player, but must never
  // recolor this detector-audit border away from the persisted Team A label.
  assert.equal(initialIdentityAuditTeamClass(detectedTeamA), 'team-a');
  assert.match(className, /\bteam-a\b/);
  assert.match(className, /\bselected\b/);
  assert.match(className, /\bdecided\b/);
});

test('required unresolved state is additive to the detected team color', () => {
  const className = initialIdentityAuditObservationBoxClassName(observation('B'), {
    selected: false,
    decided: false,
    requiredUnresolved: true,
  });

  assert.match(className, /\bteam-b\b/);
  assert.match(className, /\brequired-unresolved\b/);
  assert.doesNotMatch(className, /\bdecided\b/);
});
