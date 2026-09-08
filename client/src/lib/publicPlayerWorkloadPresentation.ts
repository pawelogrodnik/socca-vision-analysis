import type { PublicPlayerActivityWindow, PublicPlayerWorkload, PublicReportPlayer } from '../types';

export type WorkloadMetric = 'distance' | 'detectedTime' | 'highIntensity' | 'sprints';
export type WorkloadPresentationMode = 'normalized' | 'legacy';
export type ReportablePlayerWorkloadMetric = 'distancePer5' | 'highIntensityPer5' | 'sprintsPer5';
export type PublicPlayerChartMetric = 'minutes' | 'distanceKm' | 'distancePer5' | 'highIntensityPer5' | 'sprintsPer5' | 'peakSpeed';
export type WorkloadMetricRange = { minimum: number; maximum: number };

export const WORKLOAD_METRICS: Array<{ key: WorkloadMetric; label: string }> = [
  { key: 'distance', label: 'Dystans / 5 min' },
  { key: 'detectedTime', label: 'Czas wykryty' },
  { key: 'highIntensity', label: 'HI / 5 min' },
  { key: 'sprints', label: 'Sprinty / 5 min' },
];

const LEGACY_WORKLOAD_METRICS: Array<{ key: WorkloadMetric; label: string }> = [
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

export function workloadPresentationMode(players: PublicReportPlayer[]): WorkloadPresentationMode {
  return players.some((player) => player.workload?.activity_windows.some((window) => (
    Object.prototype.hasOwnProperty.call(window, 'rate_status')
    && Object.prototype.hasOwnProperty.call(window, 'distance_per_5min_m')
    && Object.prototype.hasOwnProperty.call(window, 'high_intensity_distance_per_5min_m')
    && Object.prototype.hasOwnProperty.call(window, 'sprints_per_5min')
  )))
    ? 'normalized'
    : 'legacy';
}

export function workloadMetrics(mode: WorkloadPresentationMode): Array<{ key: WorkloadMetric; label: string }> {
  return mode === 'normalized' ? WORKLOAD_METRICS : LEGACY_WORKLOAD_METRICS;
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
    return metric === 'sprintsPer5'
      ? Number(value || 0) > 0
      : value !== null && value !== undefined;
  });
}

export function visiblePlayerChartMetric(
  selected: PublicPlayerChartMetric,
  available: PublicPlayerChartMetric[],
): PublicPlayerChartMetric {
  if (available.includes(selected)) return selected;
  if (available.includes('distancePer5')) return 'distancePer5';
  if (available.includes('distanceKm')) return 'distanceKm';
  return 'minutes';
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

export function windowValue(
  window: PublicPlayerActivityWindow | undefined,
  metric: WorkloadMetric,
  mode: WorkloadPresentationMode,
): string {
  const value = windowMetricValue(window, metric, mode);
  if (value === null) return '—';
  if (metric === 'detectedTime') return formatWorkloadSeconds(value);
  if (metric === 'sprints') return mode === 'normalized' ? value.toFixed(1) : String(value);
  return `${Math.round(value)} m`;
}

export function windowMetricValue(
  window: PublicPlayerActivityWindow | undefined,
  metric: WorkloadMetric,
  mode: WorkloadPresentationMode,
): number | null {
  if (!window) return null;
  if (metric === 'detectedTime') return window.detected_time_sec;
  if (mode === 'legacy') {
    if (window.detected_time_sec <= 0) return null;
    if (metric === 'distance') return window.total_distance_m;
    if (metric === 'highIntensity') return window.high_intensity_distance_m;
    return window.sprint_count;
  }
  if (window.rate_status !== 'reportable') return null;
  if (metric === 'distance') return window.distance_per_5min_m ?? null;
  if (metric === 'highIntensity') return window.high_intensity_distance_per_5min_m ?? null;
  return window.sprints_per_5min ?? null;
}

export function windowIntensity(
  window: PublicPlayerActivityWindow | undefined,
  metric: WorkloadMetric,
  maximum: number,
  mode: WorkloadPresentationMode,
): number {
  if (metric === 'detectedTime') return 0;
  const value = windowMetricValue(window, metric, mode);
  if (value === null) return 0;
  return maximum > 0 ? Math.min(1, value / maximum) : 0;
}

export function windowRelativeIntensity(
  window: PublicPlayerActivityWindow | undefined,
  metric: WorkloadMetric,
  range: WorkloadMetricRange,
  mode: WorkloadPresentationMode,
): number {
  const value = windowMetricValue(window, metric, mode);
  if (value === null || range.maximum <= range.minimum) return 0.5;
  return Math.max(0, Math.min(1, (value - range.minimum) / (range.maximum - range.minimum)));
}

export function metricWindowMaximum(
  players: PublicReportPlayer[],
  metric: WorkloadMetric,
  mode: WorkloadPresentationMode,
): number {
  return Math.max(
    0,
    ...players
      .flatMap((player) => player.workload?.activity_windows || [])
      .map((window) => windowMetricValue(window, metric, mode))
      .filter((value): value is number => value !== null),
  );
}

export function metricWindowRange(
  players: PublicReportPlayer[],
  metric: WorkloadMetric,
  mode: WorkloadPresentationMode,
): WorkloadMetricRange {
  const values = players
    .flatMap((player) => player.workload?.activity_windows || [])
    .map((window) => windowMetricValue(window, metric, mode))
    .filter((value): value is number => value !== null);
  if (!values.length) return { minimum: 0, maximum: 0 };
  return { minimum: Math.min(...values), maximum: Math.max(...values) };
}

export function isUnavailableWorkloadCell(
  window: PublicPlayerActivityWindow | undefined,
  metric: WorkloadMetric,
  mode: WorkloadPresentationMode,
): boolean {
  return metric !== 'detectedTime' && windowMetricValue(window, metric, mode) === null;
}

export function workloadCellTooltip(
  playerName: string,
  window: PublicPlayerActivityWindow | undefined,
  referenceWindow: PublicPlayerActivityWindow,
  metric: WorkloadMetric,
  mode: WorkloadPresentationMode,
): string {
  const interval = exactWindowLabel(window || referenceWindow);
  const heading = `${playerName}, ${interval} materiału.`;
  if (!window) return `${heading} Brak potwierdzonych danych.`;
  const detected = `Czas wykryty: ${formatWorkloadSeconds(window.detected_time_sec)}`;
  if (metric === 'detectedTime') {
    return `${heading}\n${detected}\nTo potwierdzony czas obserwacji, nie pełny zapis aktywności zawodnika.`;
  }
  if (mode === 'normalized' && isUnavailableWorkloadCell(window, metric, mode)) {
    return `${heading}\n— Za mało danych do wiarygodnego porównania\n${detected}`;
  }
  const value = windowValue(window, metric, mode);
  if (metric === 'distance') {
    const label = mode === 'normalized' ? 'Dystans / 5 min' : 'Dystans';
    return `${heading}\n${label}: ${value}\nDystans zarejestrowany: ${Math.round(window.total_distance_m)} m\n${detected}`;
  }
  if (metric === 'highIntensity') {
    const label = mode === 'normalized' ? 'HI / 5 min' : 'Wysoka intensywność';
    return `${heading}\n${label}: ${value}\nHI zarejestrowana: ${Math.round(window.high_intensity_distance_m)} m\n${detected}`;
  }
  const label = mode === 'normalized' ? 'Sprinty / 5 min' : 'Sprinty';
  return `${heading}\n${label}: ${value}\nSprinty zarejestrowane: ${window.sprint_count}\n${detected}`;
}
