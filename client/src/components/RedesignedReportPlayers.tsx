import { useMemo, useState } from 'react';
import type { PublicReportPlayer, PublicReportTeam } from '../types';
import { displayJerseyNumber, publicReportPlayersForTeam, publicReportTeamKey } from '../lib/publicReportPresentation';
import { formatRate, formatWorkloadSeconds } from '../lib/publicPlayerWorkloadPresentation';
import { formatReportKilometers, formatReportSpeed, playerComparableValue } from '../lib/redesignedPublicReportPresentation';

type SortKey = 'player_name' | 'detected_time_sec' | 'total_distance_m' | 'distance_per_5min_m' | 'high_intensity_distance_per_5min_m' | 'sprints_per_5min' | 'peak_speed_kmh';

const COLUMNS: Array<{ key: SortKey; label: string }> = [
  { key: 'player_name', label: 'Zawodnik' },
  { key: 'detected_time_sec', label: 'Czas wykryty' },
  { key: 'total_distance_m', label: 'Dystans' },
  { key: 'distance_per_5min_m', label: 'Dystans / 5 min' },
  { key: 'high_intensity_distance_per_5min_m', label: 'HI / 5 min' },
  { key: 'sprints_per_5min', label: 'Speed bursts / 5 min' },
  { key: 'peak_speed_kmh', label: 'Max speed' },
];

function playerName(player: PublicReportPlayer): string {
  const jersey = displayJerseyNumber(player.player_number);
  return `${jersey ? `#${jersey} ` : ''}${player.player_name || player.player_id}`;
}

function cellValue(player: PublicReportPlayer, key: SortKey): string {
  switch (key) {
    case 'player_name': return playerName(player);
    case 'detected_time_sec': return formatWorkloadSeconds(player.detected_time_sec ?? player.playing_time_sec);
    case 'total_distance_m': return formatReportKilometers(player.total_distance_m);
    case 'distance_per_5min_m': return formatRate(player.workload?.distance_per_5min_m, 'm');
    case 'high_intensity_distance_per_5min_m': return formatRate(player.workload?.high_intensity_distance_per_5min_m, 'm');
    case 'sprints_per_5min': return formatRate(player.workload?.sprints_per_5min, 'sprints');
    case 'peak_speed_kmh': return formatReportSpeed(player.peak_speed_kmh);
  }
}

function sortPlayers(players: PublicReportPlayer[], key: SortKey, direction: 'ascending' | 'descending') {
  return [...players].sort((left, right) => {
    if (key === 'player_name') return direction === 'ascending'
      ? playerName(left).localeCompare(playerName(right), 'pl')
      : playerName(right).localeCompare(playerName(left), 'pl');
    const leftValue = playerComparableValue(left, key);
    const rightValue = playerComparableValue(right, key);
    if (leftValue == null && rightValue == null) return playerName(left).localeCompare(playerName(right), 'pl');
    if (leftValue == null) return 1;
    if (rightValue == null) return -1;
    return direction === 'ascending' ? leftValue - rightValue : rightValue - leftValue;
  });
}

type PlayersProps = {
  players: PublicReportPlayer[];
  teams: PublicReportTeam[];
  selectedTeam: PublicReportTeam | undefined;
  selectedTeamKey: string | null;
  onSelectTeam: (key: string) => void;
};

export function RedesignedReportPlayers({ players, teams, selectedTeam, selectedTeamKey, onSelectTeam }: PlayersProps) {
  const [sort, setSort] = useState<{ key: SortKey; direction: 'ascending' | 'descending' }>({ key: 'distance_per_5min_m', direction: 'descending' });
  const visiblePlayers = useMemo(() => publicReportPlayersForTeam(players, selectedTeam), [players, selectedTeam]);
  const sortedPlayers = useMemo(() => sortPlayers(visiblePlayers, sort.key, sort.direction), [sort, visiblePlayers]);
  const chooseSort = (key: SortKey) => setSort((current) => ({ key, direction: current.key === key && current.direction === 'descending' ? 'ascending' : 'descending' }));

  return <>
    <section className='redesign-section redesign-players-section' aria-labelledby='redesign-players-title'>
      <div className='redesign-section-heading'><div><p className='redesign-kicker'>Zawodnicy</p><h2 id='redesign-players-title'>Statystyki zawodników</h2></div></div>
      <div className='redesign-team-tabs' role='tablist' aria-label='Wybór drużyny'>
        {teams.map((team, index) => {
          const key = publicReportTeamKey(team, index);
          return <button className={key === selectedTeamKey ? 'active' : ''} type='button' role='tab' aria-selected={key === selectedTeamKey} key={key} onClick={() => onSelectTeam(key)}>{team.team_name || team.team_label || `Drużyna ${index + 1}`}</button>;
        })}
      </div>
      <div className='redesign-table-wrap'>
        <table className='redesign-player-table'><thead><tr>{COLUMNS.map((column) => <th scope='col' key={column.key} aria-sort={sort.key === column.key ? sort.direction : 'none'}><button type='button' onClick={() => chooseSort(column.key)}>{column.label}{sort.key === column.key ? (sort.direction === 'ascending' ? ' ↑' : ' ↓') : ''}</button></th>)}</tr></thead>
          <tbody>{sortedPlayers.map((player) => <tr key={player.player_id}>{COLUMNS.map((column) => <td key={column.key}>{cellValue(player, column.key)}</td>)}</tr>)}{!sortedPlayers.length ? <tr><td colSpan={COLUMNS.length}>Brak rozpoznanych zawodników tej drużyny.</td></tr> : null}</tbody>
        </table>
      </div>
      <p className='redesign-note'>„Czas wykryty” opisuje tylko fragmenty, w których zawodnik został rozpoznany; nie jest czasem gry.</p>
    </section>
  </>;
}
