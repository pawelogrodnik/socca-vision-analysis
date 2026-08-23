import type {
  MixedSegmentAssignment,
  ReviewedCorrectionActionCapability,
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
  { action: 'mixed_players', label: 'Kilku zawodników' },
  { action: 'referee', label: 'Sędzia' },
  { action: 'false_detection', label: 'Fałszywa detekcja' },
  { action: 'team_unknown', label: 'Nieznana drużyna' },
  { action: 'unresolved', label: 'Nie wiem' },
];

// Only required queue work stages an exact mixed marker for the dedicated
// Mixed Players stage. Optional MAX and Video QA split directly instead.
// Server capabilities remain the availability authority in both modes.
export function reviewedIdentityPrimaryActionCards(
  capabilities: Partial<Record<ReviewedCorrectionPrimaryAction, ReviewedCorrectionActionCapability>> | undefined,
  mixedHandling: 'stage' | 'direct',
): ReviewedIdentityActionCard[] {
  return REVIEWED_IDENTITY_PRIMARY_ACTIONS.flatMap((card) => {
    if (card.action === 'mixed_players') {
      if (mixedHandling === 'stage') {
        return capabilities?.mixed_players?.allowed === true ? [card] : [];
      }
      return capabilities?.split?.allowed === true
        ? [{ action: 'split' as const, label: 'Podziel' }]
        : [];
    }
    return capabilities?.[card.action]?.allowed === true ? [card] : [];
  });
}

export const REVIEWED_IDENTITY_ADVANCED_ACTIONS: readonly ReviewedIdentityActionCard[] = [
  { action: 'assign_existing_slot', label: 'Ten sam anonimowy gracz co Axx/Bxx' },
  { action: 'create_new_stable_player', label: 'Nowy anonimowy zawodnik' },
];

export function isReviewedIdentityChildAction(
  action: ReviewedCorrectionPrimaryAction,
): action is MixedSegmentAssignment['action'] {
  return action !== 'split' && action !== 'mixed_players';
}

export function reviewedIdentityChildActions(): ReadonlyArray<
  ReviewedIdentityActionCard & { action: MixedSegmentAssignment['action'] }
> {
  return [...REVIEWED_IDENTITY_PRIMARY_ACTIONS, ...REVIEWED_IDENTITY_ADVANCED_ACTIONS]
    .filter((card): card is ReviewedIdentityActionCard & { action: MixedSegmentAssignment['action'] } => (
      isReviewedIdentityChildAction(card.action)
    ));
}
