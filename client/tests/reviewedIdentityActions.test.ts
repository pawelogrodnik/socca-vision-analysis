import assert from 'node:assert/strict';
import test from 'node:test';

import type { ReviewedCorrectionActionCapability } from '../src/types.ts';
import {
  reviewedIdentityChildActions,
  reviewedIdentityPrimaryActionCards,
} from '../src/utils/reviewedIdentityActions.ts';

const allowed: ReviewedCorrectionActionCapability = { allowed: true };
const disallowed: ReviewedCorrectionActionCapability = { allowed: false };

function allAllowed(): Partial<Record<string, ReviewedCorrectionActionCapability>> {
  return Object.fromEntries([
    'assign_roster_player',
    'assign_team',
    'mixed_players',
    'split',
    'referee',
    'false_detection',
    'team_unknown',
    'unresolved',
  ].map((action) => [action, allowed]));
}

test('required review stages mixed players and never offers a direct split card', () => {
  const cards = reviewedIdentityPrimaryActionCards(allAllowed(), 'stage');
  const actions = cards.map((card) => card.action);

  assert.ok(actions.includes('mixed_players'));
  assert.equal(cards.find((card) => card.action === 'mixed_players')?.label, 'Kilku zawodników');
  assert.ok(!actions.includes('split'));
});

test('required review hides staged mixed when server capability disallows it', () => {
  const capabilities = { ...allAllowed(), mixed_players: disallowed };
  const cards = reviewedIdentityPrimaryActionCards(capabilities, 'stage');

  assert.ok(!cards.map((card) => card.action).includes('mixed_players'));
});

test('optional MAX and Video QA offer direct split instead of staged mixed', () => {
  const cards = reviewedIdentityPrimaryActionCards(allAllowed(), 'direct');
  const actions = cards.map((card) => card.action);

  // A direct-mode source must not be promotable into staged mixed work even
  // when its capability map would allow staging.
  assert.ok(!actions.includes('mixed_players'));
  assert.ok(actions.includes('split'));
  assert.equal(cards.find((card) => card.action === 'split')?.label, 'Podziel');
  // The split action replaces the staged mixed slot in presentation order.
  assert.equal(actions.indexOf('split'), 2);
});

test('direct mode hides the split card when the source is not splittable', () => {
  const capabilities = { ...allAllowed(), split: { allowed: false, reason: 'not_enough_observations' } };
  const cards = reviewedIdentityPrimaryActionCards(capabilities, 'direct');

  assert.ok(!cards.map((card) => card.action).includes('split'));
  assert.ok(!cards.map((card) => card.action).includes('mixed_players'));
});

test('child assignment vocabulary never exposes split or staged mixed actions', () => {
  const actions = reviewedIdentityChildActions().map((card) => card.action);

  assert.ok(!actions.includes('split'));
  assert.ok(!actions.includes('mixed_players'));
});
