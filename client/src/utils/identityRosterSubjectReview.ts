import type {
  IdentityRosterSubjectCandidate,
  IdentityRosterSubjectReviewCard,
  IdentityRosterSubjectReviewDocument,
} from '../types';

export type SubjectReviewFilter = 'pending' | 'reviewed' | 'all';
export type SubjectTeamFilter = 'A' | 'B' | 'U' | 'all';

export function isActionableSubjectReviewCard(
  card: IdentityRosterSubjectReviewCard,
): boolean {
  if (card.requires_operator_review === false) return false;
  if (![
    'ready_for_operator_review',
    'blocked_conflict',
    'blocked_seed_conflict',
  ].includes(card.review_status)) return false;
  return card.allowed_actions.some((action) => [
    'assign_roster_player',
    'confirm_recommended_player',
    'mark_unresolved',
  ].includes(action));
}

export function isSeedResolvedSubjectReviewCard(
  card: IdentityRosterSubjectReviewCard,
): boolean {
  return card.review_status === 'completed_by_initial_audit'
    || (
      card.requires_operator_review === false
      && card.seed_resolution?.status === 'accepted'
    );
}

export function isEffectivelyReviewedSubjectReviewCard(
  card: IdentityRosterSubjectReviewCard,
): boolean {
  return Boolean(card.operator_decision) || isSeedResolvedSubjectReviewCard(card);
}

export function subjectRosterOptions(
  card: IdentityRosterSubjectReviewCard,
): IdentityRosterSubjectCandidate[] {
  return card.operator_roster_options?.length
    ? card.operator_roster_options
    : card.roster_candidates;
}

export function visibleSubjectReviewCards(
  document: IdentityRosterSubjectReviewDocument,
  reviewFilter: SubjectReviewFilter,
  teamFilter: SubjectTeamFilter,
): IdentityRosterSubjectReviewCard[] {
  return document.cards.filter((card) => {
    const reviewed = isEffectivelyReviewedSubjectReviewCard(card);
    const reviewMatches =
      reviewFilter === 'all' ||
      (reviewFilter === 'reviewed'
        ? reviewed
        : !reviewed && isActionableSubjectReviewCard(card));
    const normalizedTeam = card.team_label || 'U';
    return reviewMatches && (teamFilter === 'all' || teamFilter === normalizedTeam);
  });
}

export function subjectReviewStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    ready_for_operator_review: 'Gotowy do review',
    blocked_conflict: 'Konflikt',
    blocked_seed_conflict: 'Konflikt z initial audit',
    completed_by_initial_audit: 'Rozwiazany przez initial audit',
    needs_more_visual_evidence: 'Ograniczone dowody',
    no_visual_evidence: 'Brak cropow',
  };
  return labels[status] || status;
}

export function subjectDecisionLabel(card: IdentityRosterSubjectReviewCard): string {
  const decision = card.operator_decision;
  if (!decision && isSeedResolvedSubjectReviewCard(card)) {
    const seededPlayer = card.seed_resolution?.assigned_player;
    return seededPlayer?.name
      || seededPlayer?.player_name
      || card.recommended_player?.player_name
      || 'Rozwiazany przez audit';
  }
  if (!decision) return 'Nieoznaczony';
  if (decision.decision === 'mark_unresolved') return 'Nierozstrzygniety';
  const player = subjectRosterOptions(card).find((candidate) => candidate.player_id === decision.player_id);
  return player?.player_name || decision.player_id || 'Przypisany';
}

export function nearestPendingCardIndex(
  cards: IdentityRosterSubjectReviewCard[],
  currentIndex: number,
): number {
  if (cards.length === 0) return 0;
  for (let offset = 1; offset <= cards.length; offset += 1) {
    const index = (currentIndex + offset) % cards.length;
    if (
      !isEffectivelyReviewedSubjectReviewCard(cards[index])
      && isActionableSubjectReviewCard(cards[index])
    ) return index;
  }
  return Math.min(currentIndex, cards.length - 1);
}
