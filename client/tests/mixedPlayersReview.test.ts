import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import type { MixedPlayerCase, MixedSegmentAssignment } from '../src/types.ts';
import { mixedFramesPerSecond, mixedQueueAfterSuccessfulSave, mixedSegments, mixedTimeForFrame, remapMixedAssignments, replaceMixedBoundaryInInterval, toggleMixedBoundary, validMixedResolution } from '../src/utils/mixedPlayersReview.ts';

const reviewCase: MixedPlayerCase = {
  candidate_subject_id: 'mixed-1',
  original_issue: 'mixed_players',
  mixed_hint: 'cross_team',
  resolution_status: 'unresolved',
  source_subject_digest: 'digest',
  source_tracklet_ids: ['t1'],
  observation_count: 50,
  frame_start: 10,
  frame_end: 50,
  temporal_evidence: {
    status: 'ready',
    anchor_crops: [40, 10, 20, 30, 50].map((frame) => ({
      anchor_crop_id: `c${frame}`,
      artifact: `c${frame}.jpg`,
      frame,
      time_sec: frame / 10,
    })),
  },
};

test('boundaries create ordered non-overlapping segments and can be removed', () => {
  let boundaries = toggleMixedBoundary([], 20);
  boundaries = toggleMixedBoundary(boundaries, 40);
  assert.deepEqual(boundaries, [20, 40]);
  assert.deepEqual(mixedSegments(reviewCase, boundaries).map((segment) => [segment.frameStart, segment.frameEnd]), [[10, 20], [21, 40], [41, 50]]);
  assert.deepEqual(toggleMixedBoundary(boundaries, 20), [40]);
});

test('valid split requires a decision for every segment and stays inside range', () => {
  const assignments: MixedSegmentAssignment[] = [
    { action: 'assign_roster_player', player_id: 'patryk' },
    { action: 'assign_team', team_label: 'B' },
  ];
  assert.equal(validMixedResolution(reviewCase, [25], assignments), true);
  assert.equal(validMixedResolution(reviewCase, [25], [assignments[0]]), false);
  assert.equal(validMixedResolution(reviewCase, [50], assignments), false);
});

test('adding or moving a boundary preserves unambiguous segment assignments', () => {
  const teamA: MixedSegmentAssignment = { action: 'assign_team', team_label: 'A' };
  const teamB: MixedSegmentAssignment = { action: 'assign_team', team_label: 'B' };

  const added = remapMixedAssignments(reviewCase, [30], [20, 30], [teamA, teamB]);
  assert.deepEqual(added.assignments, [teamA, teamA, teamB]);
  assert.equal(added.requiresConfirmation, false);

  const moved = remapMixedAssignments(reviewCase, [20, 30], [25, 30], [teamA, teamA, teamB]);
  assert.deepEqual(moved.assignments, [teamA, teamA, teamB]);
  assert.equal(moved.requiresConfirmation, false);
});

test('removing a boundary warns before clearing conflicting assignments', () => {
  const teamA: MixedSegmentAssignment = { action: 'assign_team', team_label: 'A' };
  const teamB: MixedSegmentAssignment = { action: 'assign_team', team_label: 'B' };
  const conflict = remapMixedAssignments(reviewCase, [20], [], [teamA, teamB]);
  assert.deepEqual(conflict.assignments, [null]);
  assert.equal(conflict.requiresConfirmation, true);
  assert.deepEqual(conflict.clearedAssignments, [teamA, teamB]);

  const same = remapMixedAssignments(reviewCase, [20], [], [teamA, teamA]);
  assert.deepEqual(same.assignments, [teamA]);
  assert.equal(same.requiresConfirmation, false);

  const partial = remapMixedAssignments(reviewCase, [20], [], [teamA, null]);
  assert.deepEqual(partial.assignments, [null]);
  assert.equal(partial.requiresConfirmation, true);
});

test('local refinement replaces only the split inside the selected overview interval', () => {
  assert.deepEqual(replaceMixedBoundaryInInterval([15, 35, 45], 30, 40, 38), [15, 38, 45]);
  assert.deepEqual(replaceMixedBoundaryInInterval([], 20, 30, 27), [27]);
});

test('normal classification stays compact while dedicated workspace owns splitting', () => {
  const form = readFileSync(resolve(import.meta.dirname, '../src/components/ReviewedIdentityCorrectionForm.tsx'), 'utf8');
  const mixed = readFileSync(resolve(import.meta.dirname, '../src/components/MixedPlayersReviewPanel.tsx'), 'utf8');
  assert.match(form, /Zmieszani gracze/);
  assert.match(form, /osobnego kroku/);
  assert.doesNotMatch(form, /Podziel tutaj/);
  assert.match(mixed, /Materiał w kolejności czasu/);
  assert.match(mixed, /Doprecyzuj moment przejścia/);
  assert.match(mixed, /getMixedBoundaryRefinement/);
  assert.match(mixed, /window\.confirm/);
  assert.match(mixed, /Wybrany fragment/);
  assert.match(mixed, /Zapisz podział \+ następny/);
  assert.match(mixed, /Nie ma prostego podziału czasowego/);
});

test('mixed workspace keeps temporal evidence explicitly sorted', () => {
  const sorted = [...reviewCase.temporal_evidence.anchor_crops].sort((left, right) => left.frame - right.frame);
  assert.deepEqual(sorted.map((crop) => crop.frame), [10, 20, 30, 40, 50]);
});

test('only a successful save removes the current case and advances safely', () => {
  const second = { ...reviewCase, candidate_subject_id: 'mixed-2' };
  const before = [reviewCase, second];

  assert.deepEqual(before.map((item) => item.candidate_subject_id), ['mixed-1', 'mixed-2']);
  const after = mixedQueueAfterSuccessfulSave(before, 'mixed-1', 0);
  assert.deepEqual(after.cases.map((item) => item.candidate_subject_id), ['mixed-2']);
  assert.equal(after.index, 0);
});

test('segment presentation derives operator time from temporal evidence', () => {
  assert.equal(mixedFramesPerSecond(reviewCase), 10);
  assert.equal(mixedTimeForFrame(reviewCase, 35), 3.5);
});
