import assert from 'node:assert/strict';
import test from 'node:test';

import {
  canApplyInitialIdentityAuditAction,
  initialIdentityAuditPlayerUsedElsewhereInFrame,
  type InitialIdentityAuditAction,
  type InitialIdentityAuditDecision,
} from '../src/utils/initialIdentityAudit.ts';

const frameOneKeys = ['frame-1-a', 'frame-1-b'];
const pawel: InitialIdentityAuditAction = {
  kind: 'player',
  playerId: 'pawel',
  playerName: 'Pawel',
  teamLabel: 'A',
};

function assigned(
  observationKey: string,
): Record<string, InitialIdentityAuditDecision> {
  return {
    [observationKey]: { ...pawel, observationKey },
  };
}

test('all roster players are available when the current frame has no decisions', () => {
  assert.equal(
    initialIdentityAuditPlayerUsedElsewhereInFrame(frameOneKeys, {}, 'frame-1-b', 'pawel'),
    false,
  );
});

test('a player assigned to bbox A is unavailable for bbox B in the same frame', () => {
  assert.equal(
    initialIdentityAuditPlayerUsedElsewhereInFrame(
      frameOneKeys,
      assigned('frame-1-a'),
      'frame-1-b',
      'pawel',
    ),
    true,
  );
});

test('a player remains available for their own existing bbox assignment', () => {
  assert.equal(
    initialIdentityAuditPlayerUsedElsewhereInFrame(
      frameOneKeys,
      assigned('frame-1-a'),
      'frame-1-a',
      'pawel',
    ),
    false,
  );
});

test('clearing the decision releases the player', () => {
  const decisions = assigned('frame-1-a');
  delete decisions['frame-1-a'];
  assert.equal(
    initialIdentityAuditPlayerUsedElsewhereInFrame(
      frameOneKeys,
      decisions,
      'frame-1-b',
      'pawel',
    ),
    false,
  );
});

test('an assignment from another frame does not occupy the player', () => {
  assert.equal(
    initialIdentityAuditPlayerUsedElsewhereInFrame(
      frameOneKeys,
      assigned('frame-0-a'),
      'frame-1-b',
      'pawel',
    ),
    false,
  );
});

test('the application guard rejects a programmatic same-frame duplicate', () => {
  assert.equal(
    canApplyInitialIdentityAuditAction(
      frameOneKeys,
      assigned('frame-1-a'),
      'frame-1-b',
      pawel,
    ),
    false,
  );
});

test('generic actions are unaffected by roster-player occupancy', () => {
  const decisions = assigned('frame-1-a');
  const actions: InitialIdentityAuditAction[] = [
    { kind: 'team_unknown', teamLabel: 'A' },
    { kind: 'team_unknown', teamLabel: 'B' },
    { kind: 'referee' },
    { kind: 'false_detection' },
    { kind: 'skip' },
  ];
  for (const action of actions) {
    assert.equal(
      canApplyInitialIdentityAuditAction(
        frameOneKeys,
        decisions,
        'frame-1-b',
        action,
      ),
      true,
    );
  }
});