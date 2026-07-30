import type {
  InitialIdentityAuditObservation,
  InitialIdentityAuditTeamLabel,
} from '../types';

export function secondHalfTeamClass(
  teamLabel: InitialIdentityAuditTeamLabel,
): string {
  if (teamLabel === 'A') return 'team-a';
  if (teamLabel === 'B') return 'team-b';
  return 'team-unknown';
}

export function secondHalfObservationLabel(
  observation: InitialIdentityAuditObservation,
  displayIndex: number,
  decided: boolean,
): string {
  const suggestion = secondHalfVisibleSuggestion(
    observation,
  )?.player_name?.trim();
  const decisionPrefix = decided ? '✓ ' : '';
  return suggestion
    ? `${decisionPrefix}${displayIndex} · ${suggestion}?`
    : `${decisionPrefix}${displayIndex}`;
}

export function secondHalfVisibleSuggestion(
  observation: InitialIdentityAuditObservation,
): NonNullable<InitialIdentityAuditObservation['suggested_player']> | null {
  const suggestion = observation.suggested_player;
  if (!suggestion) return null;
  if (
    observation.team_label in { A: true, B: true }
    && suggestion.team_label in { A: true, B: true }
    && observation.team_label !== suggestion.team_label
  ) {
    return null;
  }
  return suggestion;
}

export function secondHalfSuggestionSourceLabel(
  suggestionSource?: string,
): string {
  if (suggestionSource === 'h1_safe_lineage') {
    return 'Ciągłość trackletu od potwierdzenia H1';
  }
  if (suggestionSource === 'cross_analysis_reid_top3_advisory') {
    return 'Porównanie wyglądu ReID';
  }
  return 'Automatyczna hipoteza systemu';
}
