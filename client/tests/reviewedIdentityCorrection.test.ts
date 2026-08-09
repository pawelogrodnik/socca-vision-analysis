import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import type { ReviewedCorrectionContext } from '../src/types.ts';
import {
  buildReviewedCorrectionPayload,
  correctionOptionsForSubject,
  defaultCorrectionTeam,
} from '../src/utils/reviewedIdentityCorrection.ts';
import {
  formatElapsedTime,
  formatReviewTime,
  reviewedIdentityStatusLabel,
  reviewedRenderStatusLabel,
  shouldShowInitialReviewCta,
} from '../src/utils/reviewedOutputPresentation.ts';

const base = {
  playerId: '',
  stableSlotId: '',
  teamLabel: 'A',
  comment: '',
};

test('builds API payloads for every whole-subject correction action', () => {
  assert.deepEqual(
    buildReviewedCorrectionPayload('subject-1', {
      ...base,
      action: 'assign_roster_player',
      playerId: 'player-a',
    }),
    { candidate_subject_id: 'subject-1', action: 'assign_roster_player', player_id: 'player-a' },
  );
  assert.deepEqual(
    buildReviewedCorrectionPayload('subject-1', {
      ...base,
      action: 'assign_existing_slot',
      stableSlotId: 'A03',
    }),
    { candidate_subject_id: 'subject-1', action: 'assign_existing_slot', stable_slot_id: 'A03' },
  );
  assert.deepEqual(
    buildReviewedCorrectionPayload('subject-1', {
      ...base,
      action: 'create_new_stable_player',
      comment: 'new player',
    }),
    { candidate_subject_id: 'subject-1', action: 'create_new_stable_player', team_label: 'A', comment: 'new player' },
  );
  for (const action of ['referee', 'false_detection', 'team_unknown', 'unresolved'] as const) {
    assert.deepEqual(
      buildReviewedCorrectionPayload('subject-1', { ...base, action }),
      { candidate_subject_id: 'subject-1', action },
    );
  }
});

test('filters roster and canonical/manual slot options to the subject team', () => {
  const context: ReviewedCorrectionContext = {
    candidate_subject_id: 'subject-1',
    team_label: 'A',
    source_team_label: 'A',
    effective_team_label: 'A',
    available_team_labels: ['A'],
    tracklet_ids: ['tracklet-1'],
    review_card_key: 'card-1',
    current_decision: null,
    semantic_decision_digest: 'digest',
    roster_options: [
      { player_id: 'a', player_name: 'A', team_label: 'A' },
      { player_id: 'b', player_name: 'B', team_label: 'B' },
    ],
    slot_options: [
      { stable_slot_id: 'A03', team_label: 'A', source: 'global_identity', status: 'canonical' },
      { stable_slot_id: 'A11', team_label: 'A', source: 'manual_new_player_confirmation', status: 'active' },
      { stable_slot_id: 'B02', team_label: 'B', source: 'global_identity', status: 'canonical' },
    ],
  };
  const options = correctionOptionsForSubject(context);
  assert.deepEqual(options.roster.map((row) => row.player_id), ['a']);
  assert.deepEqual(options.slots.map((row) => row.stable_slot_id), ['A03', 'A11']);
});

test('unknown-team context exposes both teams but filters options after the operator selects one', () => {
  const context: ReviewedCorrectionContext = {
    candidate_subject_id: 'subject-u',
    team_label: 'U',
    source_team_label: 'U',
    effective_team_label: 'U',
    available_team_labels: ['A', 'B'],
    tracklet_ids: ['tracklet-u'],
    review_card_key: null,
    current_decision: null,
    semantic_decision_digest: 'digest',
    roster_options: [
      { player_id: 'a', player_name: 'A', team_label: 'A' },
      { player_id: 'b', player_name: 'B', team_label: 'B' },
    ],
    slot_options: [
      { stable_slot_id: 'A03', team_label: 'A', source: 'global_identity', status: 'canonical' },
      { stable_slot_id: 'B03', team_label: 'B', source: 'global_identity', status: 'canonical' },
    ],
  };
  const options = correctionOptionsForSubject(context, 'B');
  assert.deepEqual(options.roster.map((row) => row.player_id), ['b']);
  assert.deepEqual(options.slots.map((row) => row.stable_slot_id), ['B03']);
  assert.deepEqual(
    buildReviewedCorrectionPayload('subject-u', { ...base, action: 'assign_team', teamLabel: 'B' }),
    { candidate_subject_id: 'subject-u', action: 'assign_team', team_label: 'B' },
  );
});

test('defaults a stale effective team to the valid source-team correction domain', () => {
  const context: ReviewedCorrectionContext = {
    candidate_subject_id: 'subject-b',
    team_label: 'A',
    source_team_label: 'B',
    effective_team_label: 'A',
    available_team_labels: ['B'],
    tracklet_ids: ['tracklet-b'],
    review_card_key: 'card-b',
    current_decision: null,
    semantic_decision_digest: 'digest',
    roster_options: [
      { player_id: 'b1', player_name: 'B1', team_label: 'B' },
      { player_id: 'b2', player_name: 'B2', team_label: 'B' },
    ],
    slot_options: [],
  };

  const selectedTeam = defaultCorrectionTeam(context);
  const options = correctionOptionsForSubject(context, selectedTeam);

  assert.equal(selectedTeam, 'B');
  assert.notEqual(selectedTeam, 'A');
  assert.deepEqual(options.roster.map((row) => row.player_id), ['b1', 'b2']);
});

test('defaults a normal same-team correction to its effective team', () => {
  const context: ReviewedCorrectionContext = {
    candidate_subject_id: 'subject-a',
    team_label: 'A',
    source_team_label: 'A',
    effective_team_label: 'A',
    available_team_labels: ['A'],
    tracklet_ids: ['tracklet-a'],
    review_card_key: null,
    current_decision: null,
    semantic_decision_digest: 'digest',
    roster_options: [],
    slot_options: [],
  };

  assert.equal(defaultCorrectionTeam(context), 'A');
});

test('defaults an unknown-source correction to a valid effective team', () => {
  const context: ReviewedCorrectionContext = {
    candidate_subject_id: 'subject-u',
    team_label: 'B',
    source_team_label: 'U',
    effective_team_label: 'B',
    available_team_labels: ['A', 'B'],
    tracklet_ids: ['tracklet-u'],
    review_card_key: null,
    current_decision: null,
    semantic_decision_digest: 'digest',
    roster_options: [],
    slot_options: [],
  };

  assert.equal(defaultCorrectionTeam(context), 'B');
});

test('requires a team choice when an unknown source has no valid effective team', () => {
  const context: ReviewedCorrectionContext = {
    candidate_subject_id: 'subject-u',
    team_label: 'U',
    source_team_label: 'U',
    effective_team_label: 'U',
    available_team_labels: ['A', 'B'],
    tracklet_ids: ['tracklet-u'],
    review_card_key: null,
    current_decision: null,
    semantic_decision_digest: 'digest',
    roster_options: [],
    slot_options: [],
  };

  assert.equal(defaultCorrectionTeam(context), '');
});

test('reviewed output presentation uses operator-facing labels and one initial CTA', () => {
  assert.equal(reviewedIdentityStatusLabel('missing'), 'Review nieprzygotowane');
  assert.equal(reviewedIdentityStatusLabel('partial_reviewed'), 'Review rozpoczęte');
  assert.equal(reviewedRenderStatusLabel('running'), 'Trwa przygotowywanie wideo');
  assert.equal(reviewedRenderStatusLabel('stale'), 'Wideo nieaktualne po poprawkach');
  assert.equal(shouldShowInitialReviewCta('missing', 'missing'), true);
  assert.equal(shouldShowInitialReviewCta('partial_reviewed', 'missing'), true);
  assert.equal(shouldShowInitialReviewCta('partial_reviewed', 'completed'), false);
  assert.equal(formatReviewTime(42.3), '00:42.3');
  assert.equal(formatElapsedTime('2026-08-06T10:00:00.000Z', Date.parse('2026-08-06T10:02:03.000Z')), '2 min 3 s');
});

test('video QA stays in the unified workspace and report has no interactive review editor', () => {
  const root = new URL('../src/components/', import.meta.url);
  const workspace = readFileSync(new URL('IdentityReviewWorkspace.tsx', root), 'utf8');
  const output = readFileSync(new URL('ReviewedVideoQaPanel.tsx', root), 'utf8');
  const atTime = readFileSync(new URL('ReviewedIdentityAtTimePanel.tsx', root), 'utf8');
  const form = readFileSync(new URL('ReviewedIdentityCorrectionForm.tsx', root), 'utf8');
  const reportPage = readFileSync(new URL('MatchReportPage.tsx', root), 'utf8');

  assert.match(workspace, /Przygotuj wideo do sprawdzenia/);
  assert.match(workspace, /finalizeReviewWorkflow\(match\.id, videoSettings\)/);
  assert.doesNotMatch(workspace, /finalizeReviewedIdentity/);
  assert.doesNotMatch(workspace, /generateReviewedOutput/);
  assert.match(workspace, /getReviewWorkflow\(match\.id\)/);
  assert.match(workspace, /IdentityExceptionReviewPanel/);
  assert.match(workspace, /InitialIdentityAuditPanel/);
  assert.match(workspace, /ReviewedVideoQaPanel/);
  assert.match(output, /reviewedCorrectionWorkflowPresentation\(result\)/);
  assert.match(output, /Przygotowuję zaktualizowane wideo automatycznie/);
  assert.match(output, /Zatwierdź Video QA/);
  assert.match(output, /approveReviewVideoQa\(matchId\)/);
  assert.doesNotMatch(output, /finalizeReviewWorkflow/);
  assert.match(output, /Sprawdź osoby w klatce/);
  assert.match(output, /Statystyki po review/);
  assert.match(output, /videoRef\.current\.currentTime/);
  assert.match(output, /setStats\(null\)/);
  assert.match(atTime, /Osoby widoczne w tej klatce/);
  assert.match(atTime, /Zidentyfikuj/);
  assert.match(atTime, /candidate_subject_id/);
  assert.match(atTime, /Szczegóły techniczne/);
  assert.match(form, /allocated_stable_slot_id|onSaved/);
  assert.match(form, /setError\(errorMessage\(reason\)\)/);
  assert.match(form, /context\?\.source_team_label/);
  assert.match(form, /Zawodnik z kadry/);
  assert.match(form, /Do której drużyny należy ta osoba/);
  assert.match(form, /Tylko \$\{teamName\} — pozostaw \$\{teamLabel\}\?/);
  assert.match(form, /assign_team/);
  assert.match(form, /Co wiesz o tym zawodniku/);
  assert.match(form, /sourceTeamUnknown/);
  assert.match(form, /!action/);
  assert.match(form, /defaultCorrectionTeam\(value\)/);
  assert.doesNotMatch(form, /setSelectedTeamLabel\(\(current\)/);
  assert.match(form, /current effective team: \{context\?\.effective_team_label/);
  assert.match(form, /selected correction team: \{selectedTeamLabel/);
  assert.doesNotMatch(reportPage, /ReviewedMatchOutputPanel|ReviewedVideoQaPanel|ReviewedIdentityCorrectionForm/);
  assert.match(reportPage, /Review meczu nie jest jeszcze zakończony/);
});
