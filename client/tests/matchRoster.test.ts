import assert from 'node:assert/strict';
import test from 'node:test';

import type { Player, Team } from '../src/types.ts';
import { selectedMatchRosterReadiness } from '../src/utils/matchRoster.ts';

function player(name: string, id = name.toLocaleLowerCase()): Player {
  return { id, name, role: 'player', is_guest: false };
}

function team(name: string, id: string, players: Player[]): Team {
  return { id, name, players };
}

const corgi = team('Corgi', 'team-a', [player('Pawel')]);
const verisk = team('Verisk', 'team-b', [player('Roman')]);

test('missing Team A is rejected', () => {
  assert.equal(selectedMatchRosterReadiness(undefined, verisk).code, 'missing_team_a');
});

test('missing Team B is rejected', () => {
  assert.equal(selectedMatchRosterReadiness(corgi, undefined).code, 'missing_team_b');
});

test('the same team cannot be selected for both sides', () => {
  assert.equal(selectedMatchRosterReadiness(corgi, { ...corgi }).code, 'duplicate_teams');
});

test('Team A must contain a valid roster player', () => {
  assert.equal(
    selectedMatchRosterReadiness(team('Corgi', 'team-a', []), verisk).code,
    'empty_team_a_roster',
  );
});

test('Team B must contain a valid roster player', () => {
  assert.equal(
    selectedMatchRosterReadiness(corgi, team('Verisk', 'team-b', [])).code,
    'empty_team_b_roster',
  );
});

test('different Team A and Team B rosters are ready', () => {
  assert.deepEqual(selectedMatchRosterReadiness(corgi, verisk), {
    ready: true,
    code: null,
    message: null,
  });
});

test('team-stats-only opponent does not require named roster players', () => {
  assert.equal(
    selectedMatchRosterReadiness(corgi, team('Verisk', 'team-b', []), {
      teams: { A: 'complete_roster', B: 'team_stats_only' },
    }).ready,
    true,
  );
});

test('complete-roster focus team still requires a named roster player', () => {
  assert.equal(
    selectedMatchRosterReadiness(team('Corgi', 'team-a', []), verisk, {
      teams: { A: 'complete_roster', B: 'team_stats_only' },
    }).code,
    'empty_team_a_roster',
  );
});
