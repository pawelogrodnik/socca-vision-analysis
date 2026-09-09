import { useMemo, useState, type CSSProperties } from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { MatchGroupExternalVideoStatus, PublicMatchReport, PublicReportTeam } from '../types';
import { publicReportPlayersForTeam, publicReportTeamKey } from '../lib/publicReportPresentation';
import {
  balancedPossessionPercentages,
  displayTeamName,
  formatReportDuration,
  formatReportKilometers,
  formatReportPercent,
  formatReportSpeed,
  momentumDisplayBuckets,
} from '../lib/redesignedPublicReportPresentation';
import { PublicPlayerWorkloadSection } from './PublicPlayerWorkloadSection';
import { RedesignedReportHeatmaps } from './RedesignedReportHeatmaps';
import { RedesignedReportPlayers } from './RedesignedReportPlayers';
import { RedesignedReportVideoMoments } from './RedesignedReportVideoMoments';
import { TeamShapeSection } from './TeamShapeSection';

type Props = {
  report: PublicMatchReport;
  externalVideo: MatchGroupExternalVideoStatus | null;
  editorAllowed: boolean;
  onEditKeyMoments: () => void;
};

type ComparisonRow = {
  label: string;
  leftValue: number | null | undefined;
  rightValue: number | null | undefined;
  leftText: string;
  rightText: string;
  scale: 'pair' | 'percent';
  startsGroup?: boolean;
};

const TEAM_A_COLOR = '#39e2c3';
const TEAM_B_COLOR = '#5499ff';
const CHART_TOOLTIP_CONTENT_STYLE = {
  backgroundColor: '#061c32',
  border: '1px solid #3b7895',
  borderRadius: 8,
  boxShadow: '0 12px 28px rgba(0, 0, 0, .38)',
  color: '#effaff',
};
const CHART_TOOLTIP_LABEL_STYLE = { color: '#f8fafc', fontWeight: 800 };
const CHART_TOOLTIP_ITEM_STYLE = { color: '#d8f5ff', fontWeight: 700 };
export const redesignedPossessionDataKeys = {
  teamA: 'cumulative_team_a_percent',
  teamB: 'cumulative_team_b_percent',
} as const;

export function comparisonBarWidth(value: number | null | undefined, scaleMax: number): number {
  if (value == null || !Number.isFinite(value) || scaleMax <= 0) return 0;
  return Math.max(0, Math.min(100, (value / scaleMax) * 100));
}

function TeamBadge({ team, fallback, color }: { team: PublicReportTeam | undefined; fallback: string; color: string }) {
  return <span className='redesign-team-badge' style={{ '--team-color': color } as CSSProperties}>{displayTeamName(team, fallback)}</span>;
}

function comparisonRows(left: PublicReportTeam, right: PublicReportTeam): ComparisonRow[] {
  const possession = balancedPossessionPercentages(left.possession_share_percent, right.possession_share_percent);
  return [
    {
      label: 'Posiadanie',
      leftValue: possession?.left,
      rightValue: possession?.right,
      leftText: formatReportPercent(possession?.left),
      rightText: formatReportPercent(possession?.right),
      scale: 'percent',
    },
    { label: 'Próby podań', leftValue: left.pass_attempts, rightValue: right.pass_attempts, leftText: left.pass_attempts == null ? '—' : String(left.pass_attempts), rightText: right.pass_attempts == null ? '—' : String(right.pass_attempts), scale: 'pair', startsGroup: true },
    { label: 'Podania celne', leftValue: left.completed_passes, rightValue: right.completed_passes, leftText: left.completed_passes == null ? '—' : String(left.completed_passes), rightText: right.completed_passes == null ? '—' : String(right.completed_passes), scale: 'pair' },
    { label: 'Skuteczność podań', leftValue: left.completion_rate, rightValue: right.completion_rate, leftText: formatReportPercent(left.completion_rate), rightText: formatReportPercent(right.completion_rate), scale: 'percent' },
    { label: 'Dystans', leftValue: left.total_distance_m, rightValue: right.total_distance_m, leftText: formatReportKilometers(left.total_distance_m), rightText: formatReportKilometers(right.total_distance_m), scale: 'pair', startsGroup: true },
    { label: 'Dystans wysokiej intensywności', leftValue: left.high_intensity_distance_m, rightValue: right.high_intensity_distance_m, leftText: formatReportKilometers(left.high_intensity_distance_m), rightText: formatReportKilometers(right.high_intensity_distance_m), scale: 'pair' },
    { label: 'Sprinty', leftValue: left.sprint_count, rightValue: right.sprint_count, leftText: left.sprint_count == null ? '—' : String(left.sprint_count), rightText: right.sprint_count == null ? '—' : String(right.sprint_count), scale: 'pair' },
    { label: 'Max speed', leftValue: left.peak_speed_kmh, rightValue: right.peak_speed_kmh, leftText: formatReportSpeed(left.peak_speed_kmh), rightText: formatReportSpeed(right.peak_speed_kmh), scale: 'pair' },
  ];
}

function Hero({ report }: { report: PublicMatchReport }) {
  const duration = formatReportDuration(report.match.duration_sec);
  return (
    <header className='redesign-hero'>
      <div className='redesign-hero-overlay' aria-hidden='true' />
      <div className='redesign-hero-content'>
        <h1>{report.match.title}</h1>
        <div className='redesign-hero-meta'>
          {report.match.match_date ? <span>{report.match.match_date}</span> : null}
          {duration ? <span>Czas analizy: {duration}</span> : null}
        </div>
      </div>
    </header>
  );
}

function MatchFlow({ report }: { report: PublicMatchReport }) {
  const [left, right] = report.teams;
  const possession = report.ball?.possession_timeline || [];
  const momentum = momentumDisplayBuckets(report.ball?.attacking_momentum?.timeline || []);
  if (!possession.length && !momentum.length) return null;
  return (
    <section className='redesign-section redesign-flow-section' aria-labelledby='redesign-flow-title'>
      <div className='redesign-section-heading'>
        <div><p className='redesign-kicker'>Przebieg meczu</p><h2 id='redesign-flow-title'>Match Flow</h2></div>
      </div>
      <div className='redesign-flow-grid'>
        {possession.length ? (
          <article>
            <h3>Posiadanie w czasie</h3>
            <div className='redesign-chart' data-chart-kind='possession-area'>
              <ResponsiveContainer width='100%' height='100%'>
                <AreaChart data={possession} margin={{ top: 8, right: 8, bottom: 4, left: -12 }}>
                  <CartesianGrid stroke='#254965' strokeDasharray='3 3' vertical={false} />
                  <XAxis dataKey='label' stroke='#a8c1d2' />
                  <YAxis domain={[0, 100]} ticks={[0, 50, 100]} tickFormatter={(value) => `${value}%`} stroke='#a8c1d2' />
                  <Tooltip
                    contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
                    labelStyle={CHART_TOOLTIP_LABEL_STYLE}
                    itemStyle={CHART_TOOLTIP_ITEM_STYLE}
                    formatter={(value) => `${Number(value).toFixed(1)}%`}
                  />
                  <Legend />
                  <Area type='monotone' dataKey={redesignedPossessionDataKeys.teamA} name={displayTeamName(left, 'Drużyna A')} stackId='possession' stroke={TEAM_A_COLOR} fill={TEAM_A_COLOR} fillOpacity={0.84} />
                  <Area type='monotone' dataKey={redesignedPossessionDataKeys.teamB} name={displayTeamName(right, 'Drużyna B')} stackId='possession' stroke={TEAM_B_COLOR} fill={TEAM_B_COLOR} fillOpacity={0.84} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </article>
        ) : null}
        {momentum.length ? (
          <article>
            <h3>Momentum meczu</h3>
            <div className='redesign-chart' data-chart-kind='diverging-momentum'>
              <ResponsiveContainer width='100%' height='100%'>
                <BarChart data={momentum} margin={{ top: 8, right: 8, bottom: 4, left: -12 }}>
                  <CartesianGrid stroke='#254965' strokeDasharray='3 3' vertical={false} />
                  <XAxis dataKey='start_time_sec' tickFormatter={(value) => `${Math.floor(Number(value) / 60)}′`} stroke='#a8c1d2' />
                  <YAxis stroke='#a8c1d2' />
                  <ReferenceLine y={0} stroke='#e4f2fa' strokeWidth={1.5} />
                  <Tooltip
                    contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
                    labelStyle={CHART_TOOLTIP_LABEL_STYLE}
                    itemStyle={CHART_TOOLTIP_ITEM_STYLE}
                    labelFormatter={(value) => `${Math.floor(Number(value) / 60)}–${Math.floor(Number(value) / 60) + 1} min`}
                    formatter={(value) => {
                      const signedScore = Number(value);
                      const team = signedScore >= 0 ? displayTeamName(left, 'Drużyna A') : displayTeamName(right, 'Drużyna B');
                      return [`${signedScore >= 0 ? '+' : ''}${signedScore.toFixed(0)}`, `Przewaga: ${team}`];
                    }}
                  />
                  <Bar dataKey='signed_score' name='Przewaga' radius={[3, 3, 0, 0]}>
                    {momentum.map((point) => <Cell key={point.start_time_sec} fill={point.signed_score >= 0 ? TEAM_A_COLOR : TEAM_B_COLOR} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </article>
        ) : null}
      </div>
    </section>
  );
}

function TeamComparison({ report }: { report: PublicMatchReport }) {
  const [left, right] = report.teams;
  if (!left || !right) return null;
  return (
    <section className='redesign-section redesign-comparison-section' aria-labelledby='redesign-comparison-title'>
      <div className='redesign-section-heading'>
        <div><p className='redesign-kicker'>Drużyny</p><h2 id='redesign-comparison-title'>Porównanie drużyn</h2></div>
      </div>
      <div className='redesign-comparison-header'>
        <TeamBadge team={left} fallback='Drużyna A' color={TEAM_A_COLOR} />
        <span>Statystyka</span>
        <TeamBadge team={right} fallback='Drużyna B' color={TEAM_B_COLOR} />
      </div>
      <div className='redesign-comparison-rows'>
        {comparisonRows(left, right).map((row) => {
          const scaleMax = row.scale === 'percent' ? 100 : Math.max(Number(row.leftValue) || 0, Number(row.rightValue) || 0, 1);
          const leftWidth = comparisonBarWidth(row.leftValue, scaleMax);
          const rightWidth = comparisonBarWidth(row.rightValue, scaleMax);
          return (
            <div className={`redesign-comparison-row${row.startsGroup ? ' group-start' : ''}`} key={row.label}>
              <strong>{row.leftText}</strong>
              <span className='redesign-comparison-bar left'><i style={{ width: `${leftWidth}%` }} /></span>
              <span>{row.label}</span>
              <span className='redesign-comparison-bar right'><i style={{ width: `${rightWidth}%` }} /></span>
              <strong>{row.rightText}</strong>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function RedesignedPublishedReportContent({ report, externalVideo, editorAllowed, onEditKeyMoments }: Props) {
  const teamOptions = report.teams.map((team, index) => ({ key: publicReportTeamKey(team, index), team }));
  const [selectedTeamKey, setSelectedTeamKey] = useState<string | null>(teamOptions[0]?.key || null);
  const selectedTeam = teamOptions.find((item) => item.key === selectedTeamKey)?.team || teamOptions[0]?.team;
  const selectedPlayers = useMemo(() => publicReportPlayersForTeam(report.players, selectedTeam), [report.players, selectedTeam]);
  const assetHref = (path: string) => path.startsWith('http') || path.startsWith('/') ? path : `/${path}`;

  return (
    <div className='redesigned-report'>
      <Hero report={report} />
      <div className='redesigned-report-content'>
        <RedesignedReportVideoMoments report={report} externalVideo={externalVideo} editorAllowed={editorAllowed} onEditKeyMoments={onEditKeyMoments} />
        <MatchFlow report={report} />
        <TeamComparison report={report} />
        <RedesignedReportPlayers players={report.players} teams={report.teams} selectedTeam={selectedTeam} selectedTeamKey={selectedTeamKey} onSelectTeam={setSelectedTeamKey} />
        <PublicPlayerWorkloadSection players={selectedPlayers} teamName={selectedTeam?.team_name || selectedTeam?.team_label} teamColor={selectedTeam?.display_color} variant='redesigned' />
        <RedesignedReportHeatmaps players={selectedPlayers} assetHref={assetHref} />
        {report.team_shape ? <TeamShapeSection reportTeams={report.teams} teamShape={report.team_shape} /> : null}
      </div>
    </div>
  );
}
