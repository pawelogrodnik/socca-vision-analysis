import type {
  MixedSegmentAssignment,
  ReviewedCorrectionPrimaryAction,
} from '../types';

export type ReviewedIdentityActionCard = {
  action: ReviewedCorrectionPrimaryAction;
  label: string;
};

// This is presentation order only. Availability always comes from the
// server-provided action_capabilities map in the correction context.
export const REVIEWED_IDENTITY_PRIMARY_ACTIONS: readonly ReviewedIdentityActionCard[] = [
  { action: 'assign_roster_player', label: 'Zawodnik z kadry' },
  { action: 'assign_team', label: 'Tylko drużyna / zawodnik nieznany' },
  { action: 'split', label: 'To kilku zawodników — podziel' },
  { action: 'referee', label: 'Sędzia' },
  { action: 'false_detection', label: 'Fałszywa detekcja' },
  { action: 'team_unknown', label: 'Nieznana drużyna' },
  { action: 'unresolved', label: 'Nie wiem' },
];

export const REVIEWED_IDENTITY_ADVANCED_ACTIONS: readonly ReviewedIdentityActionCard[] = [
  { action: 'assign_existing_slot', label: 'Ten sam anonimowy gracz co Axx/Bxx' },
  { action: 'create_new_stable_player', label: 'Nowy anonimowy zawodnik' },
];

export function isReviewedIdentityChildAction(
  action: ReviewedCorrectionPrimaryAction,
): action is MixedSegmentAssignment['action'] {
  return action !== 'split';
}

export function reviewedIdentityChildActions(): ReadonlyArray<
  ReviewedIdentityActionCard & { action: MixedSegmentAssignment['action'] }
> {
  return [...REVIEWED_IDENTITY_PRIMARY_ACTIONS, ...REVIEWED_IDENTITY_ADVANCED_ACTIONS]
    .filter((card): card is ReviewedIdentityActionCard & { action: MixedSegmentAssignment['action'] } => (
      isReviewedIdentityChildAction(card.action)
    ));
}
