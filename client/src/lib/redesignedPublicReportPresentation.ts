import type { PublicReportPlayer, PublicReportTeam } from '../types';

type MomentumSample = {
  time_sec: number;
  signed_score: number;
};

export type MomentumDisplayBucket = {
  start_time_sec: number;
  end_time_sec: number;
  signed_score: number;
};

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

export function balancedPossessionPercentages(
  left: number | null | undefined,
  right: number | null | undefined,
): { left: number; right: number } | null {
  if (left == null || right == null || !Number.isFinite(left) || !Number.isFinite(right)) return null;
  const total = Math.max(0, left) + Math.max(0, right);
  if (total <= 0) return null;
  const normalizedLeft = Math.round((Math.max(0, left) / total) * 100);
  return { left: normalizedLeft, right: 100 - normalizedLeft };
}

export function formatReportSpeed(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(1)} km/h`;
}

export function displayTeamName(team: PublicReportTeam | undefined, fallback: string): string {
  return team?.team_name || team?.team_label || fallback;
}

export function momentumDisplayBuckets(points: MomentumSample[]): MomentumDisplayBucket[] {
  const buckets = new Map<number, number[]>();
  for (const point of points) {
    if (!Number.isFinite(point.time_sec) || !Number.isFinite(point.signed_score)) continue;
    const startTimeSec = Math.max(0, Math.floor(point.time_sec / 60) * 60);
    buckets.set(startTimeSec, [...(buckets.get(startTimeSec) || []), point.signed_score]);
  }
  return [...buckets.entries()]
    .sort(([left], [right]) => left - right)
    .map(([start_time_sec, scores]) => ({
      start_time_sec,
      end_time_sec: start_time_sec + 60,
      signed_score: scores.reduce((total, score) => total + score, 0) / scores.length,
    }));
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
