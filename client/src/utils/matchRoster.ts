import type { Team } from '../types';

export type MatchRosterReadiness = {
  ready: boolean;
  code: 'missing_team_a' | 'missing_team_b' | 'duplicate_teams' | 'empty_team_a_roster' | 'empty_team_b_roster' | null;
  message: string | null;
};

export function matchRosterReadiness(
  teams: Team[] | undefined,
): MatchRosterReadiness {
  return selectedMatchRosterReadiness(teams?.[0], teams?.[1]);
}

export function selectedMatchRosterReadiness(
  teamA: Team | undefined,
  teamB: Team | undefined,
): MatchRosterReadiness {
  if (!teamA) return notReady('missing_team_a', 'Wybierz Team A z rosterem zawodników.');
  if (!teamB) return notReady('missing_team_b', 'Wybierz Team B z rosterem zawodników.');
  if (teamKey(teamA) === teamKey(teamB)) {
    return notReady('duplicate_teams', 'Team A i Team B muszą być różnymi drużynami.');
  }
  if (!hasValidPlayer(teamA)) {
    return notReady('empty_team_a_roster', 'Team A musi mieć co najmniej jednego zawodnika w rosterze.');
  }
  if (!hasValidPlayer(teamB)) {
    return notReady('empty_team_b_roster', 'Team B musi mieć co najmniej jednego zawodnika w rosterze.');
  }
  return { ready: true, code: null, message: null };
}

function teamKey(team: Team): string {
  return String(team.id || team.name).trim().toLocaleLowerCase();
}

function hasValidPlayer(team: Team): boolean {
  return (team.players || []).some((player) => Boolean(player.id && player.name.trim()));
}

function notReady(
  code: Exclude<MatchRosterReadiness['code'], null>,
  message: string,
): MatchRosterReadiness {
  return { ready: false, code, message };
}