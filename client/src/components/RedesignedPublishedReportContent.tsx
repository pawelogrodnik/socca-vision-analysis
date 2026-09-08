import { useMemo, useState, type CSSProperties } from 'react';
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { MatchGroupExternalVideoStatus, PublicMatchReport, PublicReportTeam } from '../types';
import { PublicPlayerWorkloadSection } from './PublicPlayerWorkloadSection';
import { RedesignedReportHeatmaps } from './RedesignedReportHeatmaps';
import { RedesignedReportPlayers } from './RedesignedReportPlayers';
import { RedesignedReportVideoMoments } from './RedesignedReportVideoMoments';
import { TeamShapeSection } from './TeamShapeSection';
import { publicReportPlayersForTeam, publicReportTeamKey } from '../lib/publicReportPresentation';
import { displayTeamName, formatReportDuration, formatReportKilometers, formatReportPercent, formatReportSpeed } from '../lib/redesignedPublicReportPresentation';

type Props = { report: PublicMatchReport; externalVideo: MatchGroupExternalVideoStatus | null; editorAllowed: boolean; onEditKeyMoments: () => void };
type ComparisonRow = { label: string; leftValue: number | null | undefined; rightValue: number | null | undefined; leftText: string; rightText: string };
const TEAM_A_COLOR = '#39e2c3';
const TEAM_B_COLOR = '#5499ff';

function TeamBadge({ team, fallback, color }: { team: PublicReportTeam | undefined; fallback: string; color: string }) {
  return <span className='redesign-team-badge' style={{ '--team-color': color } as CSSProperties}>{displayTeamName(team, fallback)}</span>;
}

function comparisonRows(left: PublicReportTeam, right: PublicReportTeam): ComparisonRow[] {
  return [
    { label: 'Posiadanie', leftValue: left.possession_share_percent, rightValue: right.possession_share_percent, leftText: formatReportPercent(left.possession_share_percent), rightText: formatReportPercent(right.possession_share_percent) },
    { label: 'Skuteczność podań', leftValue: left.completion_rate, rightValue: right.completion_rate, leftText: formatReportPercent(left.completion_rate), rightText: formatReportPercent(right.completion_rate) },
    { label: 'Dystans', leftValue: left.total_distance_m, rightValue: right.total_distance_m, leftText: formatReportKilometers(left.total_distance_m), rightText: formatReportKilometers(right.total_distance_m) },
    { label: 'Dystans wysokiej intensywności', leftValue: left.high_intensity_distance_m, rightValue: right.high_intensity_distance_m, leftText: formatReportKilometers(left.high_intensity_distance_m), rightText: formatReportKilometers(right.high_intensity_distance_m) },
    { label: 'Sprinty', leftValue: left.sprint_count, rightValue: right.sprint_count, leftText: left.sprint_count == null ? '—' : String(left.sprint_count), rightText: right.sprint_count == null ? '—' : String(right.sprint_count) },
    { label: 'Max speed', leftValue: left.peak_speed_kmh, rightValue: right.peak_speed_kmh, leftText: formatReportSpeed(left.peak_speed_kmh), rightText: formatReportSpeed(right.peak_speed_kmh) },
    { label: 'Próby podań', leftValue: left.pass_attempts, rightValue: right.pass_attempts, leftText: left.pass_attempts == null ? '—' : String(left.pass_attempts), rightText: right.pass_attempts == null ? '—' : String(right.pass_attempts) },
    { label: 'Podania celne', leftValue: left.completed_passes, rightValue: right.completed_passes, leftText: left.completed_passes == null ? '—' : String(left.completed_passes), rightText: right.completed_passes == null ? '—' : String(right.completed_passes) },
  ];
}

function Hero({ report }: { report: PublicMatchReport }) {
  const duration = formatReportDuration(report.match.duration_sec);
  return <header className='redesign-hero'><div className='redesign-hero-overlay' aria-hidden='true' /><div className='redesign-hero-content'><h1>{report.match.title}</h1><div className='redesign-hero-meta'>{report.match.match_date ? <span>{report.match.match_date}</span> : null}{duration ? <span>Czas analizy: {duration}</span> : null}</div><p>Szczegółowa analiza drużyn, zawodników i przebiegu dostępnego materiału.</p></div></header>;
}

function MatchFlow({ report }: { report: PublicMatchReport }) {
  const [left, right] = report.teams;
  const possession = report.ball?.possession_timeline || [];
  const momentum = report.ball?.attacking_momentum?.timeline || [];
  if (!possession.length && !momentum.length) return null;
  return <section className='redesign-section redesign-flow-section' aria-labelledby='redesign-flow-title'><div className='redesign-section-heading'><div><p className='redesign-kicker'>Przebieg meczu</p><h2 id='redesign-flow-title'>Match Flow</h2></div></div><div className='redesign-flow-grid'>
    {possession.length ? <article><h3>Posiadanie w czasie</h3><div className='redesign-chart' data-chart-kind='possession-area'><ResponsiveContainer width='100%' height='100%'><AreaChart data={possession} margin={{ top: 8, right: 8, bottom: 4, left: -12 }}><CartesianGrid stroke='#254965' strokeDasharray='3 3' vertical={false} /><XAxis dataKey='label' stroke='#a8c1d2' /><YAxis domain={[0, 100]} ticks={[0, 50, 100]} tickFormatter={(value) => `${value}%`} stroke='#a8c1d2' /><Tooltip formatter={(value) => `${Number(value).toFixed(1)}%`} /><Legend /><Area type='monotone' dataKey='team_a_percent' name={displayTeamName(left, 'Drużyna A')} stackId='possession' stroke={TEAM_A_COLOR} fill={TEAM_A_COLOR} fillOpacity={0.84} /><Area type='monotone' dataKey='team_b_percent' name={displayTeamName(right, 'Drużyna B')} stackId='possession' stroke={TEAM_B_COLOR} fill={TEAM_B_COLOR} fillOpacity={0.84} /></AreaChart></ResponsiveContainer></div></article> : null}
    {momentum.length ? <article><h3>Momentum meczu</h3><div className='redesign-chart' data-chart-kind='diverging-momentum'><ResponsiveContainer width='100%' height='100%'><BarChart data={momentum} margin={{ top: 8, right: 8, bottom: 4, left: -12 }}><CartesianGrid stroke='#254965' strokeDasharray='3 3' vertical={false} /><XAxis dataKey='time_sec' tickFormatter={(value) => `${Math.floor(Number(value) / 60)}′`} stroke='#a8c1d2' /><YAxis stroke='#a8c1d2' /><ReferenceLine y={0} stroke='#e4f2fa' strokeWidth={1.5} /><Tooltip labelFormatter={(value) => `${Math.floor(Number(value) / 60)}:${String(Math.floor(Number(value) % 60)).padStart(2, '0')}`} formatter={(value) => [Math.abs(Number(value)).toFixed(0), Number(value) >= 0 ? displayTeamName(left, 'Drużyna A') : displayTeamName(right, 'Drużyna B')]} /><Bar dataKey='signed_score' name='Przewaga' radius={[3, 3, 0, 0]}>{momentum.map((point) => <Cell key={point.index} fill={point.signed_score >= 0 ? TEAM_A_COLOR : TEAM_B_COLOR} />)}</Bar></BarChart></ResponsiveContainer></div></article> : null}
  </div></section>;
}

function TeamComparison({ report }: { report: PublicMatchReport }) {
  const [left, right] = report.teams;
  if (!left || !right) return null;
  return <section className='redesign-section redesign-comparison-section' aria-labelledby='redesign-comparison-title'><div className='redesign-section-heading'><div><p className='redesign-kicker'>Drużyny</p><h2 id='redesign-comparison-title'>Porównanie drużyn</h2></div></div><div className='redesign-comparison-header'><TeamBadge team={left} fallback='Drużyna A' color={TEAM_A_COLOR} /><span>Statystyka</span><TeamBadge team={right} fallback='Drużyna B' color={TEAM_B_COLOR} /></div><div className='redesign-comparison-rows'>{comparisonRows(left, right).map((row) => { const maximum = Math.max(Number(row.leftValue) || 0, Number(row.rightValue) || 0, 1); const leftWidth = row.leftValue == null ? 0 : (row.leftValue / maximum) * 100; const rightWidth = row.rightValue == null ? 0 : (row.rightValue / maximum) * 100; return <div className='redesign-comparison-row' key={row.label}><strong>{row.leftText}</strong><span className='redesign-comparison-bar left'><i style={{ width: `${leftWidth}%` }} /></span><span>{row.label}</span><span className='redesign-comparison-bar right'><i style={{ width: `${rightWidth}%` }} /></span><strong>{row.rightText}</strong></div>; })}</div></section>;
}

export function RedesignedPublishedReportContent({ report, externalVideo, editorAllowed, onEditKeyMoments }: Props) {
  const teamOptions = report.teams.map((team, index) => ({ key: publicReportTeamKey(team, index), team }));
  const [selectedTeamKey, setSelectedTeamKey] = useState<string | null>(teamOptions[0]?.key || null);
  const selectedTeam = teamOptions.find((item) => item.key === selectedTeamKey)?.team || teamOptions[0]?.team;
  const selectedPlayers = useMemo(() => publicReportPlayersForTeam(report.players, selectedTeam), [report.players, selectedTeam]);
  const assetHref = (path: string) => path.startsWith('http') || path.startsWith('/') ? path : `/${path}`;
  return <div className='redesigned-report'><Hero report={report} /><div className='redesigned-report-content'><RedesignedReportVideoMoments report={report} externalVideo={externalVideo} editorAllowed={editorAllowed} onEditKeyMoments={onEditKeyMoments} /><MatchFlow report={report} /><TeamComparison report={report} />{report.team_shape ? <TeamShapeSection reportTeams={report.teams} teamShape={report.team_shape} /> : null}<RedesignedReportPlayers players={report.players} teams={report.teams} selectedTeam={selectedTeam} selectedTeamKey={selectedTeamKey} onSelectTeam={setSelectedTeamKey} /><PublicPlayerWorkloadSection players={selectedPlayers} teamName={selectedTeam?.team_name || selectedTeam?.team_label} teamColor={selectedTeam?.display_color} variant='redesigned' /><RedesignedReportHeatmaps players={selectedPlayers} teamName={selectedTeam?.team_name || selectedTeam?.team_label} assetHref={assetHref} /></div></div>;
}
