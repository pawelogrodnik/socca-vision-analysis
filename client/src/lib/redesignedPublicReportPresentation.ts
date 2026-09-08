import type { PublicReportPlayer, PublicReportTeam } from '../types';

export function isPublishedReportId(matchId: string | undefined): boolean {
  return Boolean(matchId?.startsWith('published-'));
}

export function formatReportClock(seconds: number | null | undefined): string {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}

export function formatReportDuration(seconds: number | null | undefined): string | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return null;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return remainder ? `${minutes} min ${remainder} s` : `${minutes} min`;
}

export function formatReportKilometers(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '—' : `${(value / 1000).toFixed(1)} km`;
}

export function formatReportPercent(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(0)}%`;
}

export function formatReportSpeed(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(1)} km/h`;
}

export function displayTeamName(team: PublicReportTeam | undefined, fallback: string): string {
  return team?.team_name || team?.team_label || fallback;
}

export function teamReportColor(team: PublicReportTeam | undefined, fallback: string): string {
  return team?.display_color || fallback;
}

export function playerComparableValue(player: PublicReportPlayer, key: string): number | null {
  const value = key === 'detected_time_sec'
    ? player.detected_time_sec ?? player.playing_time_sec
    : key === 'distance_per_5min_m'
      ? player.workload?.distance_per_5min_m
      : key === 'high_intensity_distance_per_5min_m'
        ? player.workload?.high_intensity_distance_per_5min_m
        : key === 'sprints_per_5min'
          ? player.workload?.sprints_per_5min
          : player[key as keyof PublicReportPlayer];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
