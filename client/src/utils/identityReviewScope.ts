import type { IdentityReviewScope, IdentityReviewScopeChoice } from '../types';


export function scopeForPlayerStatsChoice(
  choice: IdentityReviewScopeChoice,
): IdentityReviewScope {
  return {
    schema_version: '1.0.0',
    teams: {
      A: choice === 'B' ? 'team_stats_only' : 'complete_roster',
      B: choice === 'A' ? 'team_stats_only' : 'complete_roster',
    },
  };
}


export function playerStatsChoiceFromScope(
  scope: IdentityReviewScope | null | undefined,
): IdentityReviewScopeChoice {
  if (scope?.teams.A === 'team_stats_only' && scope.teams.B === 'complete_roster') return 'B';
  if (scope?.teams.A === 'complete_roster' && scope.teams.B === 'team_stats_only') return 'A';
  return 'both';
}


export function teamNeedsNamedRoster(
  scope: IdentityReviewScope | null | undefined,
  teamLabel: 'A' | 'B',
): boolean {
  return scope?.teams[teamLabel] !== 'team_stats_only';
}
