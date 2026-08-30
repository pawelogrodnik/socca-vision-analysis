import type { PublicPlayerActivityWindow, PublicPlayerWorkload, PublicReportPlayer } from '../types';

export type WorkloadMetric = 'distance' | 'detectedTime' | 'highIntensity' | 'sprints';
export type ReportablePlayerWorkloadMetric = 'distancePer5' | 'highIntensityPer5' | 'sprintsPer5';

export const WORKLOAD_METRICS: Array<{ key: WorkloadMetric; label: string }> = [
  { key: 'distance', label: 'Dystans' },
  { key: 'detectedTime', label: 'Czas wykryty' },
  { key: 'highIntensity', label: 'Wysoka intensywność' },
  { key: 'sprints', label: 'Sprinty' },
];

export function hasPlayerWorkload(player: PublicReportPlayer): player is PublicReportPlayer & { workload: PublicPlayerWorkload } {
  return Boolean(player.workload?.activity_windows.length);
}

export function hasWorkloadMetrics(players: PublicReportPlayer[]): boolean {
  return players.some(hasPlayerWorkload);
}

export function hasReportablePlayerChartMetric(
  players: PublicReportPlayer[],
  metric: ReportablePlayerWorkloadMetric,
): boolean {
  return players.some((player) => {
    const workload = player.workload;
    const value = metric === 'distancePer5'
      ? workload?.distance_per_5min_m
      : metric === 'highIntensityPer5'
        ? workload?.high_intensity_distance_per_5min_m
        : workload?.sprints_per_5min;
    return value !== null && value !== undefined;
  });
}

export function playerChartEmptyMessage(metric: string): string {
  if (metric === 'distancePer5' || metric === 'highIntensityPer5' || metric === 'sprintsPer5') {
    return 'Brak wystarczającego czasu wykrytego do obliczenia tej metryki.';
  }
  return 'Brak rozpoznanych z imienia zawodników tej drużyny.';
}

export function formatWorkloadSeconds(value: number | null | undefined): string {
  const seconds = Math.max(0, Math.round(Number(value || 0)));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

export function formatRate(value: number | null | undefined, unit: 'm' | 'sprints'): string {
  if (value == null) return '—';
  return unit === 'm' ? `${Math.round(value)} m / 5 min` : `${value.toFixed(1)} / 5 min`;
}

export function formatHiRatio(value: number | null | undefined): string {
  return value == null ? '—' : `${Math.round(value * 100)}%`;
}

export function exactWindowLabel(window: Pick<PublicPlayerActivityWindow, 'start_time_sec' | 'end_time_sec'>): string {
  return `${formatWorkloadSeconds(window.start_time_sec)}–${formatWorkloadSeconds(window.end_time_sec)}`;
}

export function windowValue(window: PublicPlayerActivityWindow | undefined, metric: WorkloadMetric): string {
  if (!window || window.detected_time_sec <= 0) return '—';
  if (metric === 'distance') return `${Math.round(window.total_distance_m)} m`;
  if (metric === 'detectedTime') return formatWorkloadSeconds(window.detected_time_sec);
  if (metric === 'highIntensity') return `${Math.round(window.high_intensity_distance_m)} m`;
  return String(window.sprint_count);
}

export function windowIntensity(window: PublicPlayerActivityWindow | undefined, metric: WorkloadMetric, maximum: number): number {
  if (!window || window.detected_time_sec <= 0) return 0;
  if (metric === 'detectedTime') return Math.min(1, window.detected_time_sec / Math.max(1, window.duration_sec));
  const value = metric === 'distance'
    ? window.total_distance_m
    : metric === 'highIntensity'
      ? window.high_intensity_distance_m
      : window.sprint_count;
  return maximum > 0 ? Math.min(1, value / maximum) : 0;
}

export function metricWindowMaximum(players: PublicReportPlayer[], metric: WorkloadMetric): number {
  return Math.max(
    0,
    ...players.flatMap((player) => player.workload?.activity_windows || []).map((window) => {
      if (metric === 'distance') return window.total_distance_m;
      if (metric === 'highIntensity') return window.high_intensity_distance_m;
      if (metric === 'sprints') return window.sprint_count;
      return window.duration_sec;
    }),
  );
}
