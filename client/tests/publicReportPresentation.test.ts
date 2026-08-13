import assert from 'node:assert/strict';
import test from 'node:test';

import {
  displayJerseyNumber,
  hasAdvancedPlayerMetrics,
  hasPlayerReadyMomentum,
  playerBelongsToPublicReportTeam,
  publicReportPlayersForTeam,
  publicReportTeamKey,
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

test('published player sections can be filtered by canonical team id', () => {
  const teams = [
    { team_id: 'corgi', team_label: 'A', team_name: 'Corgi' },
    { team_id: 'verisk', team_label: 'B', team_name: 'Verisk' },
  ] as PublicMatchReport['teams'];
  const players = [
    { player_id: 'pawel', player_name: 'Paweł', team_id: 'corgi', team_label: 'A' },
    { player_id: 'player-3', player_name: '3', team_id: 'verisk', team_label: 'B' },
  ] as PublicMatchReport['players'];

  assert.equal(publicReportTeamKey(teams[0], 0), 'id:corgi');
  assert.deepEqual(
    publicReportPlayersForTeam(players, teams[1]).map((player) => player.player_id),
    ['player-3'],
  );
});

test('published player team matching falls back to label when team id is unavailable', () => {
  const team = {
    team_id: null,
    team_label: 'B',
    team_name: 'Verisk',
  } as PublicMatchReport['teams'][number];
  const player = {
    player_id: 'player-1',
    player_name: '1',
    team_id: null,
    team_label: 'b',
  } as PublicMatchReport['players'][number];

  assert.equal(playerBelongsToPublicReportTeam(player, team), true);
  assert.equal(publicReportTeamKey(team, 1), 'label:b');
});
