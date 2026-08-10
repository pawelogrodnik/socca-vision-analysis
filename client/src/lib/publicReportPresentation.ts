import type { PublicMatchReport, PublicReportPlayer } from '../types';


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
