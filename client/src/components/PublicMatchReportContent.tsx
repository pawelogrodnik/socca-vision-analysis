import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useMemo, useState } from 'react';
import type { PublicMatchReport, PublicReportPlayer, PublicReportTeam } from '../types';
import { AttackingMomentumChart } from './AttackingMomentumChart';
import { PublicPlayerHeatmap } from './PublicPlayerHeatmap';

type PublicMatchReportContentProps = {
  report: PublicMatchReport;
  assetHref?: (path: string) => string;
};

type PlayerChartMetric = 'minutes' | 'distanceKm' | 'peakSpeed';

type PlayerChartRow = {
  name: string;
  minutes: number;
  distanceKm: number;
  peakSpeed: number;
};

type PlayerChartMetricConfig = {
  key: PlayerChartMetric;
  label: string;
  buttonLabel: string;
  color: string;
  axisFormatter: (value: number) => string;
  tooltipFormatter: (value: number) => string;
};

type PublicTooltipItem = {
  color?: string;
  name?: string | number;
  value?: unknown;
};

type PublicChartTooltipProps = {
  active?: boolean;
  label?: string | number;
  payload?: readonly PublicTooltipItem[];
  titleFormatter: (label: string | number | undefined) => string;
  valueFormatter: (value: unknown, name?: string | number) => string;
};

const PLAYER_CHART_METRICS: PlayerChartMetricConfig[] = [
  {
    key: 'minutes',
    label: 'Rozegrane minuty',
    buttonLabel: 'Minuty',
    color: '#f8fafc',
    axisFormatter: (value) => `${Math.round(value)}m`,
    tooltipFormatter: (value) => `${value.toFixed(1)} min`,
  },
  {
    key: 'distanceKm',
    label: 'Przebiegniety dystans',
    buttonLabel: 'Dystans',
    color: '#38bdf8',
    axisFormatter: (value) => `${value.toFixed(1)} km`,
    tooltipFormatter: (value) => `${value.toFixed(2)} km`,
  },
  {
    key: 'peakSpeed',
    label: 'Max speed',
    buttonLabel: 'Max speed',
    color: '#22c55e',
    axisFormatter: (value) => `${Math.round(value)} km/h`,
    tooltipFormatter: (value) => `${value.toFixed(1)} km/h`,
  },
];

function formatMeters(value: number | undefined): string {
  return `${(value || 0).toFixed(1)} m`;
}

function formatSpeed(value: number | undefined): string {
  return `${(value || 0).toFixed(1)} km/h`;
}

function formatSeconds(value: number | undefined): string {
  const safeValue = value || 0;
  if (safeValue < 60) return `${safeValue.toFixed(0)}s`;
  const minutes = Math.floor(safeValue / 60);
  const seconds = Math.round(safeValue % 60);
  return `${minutes}m ${seconds}s`;
}

function formatAdditionalSeconds(value: number | undefined): string {
  return `+${formatSeconds(value)}`;
}

function formatPercent(value: number | null | undefined): string {
  return value == null ? '--' : `${value.toFixed(1)}%`;
}

function reportDateLine(report: PublicMatchReport): string {
  const match = report.match;
  return `${match.match_date || 'brak daty'} | ${match.season || 'brak sezonu'} | ${match.venue || 'brak miejsca'}`;
}

function teamColor(team: PublicReportTeam, fallback: string): string {
  return team.display_color || fallback;
}

function playerLabel(player: PublicReportPlayer): string {
  const number = player.player_number && player.player_number !== 'player' ? `#${player.player_number} ` : '';
  return `${number}${player.player_name || player.player_id}`;
}

function metricRows(left: PublicReportTeam, right: PublicReportTeam) {
  return [
    {
      label: 'Dystans',
      left: formatMeters(left.total_distance_m),
      right: formatMeters(right.total_distance_m),
    },
    {
      label: 'Dlugosc wideo',
      left: '--',
      right: '--',
    },
    {
      label: 'Posiadanie',
      left: formatPercent(left.possession_share_percent),
      right: formatPercent(right.possession_share_percent),
    },
    {
      label: 'Proby podan',
      left: String(left.pass_attempts || 0),
      right: String(right.pass_attempts || 0),
    },
    {
      label: 'Podania celne',
      left: String(left.completed_passes || 0),
      right: String(right.completed_passes || 0),
    },
    {
      label: 'Podania niecelne',
      left: String(left.failed_passes || 0),
      right: String(right.failed_passes || 0),
    },
    {
      label: 'Skutecznosc podan',
      left: formatPercent(left.completion_rate),
      right: formatPercent(right.completion_rate),
    },
    {
      label: 'Progresywne kand.',
      left: String(left.progressive_pass_candidates || 0),
      right: String(right.progressive_pass_candidates || 0),
    },
    {
      label: 'HI distance',
      left: formatMeters(left.high_intensity_distance_m),
      right: formatMeters(right.high_intensity_distance_m),
    },
    {
      label: 'Top speed',
      left: formatSpeed(left.peak_speed_kmh),
      right: formatSpeed(right.peak_speed_kmh),
    },
  ];
}

function chartPercent(value: number): string {
  return `${Math.round(value)}%`;
}

function PublicChartTooltip({
  active,
  label,
  payload,
  titleFormatter,
  valueFormatter,
}: PublicChartTooltipProps) {
  if (!active || !payload?.length) return null;

  return (
    <div className='public-chart-tooltip'>
      <div className='public-chart-tooltip-title'>{titleFormatter(label)}</div>
      <div className='public-chart-tooltip-list'>
        {payload.map((item) => (
          <div className='public-chart-tooltip-row' key={`${String(item.name)}-${String(item.value)}`}>
            <span
              className='public-chart-tooltip-dot'
              style={{ background: item.color || '#94a3b8' }}
            />
            <span className='public-chart-tooltip-name'>{item.name}</span>
            <strong>{valueFormatter(item.value, item.name)}</strong>
          </div>
        ))}
      </div>
    </div>
  );
}

function playerChartRows(players: PublicReportPlayer[]) {
  return players.map((player) => ({
    name: player.player_name || player.player_id,
    minutes: Number(((player.playing_time_sec || 0) / 60).toFixed(1)),
    distanceKm: Number(((player.total_distance_m || 0) / 1000).toFixed(2)),
    peakSpeed: Number((player.peak_speed_kmh || 0).toFixed(1)),
  }));
}

export function PublicMatchReportContent({
  report,
  assetHref = (path) => `/${path}`,
}: PublicMatchReportContentProps) {
  const [playerChartMetric, setPlayerChartMetric] = useState<PlayerChartMetric>('minutes');
  const leftTeam = report.teams[0];
  const rightTeam = report.teams[1];
  const possessionTimeline = report.ball?.possession_timeline || [];
  const playerMetricConfig =
    PLAYER_CHART_METRICS.find((metric) => metric.key === playerChartMetric) || PLAYER_CHART_METRICS[0];
  const playerChartData = useMemo<PlayerChartRow[]>(
    () =>
      playerChartRows(report.players).sort(
        (left, right) => right[playerChartMetric] - left[playerChartMetric],
      ),
    [playerChartMetric, report.players],
  );
  const matchDuration = formatSeconds(report.match.duration_sec);

  return (
    <>
      <section className='card'>
        <div className='row between'>
          <div>
            <h2>{report.match.title}</h2>
            <p className='muted'>{reportDateLine(report)}</p>
          </div>
          <span className='confidence-pill'>Public report</span>
        </div>
          <div className='chips'>
          <span>Format: {report.match.format || '7v7'}</span>
          <span>Czas wideo: {formatSeconds(report.match.duration_sec)}</span>
          <span>Zawodnicy: {report.players.length}</span>
          <span>Known ball: {formatPercent((report.ball?.known_possession_coverage || 0) * 100)}</span>
          <span>Pass attempts: {report.ball?.pass_attempts || 0}</span>
          <span>Completed: {report.ball?.completed_passes || 0}</span>
          <span>Failed: {report.ball?.failed_passes || 0}</span>
        </div>
      </section>

      {leftTeam && rightTeam && (
        <section className='card team-comparison-card'>
          <h2>Statystyki druzyn</h2>
          <div className='team-comparison-header'>
            <div className='team-comparison-side left'>
              <span
                className='team-comparison-swatch'
                style={{ background: teamColor(leftTeam, '#f97316') }}
              />
              <div>
                <strong>{leftTeam.team_name}</strong>
                <span>{leftTeam.team_label || 'Team A'}</span>
              </div>
            </div>
            <div className='team-comparison-title'>MECZ</div>
            <div className='team-comparison-side right'>
              <div>
                <strong>{rightTeam.team_name}</strong>
                <span>{rightTeam.team_label || 'Team B'}</span>
              </div>
              <span
                className='team-comparison-swatch'
                style={{ background: teamColor(rightTeam, '#38bdf8') }}
              />
            </div>
          </div>
          <div className='team-comparison-list'>
            {metricRows(leftTeam, rightTeam).map((row) => {
              const leftText = row.label === 'Dlugosc wideo' ? matchDuration : row.left;
              const rightText = row.label === 'Dlugosc wideo' ? matchDuration : row.right;
              return (
              <div className='team-comparison-row' key={row.label}>
                <div className='team-comparison-value left'>
                  <span>{leftText}</span>
                </div>
                <div className='team-comparison-label'>{row.label}</div>
                <div className='team-comparison-value right'>
                  <span>{rightText}</span>
                </div>
              </div>
              );
            })}
          </div>
          <p className='team-comparison-note'>
            Podania i posiadanie sa jeszcze statystykami eksperymentalnymi.
          </p>
        </section>
      )}

      <section className='card public-charts-card'>
        <h2>Posiadanie w czasie</h2>
        {possessionTimeline.length > 0 ? (
          <div className='public-chart'>
            <ResponsiveContainer width='100%' height='100%'>
              <AreaChart
                data={possessionTimeline}
                margin={{ top: 12, right: 20, left: 0, bottom: 8 }}
              >
                <CartesianGrid stroke='#334155' strokeDasharray='3 3' />
                <XAxis dataKey='label' stroke='#94a3b8' />
                <YAxis domain={[0, 100]} stroke='#94a3b8' tickFormatter={chartPercent} />
                <Tooltip
                  content={({ active, label, payload }) => (
                    <PublicChartTooltip
                      active={active}
                      label={typeof label === 'number' || typeof label === 'string' ? label : undefined}
                      payload={payload as readonly PublicTooltipItem[] | undefined}
                      titleFormatter={(value) => `Minuta ${value || '-'}`}
                      valueFormatter={(value) => `${Number(value || 0).toFixed(1)}%`}
                    />
                  )}
                />
                <Legend />
                <Area
                  type='monotone'
                  dataKey='cumulative_team_a_percent'
                  name={leftTeam?.team_name || 'Team A'}
                  stackId='1'
                  stroke='#f8fafc'
                  fill='rgba(248,250,252,0.78)'
                />
                <Area
                  type='monotone'
                  dataKey='cumulative_team_b_percent'
                  name={rightTeam?.team_name || 'Team B'}
                  stackId='1'
                  stroke='#38bdf8'
                  fill='rgba(56,189,248,0.72)'
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className='muted'>Brak osi czasu possession dla tego raportu.</p>
        )}
        <p className='team-comparison-note'>
          Wykres pokazuje narastajacy procentowy podzial znanego possession od poczatku meczu
          do danego momentu. Minuty z mala liczba probek possession sa orientacyjne.
        </p>
      </section>

      {report.ball?.attacking_momentum?.timeline.length ? (
        <section className='card public-charts-card'>
          <AttackingMomentumChart
            points={report.ball.attacking_momentum.timeline}
            teamAName={leftTeam?.team_name || 'Team A'}
            teamBName={rightTeam?.team_name || 'Team B'}
            teamAColor={leftTeam ? teamColor(leftTeam, '#f8fafc') : '#f8fafc'}
            teamBColor={rightTeam ? teamColor(rightTeam, '#38bdf8') : '#38bdf8'}
            quality={report.ball.attacking_momentum.quality}
            warnings={report.ball.attacking_momentum.warnings}
          />
        </section>
      ) : null}

      <section className='card public-charts-card'>
        <h2>Porownanie graczy</h2>
        <div className='chart-filter-bar' aria-label='Metryka wykresu graczy'>
          {PLAYER_CHART_METRICS.map((metric) => (
            <button
              className={`chart-filter-button${metric.key === playerChartMetric ? ' active' : ''}`}
              key={metric.key}
              type='button'
              onClick={() => setPlayerChartMetric(metric.key)}
            >
              {metric.buttonLabel}
            </button>
          ))}
        </div>
        <div className='public-chart tall'>
          <ResponsiveContainer width='100%' height='100%'>
            <BarChart
              data={playerChartData}
              layout='vertical'
              margin={{ top: 12, right: 28, left: 24, bottom: 8 }}
            >
              <CartesianGrid stroke='#334155' strokeDasharray='3 3' />
              <XAxis
                type='number'
                stroke='#94a3b8'
                tickFormatter={(value) => playerMetricConfig.axisFormatter(Number(value || 0))}
              />
              <YAxis
                dataKey='name'
                interval={0}
                stroke='#94a3b8'
                tickLine={false}
                type='category'
                width={120}
              />
              <Tooltip
                content={({ active, label, payload }) => (
                  <PublicChartTooltip
                    active={active}
                    label={typeof label === 'number' || typeof label === 'string' ? label : undefined}
                    payload={payload as readonly PublicTooltipItem[] | undefined}
                    titleFormatter={(value) => String(value || 'Zawodnik')}
                    valueFormatter={(value) => playerMetricConfig.tooltipFormatter(Number(value || 0))}
                  />
                )}
              />
              <Bar
                dataKey={playerChartMetric}
                name={playerMetricConfig.label}
                fill={playerMetricConfig.color}
                radius={[0, 6, 6, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className='card'>
        <h2>Statystyki graczy</h2>
        <div className='stats-table-wrap'>
          <table className='stats-table'>
            <thead>
              <tr>
                <th>Zawodnik</th>
                <th>Druzyna</th>
                <th title='Unikalne, przypisane detekcje zawodnika'>Czas pewny</th>
                <th title='Klatki niepewne i bezpiecznie domkniete luki ciaglosci'>Mozliwy +</th>
                <th title='Czas pewny powiekszony o mozliwy dodatkowy czas'>Szacowany</th>
                <th>Dystans</th>
                <th>HI dist</th>
                <th>Sprinty</th>
                <th>Avg</th>
                <th>Peak</th>
              </tr>
            </thead>
            <tbody>
              {report.players.map((player) => (
                <tr key={player.player_id}>
                  <td>
                    <strong>{playerLabel(player)}</strong>
                    <span>{player.player_role || 'player'}</span>
                  </td>
                  <td>{player.team_name || player.team_label || 'Team'}</td>
                  <td>{formatSeconds(player.certain_playing_time_sec ?? player.detected_time_sec)}</td>
                  <td>
                    <strong>{formatAdditionalSeconds(player.possible_playing_time_sec)}</strong>
                    <span>
                      ?: {formatSeconds(player.ambiguous_playing_time_sec)} | luki:{' '}
                      {formatSeconds(player.continuity_gap_time_sec)}
                    </span>
                  </td>
                  <td>{formatSeconds(player.playing_time_sec)}</td>
                  <td>{formatMeters(player.total_distance_m)}</td>
                  <td>{formatMeters(player.high_intensity_distance_m)}</td>
                  <td>{player.sprint_count}</td>
                  <td>{formatSpeed(player.avg_speed_kmh)}</td>
                  <td>{formatSpeed(player.peak_speed_kmh)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className='card'>
        <h2>Heatmapy</h2>
        <div className='player-heatmap-grid'>
          {report.players.map((player) => (
            <figure className='player-heatmap' key={`${player.player_id}-heatmap`}>
              <PublicPlayerHeatmap
                alt={`Heatmapa ${playerLabel(player)}`}
                fallbackSrc={player.heatmap?.path ? assetHref(player.heatmap.path) : undefined}
                heatmap={player.heatmap}
              />
              <figcaption>
                {playerLabel(player)}
                <br />
                {player.heatmap?.samples || 0} probek - {player.heatmap?.quality || 'unknown'}
              </figcaption>
            </figure>
          ))}
        </div>
      </section>
    </>
  );
}
