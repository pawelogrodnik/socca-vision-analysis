import type { PublicMatchReport, PublicReportPlayer, PublicReportTeam } from '../types';


const NON_JERSEY_VALUES = new Set(['player', 'goalkeeper', 'field player']);

export function displayJerseyNumber(value: string | null | undefined): string | null {
  const normalized = String(value || '').trim();
  if (!normalized || NON_JERSEY_VALUES.has(normalized.toLowerCase())) return null;
  return normalized;
}

export function hasPlayerReadyMomentum(report: PublicMatchReport): boolean {
  const momentum = report.ball?.attacking_momentum;
  if (!momentum?.timeline.length) return false;
  return ['high', 'medium'].includes(String(momentum.signal_quality || momentum.quality || '').toLowerCase());
}

export function hasAdvancedPlayerMetrics(players: PublicReportPlayer[]): boolean {
  return players.some(
    (player) =>
      Number(player.avg_speed_kmh || 0) > 0 ||
      Number(player.peak_speed_kmh || 0) > 0 ||
      Number(player.high_intensity_distance_m || 0) > 0 ||
      Number(player.sprint_count || 0) > 0,
  );
}

function normalizedTeamValue(value: string | null | undefined): string {
  return String(value || '').trim().toLocaleLowerCase('pl');
}

export function publicReportTeamKey(team: PublicReportTeam, index: number): string {
  const teamId = normalizedTeamValue(team.team_id);
  if (teamId) return `id:${teamId}`;

  const teamLabel = normalizedTeamValue(team.team_label);
  if (teamLabel) return `label:${teamLabel}`;

  const teamName = normalizedTeamValue(team.team_name);
  return teamName ? `name:${teamName}` : `team:${index}`;
}

export function playerBelongsToPublicReportTeam(
  player: PublicReportPlayer,
  team: PublicReportTeam,
): boolean {
  const playerTeamId = normalizedTeamValue(player.team_id);
  const teamId = normalizedTeamValue(team.team_id);
  if (playerTeamId && teamId) return playerTeamId === teamId;

  const playerTeamLabel = normalizedTeamValue(player.team_label);
  const teamLabel = normalizedTeamValue(team.team_label);
  if (playerTeamLabel && teamLabel) return playerTeamLabel === teamLabel;

  const playerTeamName = normalizedTeamValue(player.team_name);
  const teamName = normalizedTeamValue(team.team_name);
  return Boolean(playerTeamName && teamName && playerTeamName === teamName);
}

export function publicReportPlayersForTeam(
  players: PublicReportPlayer[],
  team: PublicReportTeam | undefined,
): PublicReportPlayer[] {
  if (!team) return players;
  return players.filter((player) => playerBelongsToPublicReportTeam(player, team));
}
