import { useState, type CSSProperties } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import {
  buildTeamShapeTimeline,
  formatTeamShapeValue,
  shouldRenderTeamShape,
  TEAM_SHAPE_METRICS,
  teamShapeSummaryValue,
  type TeamShapeMetricKey,
} from '../lib/teamShapePresentation';
import type { PublicReportTeam, TeamShapeDocument } from '../types';

type TeamShapeSectionProps = {
  teamShape?: TeamShapeDocument | null;
  reportTeams: PublicReportTeam[];
};

function teamColor(teamLabel: string | undefined, reportTeams: PublicReportTeam[], fallback: string): string {
  return reportTeams.find((team) => team.team_label === teamLabel)?.display_color || fallback;
}

export function densityVisualLevel(value: number, maximum: number): number {
  const normalized = Math.max(0, value) / Math.max(maximum, 0.001);
  if (normalized < 0.06) return 0;
  if (normalized < 0.22) return 1;
  if (normalized < 0.45) return 2;
  if (normalized < 0.7) return 3;
  if (normalized < 0.88) return 4;
  return 5;
}

function DensityPitch({
  team,
  color,
}: {
  team: NonNullable<TeamShapeDocument['teams']>[number];
  color: string;
}) {
  const shape = team.average_shape;
  if (!shape?.grid.columns || !shape.grid.rows || !Array.isArray(shape.cells)) return null;
  const maximum = Math.max(...shape.cells.map((cell) => cell.value), 0.001);
  const teamName = team.team_name || `Team ${team.team_label || ''}`;
  return (
    <div className='team-shape-pitch-panel' style={{ '--team-accent': color } as CSSProperties}>
      <div className='team-shape-pitch-heading'>
        <strong>{teamName}</strong>
        <span>Kierunek ataku ↑</span>
      </div>
      <div
        aria-label={`Mapa najczęściej zajmowanych stref: ${teamName}. Jaśniejsza intensywność oznacza częściej zajmowaną strefę.`}
        className='team-shape-pitch'
        role='img'
      >
        <div
          aria-hidden='true'
          className='team-shape-density-map'
          style={{
            '--shape-columns': shape.grid.columns,
            '--shape-rows': shape.grid.rows,
          } as CSSProperties}
        >
          {shape.cells.map((cell) => (
            <span
              className={`team-shape-density-cell density-level-${densityVisualLevel(cell.value, maximum)}`}
              key={`${cell.column}-${cell.row}`}
              style={{
                gridColumn: cell.column + 1,
                gridRow: shape.grid.rows - cell.row,
              }}
            />
          ))}
        </div>
        <span className='team-shape-halfway-line' />
        <span className='team-shape-center-circle' />
        <span className='team-shape-penalty-area team-shape-penalty-area-top' />
        <span className='team-shape-penalty-area team-shape-penalty-area-bottom' />
      </div>
    </div>
  );
}

export function TeamShapeSection({ teamShape, reportTeams }: TeamShapeSectionProps) {
  const [metric, setMetric] = useState<TeamShapeMetricKey>('width_m');
  if (!shouldRenderTeamShape(teamShape) || !teamShape?.teams) return null;
  const colors = teamShape.teams.map((team, index) =>
    teamColor(team.team_label, reportTeams, index === 0 ? '#f8fafc' : '#38bdf8'),
  );
  const timeline = buildTeamShapeTimeline(teamShape, metric);
  const activeMetric = TEAM_SHAPE_METRICS.find((item) => item.key === metric) ?? TEAM_SHAPE_METRICS[0];

  return (
    <section className='card team-shape-section'>
      <h2>Ustawienie drużyn</h2>
      <p className='muted'>Jak zespoły rozciągały i zagęszczały ustawienie w trakcie meczu.</p>
      <h3>Charakterystyka ustawienia</h3>
      <div className='team-shape-comparison'>
        {TEAM_SHAPE_METRICS.map((item) => (
          <div className='team-shape-metric-row' key={item.key}>
            <div>
              <strong>{item.label}</strong>
              <span>{item.description}</span>
              {item.key === 'block_height_percent' ? <small>0% — własna bramka · 100% — bramka przeciwnika</small> : null}
            </div>
            <div className='team-shape-team-values'>
              {teamShape.teams?.map((team) => (
                <span key={team.team_label || team.team_id || team.team_name}>
                  <small>{team.team_name || `Team ${team.team_label || ''}`}</small>
                  <b>{formatTeamShapeValue(team.summary ? teamShapeSummaryValue(team.summary, item.key) : null, item.key)}</b>
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className='team-shape-density-heading'>
        <h3>Najczęściej zajmowane strefy</h3>
        <p className='muted'>Jaśniejszy obszar oznacza strefę, w której zawodnicy tej drużyny przebywali częściej.</p>
        <p className='team-shape-density-note'>Intensywność jest normalizowana osobno dla każdej drużyny. Obie mapy pokazano w tym samym kierunku ataku.</p>
      </div>
      <div className='team-shape-pitches'>
        {teamShape.teams.map((team, index) => (
          <DensityPitch color={colors[index]} key={team.team_label || team.team_id || team.team_name} team={team} />
        ))}
      </div>
      <div className='team-shape-density-legend' aria-hidden='true'>
        <span>Rzadziej</span>
        <i />
        <span>Częściej</span>
      </div>
      <div className='team-shape-chart-heading'>
        <div>
          <h3>Zmiany ustawienia w czasie</h3>
          <p>Wybierz metrykę dla wykresu poniżej.</p>
          <p className='team-shape-active-metric'>{activeMetric.description}</p>
        </div>
        <div className='chart-filter-bar' aria-label='Metryka wykresu zmian ustawienia'>
          {TEAM_SHAPE_METRICS.map((item) => (
            <button
              className={`chart-filter-button${item.key === metric ? ' active' : ''}`}
              key={item.key}
              onClick={() => setMetric(item.key)}
              type='button'
            >
              {item.label === 'Długość ustawienia' ? 'Długość' : item.label.replace(' ustawienia', '')}
            </button>
          ))}
        </div>
      </div>
      <div className='public-chart team-shape-chart'>
        <ResponsiveContainer height='100%' width='100%'>
          <LineChart data={timeline} margin={{ top: 12, right: 20, left: 0, bottom: 8 }}>
            <CartesianGrid stroke='#334155' strokeDasharray='3 3' />
            <XAxis dataKey='label' stroke='#94a3b8' />
            <YAxis
              domain={metric === 'block_height_percent' ? [0, 100] : ['auto', 'auto']}
              stroke='#94a3b8'
              tickFormatter={(value) => metric === 'block_height_percent' ? `${value}%` : `${value} m`}
            />
            <Tooltip formatter={(value) => formatTeamShapeValue(Number(value), metric)} />
            <Legend />
            {teamShape.teams.map((team, index) => (
              <Line
                connectNulls={false}
                dataKey={`team_${team.team_label || team.team_id || 'unknown'}`}
                dot={{ r: 3 }}
                key={team.team_label || team.team_id || team.team_name}
                name={team.team_name || `Team ${team.team_label || ''}`}
                stroke={colors[index]}
                strokeWidth={3}
                type='monotone'
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      {teamShape.takeaways?.length ? (
        <div className='team-shape-takeaways'>
          <h3>Najważniejsze różnice</h3>
          <ul>
            {teamShape.takeaways.map((takeaway) => (
              <li key={takeaway}>{takeaway}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
