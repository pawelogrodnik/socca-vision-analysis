import type { ReviewedIdentityCoverageDebt } from '../types';

export type CoverageDebtPresentationTeam = {
  teamLabel: 'A' | 'B';
  team: ReviewedIdentityCoverageDebt['per_team'][string];
  actualRequired: ReviewedIdentityCoverageDebt['actual_required_queue']['per_team'][string] | null;
  isTeamStatsOnly: boolean;
  show: boolean;
};

export function coverageDebtPresentationTeams(
  debt: ReviewedIdentityCoverageDebt,
): CoverageDebtPresentationTeam[] {
  return (['A', 'B'] as const).flatMap((teamLabel) => {
    const team = debt.per_team[teamLabel];
    if (!team) return [];
    const isTeamStatsOnly = team.scope === 'team_stats_only';
    const actualRequired = debt.actual_required_queue.per_team[teamLabel] || null;
    return [{
      teamLabel,
      team,
      isTeamStatsOnly,
      actualRequired,
      show: isTeamStatsOnly
        || team.operator_identity_debt_observations > 0
        || (actualRequired?.total_cases || 0) > 0
        || team.ambiguous_mixed_currently_labeled_observations > 0
        || team.unaccounted_unnamed_observations !== 0,
    }];
  });
}

export function requiredBreakdownLabel(kind: 'semantic' | 'continuity' | 'coverage'): string {
  return ({
    semantic: 'Drużyna / konflikt',
    continuity: 'Ciągłość',
    coverage: 'Pokrycie imienne',
  })[kind];
}
