import type { ReviewedStatsResponse } from '../types';

export function ReviewedPlayerStatsTable({ document }: { document: ReviewedStatsResponse }) {
  const players = document.stats.players;
  return <div>
    <h3>Statystyki z potwierdzonych obserwacji</h3>
    {!players.length && <p>Brak potwierdzonych obserwacji imiennych do statystyk.</p>}
    {!!players.length && <div className='table-wrap'><table>
      <thead><tr><th>Zawodnik</th><th>Team</th><th>Wykryty czas</th><th>Zaobserwowany dystans</th><th>Próbki heatmapy</th></tr></thead>
      <tbody>{players.map((player) => <tr key={player.player_id}>
        <td>{player.player_name}</td>
        <td>{player.team_label}</td>
        <td>{player.detected_time_sec.toFixed(1)} s</td>
        <td>{player.observed_distance_m.toFixed(1)} m</td>
        <td>{player.heatmap_samples}</td>
      </tr>)}</tbody>
    </table></div>}
    <p className='status'>To nie jest pełny czas gry: tabela obejmuje wyłącznie wykryte obserwacje z bezpiecznie potwierdzoną tożsamością.</p>
  </div>;
}
