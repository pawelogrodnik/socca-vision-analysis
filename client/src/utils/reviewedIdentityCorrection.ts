import type {
  ReviewedCorrectionAction,
  ReviewedCorrectionContext,
  ReviewedCorrectionRequest,
} from '../types';

export type ReviewedCorrectionFormValues = {
  action: ReviewedCorrectionAction;
  playerId: string;
  stableSlotId: string;
  teamLabel: string;
  comment: string;
  mixedHint?: ReviewedCorrectionRequest['mixed_hint'];
};

export function defaultCorrectionTeam(context: ReviewedCorrectionContext): string {
  const availableTeams = new Set(context.available_team_labels);
  if (
    ['A', 'B'].includes(context.effective_team_label)
    && availableTeams.has(context.effective_team_label)
  ) {
    return context.effective_team_label;
  }
  if (
    ['A', 'B'].includes(context.source_team_label)
    && availableTeams.has(context.source_team_label)
  ) {
    return context.source_team_label;
  }
  return '';
}

export function correctionOptionsForSubject(
  context: ReviewedCorrectionContext,
  selectedTeamLabel = context.effective_team_label,
) {
  return {
    // A named operator decision is authoritative.  Never hide the opposing
    // roster merely because the detector/source currently says Team A or B.
    roster: context.roster_options,
    slots: context.slot_options.filter(
      (option) => option.team_label === selectedTeamLabel,
    ),
  };
}

export function buildReviewedCorrectionPayload(
  candidateSubjectId: string,
  values: ReviewedCorrectionFormValues,
  context?: ReviewedCorrectionContext | null,
): ReviewedCorrectionRequest {
  const payload: ReviewedCorrectionRequest = {
    candidate_subject_id: candidateSubjectId,
    action: values.action,
  };
  if (context?.review_target_id || context?.scope_kind === 'material_continuity') {
    if (context.review_target_id) payload.review_target_id = context.review_target_id;
    if (!context.source_ownership_digest) {
      throw new Error('Ten fragment wymaga odświeżenia przed zapisem.');
    }
  }
  // Every server-materialized scope, including a normal whole subject, may
  // carry exact ownership. Echo it back so the server can reject stale cards.
  if (context?.source_ownership_digest) {
    payload.source_ownership_digest = context.source_ownership_digest;
  }
  if (values.action === 'assign_roster_player') {
    if (!values.playerId) throw new Error('Wybierz zawodnika z rosteru.');
    payload.player_id = values.playerId;
  }
  if (values.action === 'assign_existing_slot') {
    if (!values.stableSlotId) throw new Error('Wybierz istniejący stable slot.');
    payload.stable_slot_id = values.stableSlotId;
  }
  if (values.action === 'assign_team' || values.action === 'create_new_stable_player') {
    if (!['A', 'B'].includes(values.teamLabel)) {
      throw new Error('Wybierz Team A albo Team B.');
    }
    payload.team_label = values.teamLabel;
  }
  if (values.comment.trim()) payload.comment = values.comment.trim();
  if (values.action === 'mixed_players') payload.mixed_hint = values.mixedHint || 'unknown';
  return payload;
}

export const REVIEWED_CORRECTION_ACTION_LABELS: Record<ReviewedCorrectionAction, string> = {
  assign_roster_player: 'Przypisz zawodnika z rosteru',
  assign_existing_slot: 'Przypisz istniejący stable slot',
  assign_team: 'Przypisz tylko drużynę',
  create_new_stable_player: 'Potwierdź nowego zawodnika drużyny',
  referee: 'Oznacz sędziego',
  false_detection: 'Oznacz false detection',
  team_unknown: 'Team unknown',
  unresolved: 'Pozostaw unresolved',
  mixed_players: 'Zmieszani gracze',
};
