import { Link } from 'react-router-dom';
import type { AggregateMovement, AggregatePublicMatchReport } from '../types';

function number(value: number | null | undefined, suffix = ''): string {
  return value == null ? '—' : `${value.toFixed(1)}${suffix}`;
}

function time(value: number | undefined): string {
  if (value == null) return '—';
  const seconds = Math.max(0, Math.round(value));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function movementCells(movement: AggregateMovement) {
  return <>
    <td>{number(movement.total_distance_m, ' m')}</td>
    <td>{number(movement.high_intensity_distance_m, ' m')}</td>
    <td>{number(movement.sprint_count)}</td>
    <td>{number(movement.peak_speed_kmh, ' km/h')}</td>
  </>;
}

export function AggregateMatchReportContent({ report }: { report: AggregatePublicMatchReport }) {
  const teams = new Map(report.teams.map((team) => [team.team_id, team.team_name || team.team_id]));
  const possession = report.ball?.possession;
  const passes = report.ball?.passes;
  const identity = report.identity_coverage;
  const experimental = report.stats_semantics?.ball === 'experimental_candidates';
  const momentum = report.timelines?.attacking_momentum;

  return <>
    <section className='panel'>
      <h2>Podsumowanie drużyn</h2>
      <div className='table-wrap'><table><thead><tr><th>Drużyna</th><th>Dystans</th><th>Wysoka intensywność</th><th>Sprinty</th><th>Prędkość max.</th><th>Posiadanie</th></tr></thead>
        <tbody>{report.teams.map((team) => <tr key={team.team_id}>
          <td>{team.team_name || team.team_id}</td>{movementCells(team.movement)}
          <td>{number(possession?.possession_share_percent_by_team_id?.[team.team_id], '%')}</td>
        </tr>)}</tbody>
      </table></div>
    </section>

    <section className='panel'>
      <h2>Zawodnicy</h2>
      <div className='table-wrap'><table><thead><tr><th>Zawodnik</th><th>Drużyna</th><th>Dystans</th><th>Obserwowany / szacowany</th><th>Czas wykryty / ruchu</th><th>Śr. / max. prędkość</th><th>HI</th><th>Sprinty</th></tr></thead>
        <tbody>{report.players.map((player) => <tr key={player.player_id}>
          <td>{player.player_name || player.player_id}</td><td>{teams.get(player.team_id) || player.team_id}</td>
          <td>{number(player.movement.total_distance_m, ' m')}</td>
          <td>{number(player.movement.observed_distance_m, ' m')} / {number(player.movement.estimated_short_gap_distance_m, ' m')}</td>
          <td>{time(player.movement.detected_time_sec)} / {time(player.movement.movement_time_sec)}</td>
          <td>{number(player.movement.avg_speed_kmh, ' km/h')} / {number(player.movement.peak_speed_kmh, ' km/h')}</td>
          <td>{number(player.movement.high_intensity_distance_m, ' m')}</td><td>{number(player.movement.sprint_count)}</td>
        </tr>)}</tbody>
      </table></div>
    </section>

    <section className='panel'>
      <h2>Piłka {experimental && <span className='muted'>— eksperymentalne</span>}</h2>
      <p>Posiadanie: {possession?.status || 'not_available'} · znane {number(possession?.known_frames)} · wolne {number(possession?.free_frames)} · nieznane {number(possession?.unknown_frames)}.</p>
      <p>Podania: {passes?.status || 'not_available'} · próby {number(passes?.attempts)} · udane {number(passes?.completed)} · nieudane {number(passes?.failed)} · skuteczność {number(passes?.completion_rate_percent, '%')}.</p>
    </section>

    <section className='panel'>
      <h2>Pokrycie tożsamości</h2>
      <p>{identity?.status || 'not_available'} · potwierdzone {number(identity?.confirmed_observations)} / wiarygodne {number(identity?.reliable_observations)} · pokrycie {number(identity?.confirmed_coverage_percent, '%')} · nierozstrzygnięte {number(identity?.unresolved_observations)}.</p>
    </section>

    <section className='panel'>
      <h2>Timeline</h2>
      <p>Posiadanie: {report.timelines?.possession?.status || 'not_available'} · {report.timelines?.possession?.windows?.length || 0} okien.</p>
      {momentum?.status === 'completed' || momentum?.status === 'ready' ? <p>Atakujący momentum: {momentum.product_readiness || 'not_available'} · {momentum.points?.length || 0} punktów.</p> : <p>Atakujący momentum: {momentum?.status || 'not_available'}.</p>}
    </section>

    <section className='panel'>
      <h2>Źródłowe fragmenty</h2>
      <ol>{report.sources.map((source, index) => {
        const end = report.sources[index + 1]?.logical_offset_sec ?? report.timing.analyzed_duration_sec;
        return <li key={source.published_id}><Link to={`/published/matches/${encodeURIComponent(source.published_id)}/report`}>Fragment {source.sequence_index + 1}</Link> · {time(end - source.logical_offset_sec)}</li>;
      })}</ol>
    </section>

    <section className='panel'>
      <h2>Dane przestrzenne</h2>
      <p>Heatmapy: {report.spatial.heatmaps.status} — {report.spatial.heatmaps.reason || 'brak bezpiecznej wspólnej orientacji'}.</p>
      <p>Team Shape: {report.spatial.team_shape.status} — {report.spatial.team_shape.reason || 'brak bezpiecznej wspólnej orientacji'}.</p>
    </section>
  </>;
}
