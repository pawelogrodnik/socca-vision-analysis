import assert from 'node:assert/strict';
import test from 'node:test';

import type { InitialIdentityAuditObservation } from '../src/types.ts';
import {
  secondHalfObservationLabel,
  secondHalfSuggestionSourceLabel,
  secondHalfTeamClass,
  secondHalfVisibleSuggestion,
} from '../src/utils/secondHalfIdentityReanchorPresentation.ts';

const observation: InitialIdentityAuditObservation = {
  observation_key: 'frame-1:tracklet-1',
  bbox_xyxy: [10, 20, 30, 60],
  team_label: 'A',
  role: 'field_player',
  provenance: {},
  display_order: 0,
  suggested_player: {
    player_id: 'player-piotrek',
    player_name: 'Piotrek',
    team_label: 'A',
    suggestion_source: 'h1_safe_lineage',
  },
};

test('maps automatic team labels to stable overlay classes', () => {
  assert.equal(secondHalfTeamClass('A'), 'team-a');
  assert.equal(secondHalfTeamClass('B'), 'team-b');
  assert.equal(secondHalfTeamClass('U'), 'team-unknown');
});

test('marks a player name as a hypothesis on the bbox label', () => {
  assert.equal(
    secondHalfObservationLabel(observation, 1, false),
    '1 · Piotrek?',
  );
  assert.equal(
    secondHalfObservationLabel(observation, 1, true),
    '✓ 1 · Piotrek?',
  );
});

test('explains the evidence behind the name suggestion', () => {
  assert.equal(
    secondHalfSuggestionSourceLabel('h1_safe_lineage'),
    'Ciągłość trackletu od potwierdzenia H1',
  );
  assert.equal(
    secondHalfSuggestionSourceLabel('cross_analysis_reid_top3_advisory'),
    'Porównanie wyglądu ReID',
  );
});

test('hides a Team A name suggestion on a Team B observation', () => {
  const teamBObservation: InitialIdentityAuditObservation = {
    ...observation,
    team_label: 'B',
  };

  assert.equal(secondHalfVisibleSuggestion(teamBObservation), null);
  assert.equal(
    secondHalfObservationLabel(teamBObservation, 8, false),
    '8',
  );
});
