import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { AggregatePublicMatchReport } from '../types';

function clock(seconds: number): string {
  const value = Math.max(0, Math.round(seconds));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}

function chartRows(
  rows: Array<{ start_time_sec: number; end_time_sec: number; values?: Record<string, number | null> }>,
  teamIds: string[],
) {
  return rows.map((row) => ({ time_sec: row.start_time_sec, ...Object.fromEntries(teamIds.map((teamId) => [teamId, row.values?.[teamId] ?? null])) }));
}

export function AggregateMatchTimeline({ report }: { report: AggregatePublicMatchReport }) {
  const teams = report.teams.map((team, index) => ({ id: team.team_id, name: team.team_name || team.team_id, color: team.display_color || (index === 0 ? '#f8fafc' : '#38bdf8') }));
  const possessionRows = chartRows(
    (report.timelines?.possession?.windows || []).map((window) => ({
      ...window,
      values: window.possession_share_percent_by_team_id,
    })),
    teams.map((team) => team.id),
  );
  const momentumRows = chartRows(
    (report.timelines?.attacking_momentum?.points || []).map((point) => ({
      ...point,
      values: point.team_values_by_team_id,
    })),
    teams.map((team) => team.id),
  );
  const momentumExperimental = report.timelines?.attacking_momentum?.product_readiness === 'experimental';

  return <section className='panel'>
    <h2>Timeline</h2>
    {possessionRows.length > 0 && <div className='public-chart'><h3>Posiadanie w czasie</h3>
      <ResponsiveContainer width='100%' height={260}><LineChart data={possessionRows} margin={{ top: 12, right: 16, bottom: 6, left: 0 }}>
        <CartesianGrid stroke='#334155' strokeDasharray='3 3' vertical={false} />
        <XAxis dataKey='time_sec' tickFormatter={clock} stroke='#94a3b8' />
        <YAxis domain={[0, 100]} tickFormatter={(value) => `${value}%`} stroke='#94a3b8' />
        <Tooltip labelFormatter={(value) => clock(Number(value))} formatter={(value) => value == null ? '—' : `${Number(value).toFixed(1)}%`} />
        <Legend />
        {teams.map((team) => <Line key={team.id} type='stepAfter' dataKey={team.id} name={team.name} stroke={team.color} dot={false} isAnimationActive={false} />)}
      </LineChart></ResponsiveContainer>
    </div>}
    {momentumRows.length > 0 && <div className='public-chart'><h3>Atakujące momentum {momentumExperimental && <span className='muted'>— eksperymentalne</span>}</h3>
      <ResponsiveContainer width='100%' height={260}><LineChart data={momentumRows} margin={{ top: 12, right: 16, bottom: 6, left: 0 }}>
        <CartesianGrid stroke='#334155' strokeDasharray='3 3' vertical={false} />
        <XAxis dataKey='time_sec' tickFormatter={clock} stroke='#94a3b8' />
        <YAxis stroke='#94a3b8' />
        <Tooltip labelFormatter={(value) => clock(Number(value))} formatter={(value) => value == null ? '—' : Number(value).toFixed(2)} />
        <Legend />
        {teams.map((team) => <Line key={team.id} type='linear' dataKey={team.id} name={team.name} stroke={team.color} dot={false} isAnimationActive={false} />)}
      </LineChart></ResponsiveContainer>
    </div>}
    {!possessionRows.length && !momentumRows.length && <p>Timeline niedostępny dla tych źródeł.</p>}
  </section>;
}
