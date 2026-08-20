import type { ReviewedCorrectionAction, Team } from '../types';
import { matchTeamName } from './identityExceptionTeamFilter';

export type TeamAttributionActionCard = {
  action: ReviewedCorrectionAction;
  label: string;
  description?: string;
};

export const TEAM_ATTRIBUTION_ONLY_ACTIONS: readonly TeamAttributionActionCard[] = [
  { action: 'referee', label: 'Sędzia' },
  { action: 'false_detection', label: 'Fałszywa detekcja' },
  { action: 'team_unknown', label: 'Nieznana drużyna' },
  { action: 'unresolved', label: 'Nie wiem' },
];

export type TeamAttributionTeamAction = {
  action: 'assign_team';
  teamLabel: 'A' | 'B';
  label: string;
};

export function teamAttributionTeamActions(
  teams: Team[] | undefined,
): TeamAttributionTeamAction[] {
  const availableTeams = teams || [];
  return (['A', 'B'] as const).map((teamLabel) => ({
    action: 'assign_team',
    teamLabel,
    label: `${matchTeamName(availableTeams, teamLabel)} — zawodnik nieznany`,
  }));
}
