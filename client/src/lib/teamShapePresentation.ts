import type { TeamShapeDocument } from '../types';

export type TeamShapeMetricKey =
  | 'width_m'
  | 'depth_m'
  | 'compactness_m'
  | 'block_height_percent';

export const TEAM_SHAPE_METRICS: Array<{
  key: TeamShapeMetricKey;
  label: string;
  description: string;
}> = [
  {
    key: 'width_m',
    label: 'Szerokość',
    description: 'Jak szeroko zespół rozciągał ustawienie na boisku.',
  },
  {
    key: 'depth_m',
    label: 'Długość ustawienia',
    description: 'Odległość między najniżej i najwyżej ustawionym zawodnikiem.',
  },
  {
    key: 'compactness_m',
    label: 'Zwartość',
    description: 'Jak blisko zawodnicy znajdowali się środka ustawienia. Mniejsza wartość oznacza ciaśniejsze ustawienie.',
  },
  {
    key: 'block_height_percent',
    label: 'Wysokość ustawienia',
    description: 'Położenie środka drużyny między własną a bramką przeciwnika.',
  },
];

export function shouldRenderTeamShape(teamShape?: TeamShapeDocument | null): boolean {
  return teamShape?.available === true && (teamShape.teams?.length ?? 0) === 2;
}

export function formatTeamShapeValue(value: number | null | undefined, metric: TeamShapeMetricKey): string {
  if (value == null) return '--';
  if (metric === 'block_height_percent') return `${Math.round(value)}%`;
  return `${value.toFixed(1).replace('.', ',')} m`;
}

export function teamShapeSummaryValue(
  summary: NonNullable<NonNullable<TeamShapeDocument['teams']>[number]['summary']>,
  metric: TeamShapeMetricKey,
): number | undefined {
  if (metric === 'width_m') return summary.average_width_m;
  if (metric === 'depth_m') return summary.average_depth_m;
  if (metric === 'compactness_m') return summary.average_compactness_m;
  return summary.average_block_height_percent;
}

export function buildTeamShapeTimeline(
  teamShape: TeamShapeDocument,
  metric: TeamShapeMetricKey,
): Array<Record<string, string | number | null>> {
  const rows = new Map<string, Record<string, string | number | null>>();
  for (const team of teamShape.teams ?? []) {
    const teamKey = `team_${team.team_label || team.team_id || 'unknown'}`;
    for (const point of team.timeline ?? []) {
      const label = point.label || String(point.minute ?? '');
      const row = rows.get(label) ?? { label };
      row[teamKey] = point[metric] ?? null;
      rows.set(label, row);
    }
  }
  return [...rows.values()];
}