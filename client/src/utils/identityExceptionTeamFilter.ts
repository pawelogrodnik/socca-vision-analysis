import type {
  ReviewedIdentityReviewFilters,
  ReviewedIdentityTeamFilterLabel,
  Team,
} from '../types';


export type TeamReviewFilter = 'all' | ReviewedIdentityTeamFilterLabel;


export type TeamReviewFilterOption = {
  value: TeamReviewFilter;
  label: string;
  count: number;
};


export function matchTeamName(
  teams: Team[],
  teamLabel: ReviewedIdentityTeamFilterLabel,
): string {
  const index = teamLabel === 'A' ? 0 : 1;
  return teams[index]?.name?.trim() || `Team ${teamLabel}`;
}


export function teamReviewFilterOptions(
  teams: Team[],
  filters: ReviewedIdentityReviewFilters | null,
): TeamReviewFilterOption[] {
  const counts = filters?.counts || { all: 0, A: 0, B: 0, U: 0 };
  return [
    { value: 'all', label: 'Wszystkie', count: counts.all },
    { value: 'A', label: matchTeamName(teams, 'A'), count: counts.A },
    { value: 'B', label: matchTeamName(teams, 'B'), count: counts.B },
  ];
}


export function apiTeamFilter(
  filter: TeamReviewFilter,
): ReviewedIdentityTeamFilterLabel | undefined {
  return filter === 'all' ? undefined : filter;
}
