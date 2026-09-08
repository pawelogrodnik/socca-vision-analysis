import { useMemo, useState } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { MatchGroupExternalVideoStatus, PublicMatchReport, PublicReportTeam } from '../types';
import { PublicPlayerWorkloadSection } from './PublicPlayerWorkloadSection';
import { RedesignedReportPlayers } from './RedesignedReportPlayers';
import { RedesignedReportVideoMoments } from './RedesignedReportVideoMoments';
import { TeamShapeSection } from './TeamShapeSection';
import { publicReportPlayersForTeam, publicReportTeamKey } from '../lib/publicReportPresentation';
import {
  displayTeamName,
  formatReportDuration,
  formatReportKilometers,
  formatReportPercent,
  formatReportSpeed,
  publicReportInsights,
  teamReportColor,
} from '../lib/redesignedPublicReportPresentation';

type Props = {
  report: PublicMatchReport;
  externalVideo: MatchGroupExternalVideoStatus | null;
  editorAllowed: boolean;
  onEditKeyMoments: () => void;
};

function TeamBadge({ team, fallback }: { team: PublicReportTeam | undefined; fallback: string }) {
  return <span className='redesign-team-badge' style={{ '--team-color': teamReportColor(team, fallback) } as React.CSSProperties}>{displayTeamName(team, fallback)}</span>;
}

function summaryRows(left: PublicReportTeam, right: PublicReportTeam) {
  return [
    ['Posiadanie', formatReportPercent(left.possession_share_percent), formatReportPercent(right.possession_share_percent)],
    ['Skuteczność podań', formatReportPercent(left.completion_rate), formatReportPercent(right.completion_rate)],
    ['Dystans', formatReportKilometers(left.total_distance_m), formatReportKilometers(right.total_distance_m)],
    ['Dystans wysokiej intensywności', formatReportKilometers(left.high_intensity_distance_m), formatReportKilometers(right.high_intensity_distance_m)],
    ['Sprinty', left.sprint_count == null ? '—' : String(left.sprint_count), right.sprint_count == null ? '—' : String(right.sprint_count)],
    ['Max speed', formatReportSpeed(left.peak_speed_kmh), formatReportSpeed(right.peak_speed_kmh)],
    ['Próby podań', left.pass_attempts == null ? '—' : String(left.pass_attempts), right.pass_attempts == null ? '—' : String(right.pass_attempts)],
    ['Podania celne', left.completed_passes == null ? '—' : String(left.completed_passes), right.completed_passes == null ? '—' : String(right.completed_passes)],
  ];
}

function Hero({ report }: { report: PublicMatchReport }) {
  const [left, right] = report.teams;
  const duration = formatReportDuration(report.match.duration_sec);
  const memberCount = report.merged_provenance?.sources?.length;
  return <header className='redesign-hero'>
    <div className='redesign-hero-overlay' aria-hidden='true' />
    <div className='redesign-hero-content'>
      <div className='redesign-matchup'><TeamBadge team={left} fallback='Drużyna A' /><span>vs</span><TeamBadge team={right} fallback='Drużyna B' /></div>
      <h1>{report.match.title}</h1>
      <div className='redesign-hero-meta'>
        {report.match.match_date ? <span>{report.match.match_date}</span> : null}
        {duration ? <span>Czas analizy: {duration}</span> : null}
        {memberCount ? <span>Scalony mecz · {memberCount} fragment{memberCount === 1 ? '' : 'y'}</span> : null}
      </div>
      <p>Szczegółowa analiza drużyn, zawodników i przebiegu dostępnego materiału.</p>
    </div>
  </header>;
}

function QuickSummary({ report }: { report: PublicMatchReport }) {
  const [left, right] = report.teams;
  if (!left || !right) return null;
  const cards = [
    ['Posiadanie', formatReportPercent(left.possession_share_percent), formatReportPercent(right.possession_share_percent)],
    ['Skuteczność podań', formatReportPercent(left.completion_rate), formatReportPercent(right.completion_rate)],
    ['Sprinty', left.sprint_count == null ? '—' : String(left.sprint_count), right.sprint_count == null ? '—' : String(right.sprint_count)],
    ['Dystans', formatReportKilometers(left.total_distance_m), formatReportKilometers(right.total_distance_m)],
    ['Max speed', formatReportSpeed(left.peak_speed_kmh), formatReportSpeed(right.peak_speed_kmh)],
  ];
  return <section className='redesign-kpi-grid' aria-label='Szybkie podsumowanie'>{cards.map(([label, leftValue, rightValue]) => <article key={label}><h2>{label}</h2><div><strong>{leftValue}</strong><span>{displayTeamName(left, 'Drużyna A')}</span></div><div><strong>{rightValue}</strong><span>{displayTeamName(right, 'Drużyna B')}</span></div></article>)}</section>;
}

function Insights({ report }: { report: PublicMatchReport }) {
  const insights = publicReportInsights(report);
  if (!insights.length) return null;
  return <section className='redesign-section redesign-insights' aria-labelledby='redesign-insights-title'><div className='redesign-section-heading'><div><p className='redesign-kicker'>Podsumowanie</p><h2 id='redesign-insights-title'>Najważniejsze wnioski</h2></div></div><div>{insights.map((insight) => <article key={insight.title}><h3>{insight.title}</h3><p>{insight.detail}</p></article>)}</div></section>;
}

function MatchFlow({ report }: { report: PublicMatchReport }) {
  const [left, right] = report.teams;
  const possession = report.ball?.possession_timeline || [];
  const momentum = report.ball?.attacking_momentum?.timeline || [];
  if (!possession.length && !momentum.length) return null;
  return <section className='redesign-section redesign-flow-section' aria-labelledby='redesign-flow-title'><div className='redesign-section-heading'><div><p className='redesign-kicker'>Przebieg meczu</p><h2 id='redesign-flow-title'>Match Flow</h2></div></div><div className='redesign-flow-grid'>
    {possession.length ? <article><h3>Posiadanie w czasie</h3><div className='redesign-chart'><ResponsiveContainer width='100%' height='100%'><AreaChart data={possession} margin={{ top: 8, right: 8, bottom: 4, left: -12 }}><CartesianGrid stroke='#23405c' vertical={false} /><XAxis dataKey='label' stroke='#9fb4c9' /><YAxis domain={[0, 100]} tickFormatter={(value) => `${value}%`} stroke='#9fb4c9' /><Tooltip formatter={(value) => `${Number(value).toFixed(1)}%`} /><Legend /><Area type='monotone' dataKey='team_a_percent' name={displayTeamName(left, 'Drużyna A')} stroke={teamReportColor(left, '#38e0c1')} fill={teamReportColor(left, '#38e0c1')} fillOpacity={0.48} /><Area type='monotone' dataKey='team_b_percent' name={displayTeamName(right, 'Drużyna B')} stroke={teamReportColor(right, '#5b9cff')} fill={teamReportColor(right, '#5b9cff')} fillOpacity={0.38} /></AreaChart></ResponsiveContainer></div></article> : null}
    {momentum.length ? <article><h3>Momentum meczu</h3><div className='redesign-chart'><ResponsiveContainer width='100%' height='100%'><AreaChart data={momentum} margin={{ top: 8, right: 8, bottom: 4, left: -12 }}><CartesianGrid stroke='#23405c' vertical={false} /><XAxis dataKey='time_sec' tickFormatter={(value) => `${Math.floor(Number(value) / 60)}′`} stroke='#9fb4c9' /><YAxis stroke='#9fb4c9' /><Tooltip labelFormatter={(value) => `${Math.floor(Number(value) / 60)}:${String(Math.floor(Number(value) % 60)).padStart(2, '0')}`} formatter={(value) => Number(value).toFixed(0)} /><Legend /><Area type='linear' dataKey='team_a_value' name={displayTeamName(left, 'Drużyna A')} stroke={teamReportColor(left, '#38e0c1')} fill={teamReportColor(left, '#38e0c1')} fillOpacity={0.55} /><Area type='linear' dataKey='team_b_value' name={displayTeamName(right, 'Drużyna B')} stroke={teamReportColor(right, '#5b9cff')} fill={teamReportColor(right, '#5b9cff')} fillOpacity={0.5} /></AreaChart></ResponsiveContainer></div></article> : null}
  </div></section>;
}

function TeamComparison({ report }: { report: PublicMatchReport }) {
  const [left, right] = report.teams;
  if (!left || !right) return null;
  return <section className='redesign-section redesign-comparison-section' aria-labelledby='redesign-comparison-title'><div className='redesign-section-heading'><div><p className='redesign-kicker'>Drużyny</p><h2 id='redesign-comparison-title'>Porównanie drużyn</h2></div></div><div className='redesign-comparison-header'><TeamBadge team={left} fallback='Drużyna A' /><span>Statystyka</span><TeamBadge team={right} fallback='Drużyna B' /></div><div className='redesign-comparison-rows'>{summaryRows(left, right).map(([label, leftValue, rightValue]) => <div key={label}><strong>{leftValue}</strong><span>{label}</span><strong>{rightValue}</strong></div>)}</div></section>;
}

export function RedesignedPublishedReportContent({ report, externalVideo, editorAllowed, onEditKeyMoments }: Props) {
  const teamOptions = report.teams.map((team, index) => ({ key: publicReportTeamKey(team, index), team }));
  const [selectedTeamKey, setSelectedTeamKey] = useState<string | null>(teamOptions[0]?.key || null);
  const selectedTeam = teamOptions.find((item) => item.key === selectedTeamKey)?.team || teamOptions[0]?.team;
  const selectedPlayers = useMemo(() => publicReportPlayersForTeam(report.players, selectedTeam), [report.players, selectedTeam]);
  return <div className='redesigned-report'>
    <Hero report={report} />
    <div className='redesigned-report-content'>
      <QuickSummary report={report} />
      <Insights report={report} />
      <RedesignedReportVideoMoments report={report} externalVideo={externalVideo} editorAllowed={editorAllowed} onEditKeyMoments={onEditKeyMoments} />
      <MatchFlow report={report} />
      <TeamComparison report={report} />
      {report.team_shape ? <TeamShapeSection reportTeams={report.teams} teamShape={report.team_shape} /> : null}
      <RedesignedReportPlayers players={report.players} teams={report.teams} selectedTeam={selectedTeam} selectedTeamKey={selectedTeamKey} onSelectTeam={setSelectedTeamKey} assetHref={(path) => path.startsWith('http') || path.startsWith('/') ? path : `/${path}`} />
      <PublicPlayerWorkloadSection players={selectedPlayers} teamName={selectedTeam?.team_name || selectedTeam?.team_label} teamColor={selectedTeam?.display_color} />
    </div>
  </div>;
}
