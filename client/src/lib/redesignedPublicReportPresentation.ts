import type { PublicMatchReport, PublicReportPlayer, PublicReportTeam } from '../types';

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

export type ReportInsight = {
  title: string;
  detail: string;
};

function greaterTeamInsight(
  left: PublicReportTeam,
  right: PublicReportTeam,
  field: 'total_distance_m' | 'completion_rate' | 'possession_share_percent',
  label: string,
): ReportInsight | null {
  const leftValue = left[field];
  const rightValue = right[field];
  if (leftValue == null || rightValue == null || leftValue === rightValue) return null;
  const winner = leftValue > rightValue ? left : right;
  return {
    title: `${displayTeamName(winner, 'Drużyna')} ${label}`,
    detail: field === 'total_distance_m'
      ? `${formatReportKilometers(leftValue)} vs ${formatReportKilometers(rightValue)}`
      : `${formatReportPercent(leftValue)} vs ${formatReportPercent(rightValue)}`,
  };
}

export function publicReportInsights(report: PublicMatchReport): ReportInsight[] {
  const [left, right] = report.teams;
  if (!left || !right) return [];
  const candidates = [
    greaterTeamInsight(left, right, 'possession_share_percent', 'miało większe posiadanie'),
    greaterTeamInsight(left, right, 'completion_rate', 'miało wyższą skuteczność podań'),
  ].filter((insight): insight is ReportInsight => insight !== null);

  const momentum = report.ball?.attacking_momentum;
  const strongestMomentum = momentum?.timeline.reduce((best, point) =>
    !best || Math.abs(point.signed_score) > Math.abs(best.signed_score) ? point : best,
  undefined as typeof momentum.timeline[number] | undefined);
  if (strongestMomentum?.dominant_team_label) {
    const team = strongestMomentum.dominant_team_label === 'A' ? left : right;
    candidates.push({
      title: `Największa intensywność: ${displayTeamName(team, 'drużyna')}`,
      detail: `Okno ${strongestMomentum.label}`,
    });
  }
  const distanceInsight = greaterTeamInsight(left, right, 'total_distance_m', 'pokonało większy dystans');
  if (distanceInsight) candidates.push(distanceInsight);
  return candidates.slice(0, 3);
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
