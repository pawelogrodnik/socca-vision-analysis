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
import { PublicPlayerTeamFilter } from './PublicPlayerTeamFilter';
import { TeamShapeSection } from './TeamShapeSection';
import {
  displayJerseyNumber,
  hasAdvancedPlayerMetrics,
  hasPlayerReadyMomentum,
  publicReportPlayersForTeam,
  publicReportTeamKey,
} from '../lib/publicReportPresentation';

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
    label: 'Czas wykryty',
    buttonLabel: 'Czas wykryty',
    color: '#f8fafc',
    axisFormatter: (value) => `${Math.round(value)}m`,
    tooltipFormatter: (value) => `${value.toFixed(1)} min`,
  },
  {
    key: 'distanceKm',
    label: 'Przebiegnięty dystans',
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
  const jerseyNumber = displayJerseyNumber(player.player_number);
  const number = jerseyNumber ? `#${jerseyNumber} ` : '';
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
      label: 'Długość nagrania',
      left: '--',
      right: '--',
    },
    {
      label: 'Posiadanie',
      left: formatPercent(left.possession_share_percent),
      right: formatPercent(right.possession_share_percent),
    },
    {
      label: 'Próby podań',
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
      label: 'Skuteczność podań',
      left: formatPercent(left.completion_rate),
      right: formatPercent(right.completion_rate),
    },
    {
      label: 'Podania progresywne',
      left: String(left.progressive_pass_candidates || 0),
      right: String(right.progressive_pass_candidates || 0),
    },
    {
      label: 'Dystans wysokiej intensywności',
      left: formatMeters(left.high_intensity_distance_m),
      right: formatMeters(right.high_intensity_distance_m),
    },
    {
      label: 'Prędkość maksymalna',
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
    minutes: Number(((player.detected_time_sec || player.playing_time_sec || 0) / 60).toFixed(1)),
    distanceKm: Number(((player.total_distance_m || 0) / 1000).toFixed(2)),
    peakSpeed: Number((player.peak_speed_kmh || 0).toFixed(1)),
  }));
}

export function PublicMatchReportContent({
  report,
  assetHref = (path) => `/${path}`,
}: PublicMatchReportContentProps) {
  const [playerChartMetric, setPlayerChartMetric] = useState<PlayerChartMetric>('minutes');
  const [selectedTeamKey, setSelectedTeamKey] = useState<string | null>(null);
  const isReviewedReport = report.report_type === 'reviewed_match_report';
  const leftTeam = report.teams[0];
  const rightTeam = report.teams[1];
  const teamOptions = report.teams.map((team, index) => ({
    key: publicReportTeamKey(team, index),
    team,
  }));
  const selectedTeamOption =
    teamOptions.find((option) => option.key === selectedTeamKey) || teamOptions[0];
  const activeTeam = selectedTeamOption?.team;
  const activeTeamKey = selectedTeamOption?.key;
  const visiblePlayers = useMemo(
    () => publicReportPlayersForTeam(report.players, activeTeam),
    [activeTeam, report.players],
  );
  const possessionTimeline = report.ball?.possession_timeline || [];
  const matchDuration = formatSeconds(report.match.duration_sec);
  const showAdvancedPlayerMetrics = hasAdvancedPlayerMetrics(visiblePlayers);
  const playerChartMetrics = showAdvancedPlayerMetrics
    ? PLAYER_CHART_METRICS
    : PLAYER_CHART_METRICS.filter((metric) => metric.key !== 'peakSpeed');
  const effectivePlayerChartMetric = playerChartMetrics.some(
    (metric) => metric.key === playerChartMetric,
  )
    ? playerChartMetric
    : 'minutes';
  const playerMetricConfig =
    PLAYER_CHART_METRICS.find((metric) => metric.key === effectivePlayerChartMetric) ||
    PLAYER_CHART_METRICS[0];
  const playerChartData = useMemo<PlayerChartRow[]>(
    () =>
      playerChartRows(visiblePlayers).sort(
        (left, right) => right[effectivePlayerChartMetric] - left[effectivePlayerChartMetric],
      ),
    [effectivePlayerChartMetric, visiblePlayers],
  );
  const playerReadyMomentum = hasPlayerReadyMomentum(report)
    ? report.ball?.attacking_momentum
    : undefined;

  return (
    <>
      <section className='card'>
        <div className='row between'>
          <div>
            <h2>{report.match.title}</h2>
            <p className='muted'>{reportDateLine(report)}</p>
          </div>
          <span className='confidence-pill'>
            {isReviewedReport ? 'Raport po review' : 'Raport meczu'}
          </span>
        </div>
          <div className='chips'>
          <span>Format: {report.match.format || '7v7'}</span>
          <span>Długość nagrania: {formatSeconds(report.match.duration_sec)}</span>
          <span>Rozpoznani zawodnicy: {report.players.length}</span>
        </div>
        <p className='report-reading-note'>
          Czas drużyny oznacza długość nagrania, a nie sumę czasów wszystkich zawodników.
          Czas zawodnika pokazuje wyłącznie fragmenty, na których został rozpoznany.
        </p>
      </section>

      {report.identity_coverage && (
        <section className='card public-identity-coverage'>
          <div>
            <h2>Kompletność rozpoznania zawodników</h2>
            <p className='muted'>
              Statystyki imienne obejmują tylko fragmenty przypisane konkretnym zawodnikom.
              Rozpoznanie drużyny jest liczone osobno.
            </p>
          </div>
          <div className='public-identity-coverage-grid'>
            {Object.entries(report.identity_coverage.per_team)
              .filter(([team]) => team === 'A' || team === 'B')
              .map(([team, row]) => <div key={team}>
                <strong>Team {team}</strong>
                <span>Imiennie: {Math.round((row.named_observation_coverage || 0) * 100)}%</span>
                <span>Drużyna znana: {Math.round((row.team_known_observation_coverage || 0) * 100)}%</span>
              </div>)}
          </div>
        </section>
      )}

      {leftTeam && rightTeam && (
        <section className='card team-comparison-card'>
          <h2>Statystyki drużyn</h2>
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
              const leftText = row.label === 'Długość nagrania' ? matchDuration : row.left;
              const rightText = row.label === 'Długość nagrania' ? matchDuration : row.right;
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
            Dystans drużyny obejmuje wszystkich wykrytych graczy tej drużyny, także
            nierozpoznanych z imienia. Podania i posiadanie są nadal eksperymentalne.
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
          Wykres pokazuje narastający podział rozpoznanego posiadania od początku meczu.
          Fragmenty z małą liczbą obserwacji są orientacyjne.
        </p>
      </section>

      <TeamShapeSection reportTeams={report.teams} teamShape={report.team_shape} />

      {playerReadyMomentum ? (
        <section className='card public-charts-card'>
          <AttackingMomentumChart
            points={playerReadyMomentum.timeline}
            teamAName={leftTeam?.team_name || 'Team A'}
            teamBName={rightTeam?.team_name || 'Team B'}
            teamAColor={leftTeam ? teamColor(leftTeam, '#f8fafc') : '#f8fafc'}
            teamBColor={rightTeam ? teamColor(rightTeam, '#38bdf8') : '#38bdf8'}
            quality={playerReadyMomentum.signal_quality || playerReadyMomentum.quality}
            warnings={playerReadyMomentum.warnings}
          />
        </section>
      ) : null}

      {teamOptions.length > 0 && (
        <PublicPlayerTeamFilter
          activeTeamKey={activeTeamKey}
          onSelect={setSelectedTeamKey}
          players={report.players}
          teams={report.teams}
        />
      )}

      <section className='card public-charts-card'>
        <h2>
          Porównanie rozpoznanych zawodników
          {activeTeam?.team_name ? ` — ${activeTeam.team_name}` : ''}
        </h2>
        <div className='chart-filter-bar' aria-label='Metryka wykresu graczy'>
          {playerChartMetrics.map((metric) => (
            <button
              className={`chart-filter-button${metric.key === effectivePlayerChartMetric ? ' active' : ''}`}
              key={metric.key}
              type='button'
              onClick={() => setPlayerChartMetric(metric.key)}
            >
              {metric.buttonLabel}
            </button>
          ))}
        </div>
        {playerChartData.length > 0 ? (
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
                dataKey={effectivePlayerChartMetric}
                name={playerMetricConfig.label}
                fill={playerMetricConfig.color}
                radius={[0, 6, 6, 0]}
              />
            </BarChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className='muted player-team-empty'>
            Brak rozpoznanych z imienia zawodników tej drużyny.
          </p>
        )}
      </section>

      <section className='card'>
        <h2>
          Statystyki rozpoznanych zawodników
          {activeTeam?.team_name ? ` — ${activeTeam.team_name}` : ''}
        </h2>
        <div className='stats-table-wrap'>
          <table className='stats-table'>
            <thead>
              <tr>
                <th>Zawodnik</th>
                <th>Drużyna</th>
                <th title='Czas fragmentów nagrania, na których rozpoznano zawodnika'>Czas wykryty</th>
                <th>Dystans</th>
                {showAdvancedPlayerMetrics && <th>Dystans wys. intensywności</th>}
                {showAdvancedPlayerMetrics && <th>Sprinty</th>}
                {showAdvancedPlayerMetrics && <th>Śr. prędkość</th>}
                {showAdvancedPlayerMetrics && <th>Prędkość maks.</th>}
              </tr>
            </thead>
            <tbody>
              {visiblePlayers.map((player) => (
                <tr key={player.player_id}>
                  <td>
                    <strong>{playerLabel(player)}</strong>
                  </td>
                  <td>{player.team_name || player.team_label || 'Team'}</td>
                  <td>{formatSeconds(player.detected_time_sec || player.playing_time_sec)}</td>
                  <td>{formatMeters(player.total_distance_m)}</td>
                  {showAdvancedPlayerMetrics && <td>{formatMeters(player.high_intensity_distance_m)}</td>}
                  {showAdvancedPlayerMetrics && <td>{player.sprint_count}</td>}
                  {showAdvancedPlayerMetrics && <td>{formatSpeed(player.avg_speed_kmh)}</td>}
                  {showAdvancedPlayerMetrics && <td>{formatSpeed(player.peak_speed_kmh)}</td>}
                </tr>
              ))}
              {visiblePlayers.length === 0 && (
                <tr>
                  <td colSpan={showAdvancedPlayerMetrics ? 8 : 4}>
                    Brak rozpoznanych z imienia zawodników tej drużyny.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className='card'>
        <h2>
          Heatmapy rozpoznanych zawodników
          {activeTeam?.team_name ? ` — ${activeTeam.team_name}` : ''}
        </h2>
        {visiblePlayers.length > 0 ? (
          <div className='player-heatmap-grid'>
            {visiblePlayers.map((player) => (
              <figure className='player-heatmap' key={`${player.player_id}-heatmap`}>
                <PublicPlayerHeatmap
                  alt={`Heatmapa ${playerLabel(player)}`}
                  fallbackSrc={player.heatmap?.path ? assetHref(player.heatmap.path) : undefined}
                  heatmap={player.heatmap}
                />
                <figcaption>
                  {playerLabel(player)}
                  <br />
                  {player.team_name || player.team_label || 'Drużyna'}
                </figcaption>
              </figure>
            ))}
          </div>
        ) : (
          <p className='muted player-team-empty'>
            Brak heatmap rozpoznanych z imienia zawodników tej drużyny.
          </p>
        )}
      </section>
    </>
  );
}
