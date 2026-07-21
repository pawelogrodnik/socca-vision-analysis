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
  return card.review_status === 'ready_for_operator_review';
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
    const reviewed = Boolean(card.operator_decision);
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
    needs_more_visual_evidence: 'Ograniczone dowody',
    no_visual_evidence: 'Brak cropow',
  };
  return labels[status] || status;
}

export function subjectDecisionLabel(card: IdentityRosterSubjectReviewCard): string {
  const decision = card.operator_decision;
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
    if (!cards[index].operator_decision) return index;
  }
  return Math.min(currentIndex, cards.length - 1);
}
