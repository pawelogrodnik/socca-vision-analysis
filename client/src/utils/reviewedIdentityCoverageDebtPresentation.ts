import type { ReviewedIdentityCoverageDebt } from '../types';

export type CoverageDebtPresentationTeam = {
  teamLabel: 'A' | 'B';
  team: ReviewedIdentityCoverageDebt['per_team'][string];
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
    return [{
      teamLabel,
      team,
      isTeamStatsOnly,
      show: isTeamStatsOnly || team.operator_identity_debt_observations > 0,
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
