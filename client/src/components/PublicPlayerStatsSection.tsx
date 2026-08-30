import { useState } from 'react';
import type { PublicReportPlayer } from '../types';
import { formatHiRatio, formatRate, formatWorkloadSeconds, hasWorkloadMetrics } from '../lib/publicPlayerWorkloadPresentation';
import { displayJerseyNumber, hasAdvancedPlayerMetrics } from '../lib/publicReportPresentation';

type PublicPlayerStatsSectionProps = {
  players: PublicReportPlayer[];
  teamName?: string | null;
};

function playerLabel(player: PublicReportPlayer): string {
  const number = displayJerseyNumber(player.player_number);
  return `${number ? `#${number} ` : ''}${player.player_name || player.player_id}`;
}

function formatMeters(value: number | undefined): string {
  return `${(value || 0).toFixed(1)} m`;
}

function formatSpeed(value: number | undefined): string {
  return `${(value || 0).toFixed(1)} km/h`;
}

type StatsView = 'basic' | 'workload';

export function PublicPlayerStatsSection({ players, teamName }: PublicPlayerStatsSectionProps) {
  const [view, setView] = useState<StatsView>('basic');
  const advanced = hasAdvancedPlayerMetrics(players);
  const workloadAvailable = hasWorkloadMetrics(players);
  const effectiveView = workloadAvailable ? view : 'basic';
  return (
    <section className='card'>
      <h2>Statystyki rozpoznanych zawodników{teamName ? ` — ${teamName}` : ''}</h2>
      {workloadAvailable && (
        <div className='chart-filter-bar' aria-label='Widok statystyk zawodników'>
          <button className={`chart-filter-button${effectiveView === 'basic' ? ' active' : ''}`} type='button' aria-pressed={effectiveView === 'basic'} onClick={() => setView('basic')}>Podstawowe</button>
          <button className={`chart-filter-button${effectiveView === 'workload' ? ' active' : ''}`} type='button' aria-pressed={effectiveView === 'workload'} onClick={() => setView('workload')}>Obciążenie</button>
        </div>
      )}
      {effectiveView === 'basic' ? (
        <div className='stats-table-wrap'>
          <table className='stats-table'>
            <thead><tr><th>Zawodnik</th><th>Drużyna</th><th title='Czas fragmentów nagrania, na których rozpoznano zawodnika'>Czas wykryty</th><th>Dystans</th>{advanced && <th>Dystans wys. intensywności</th>}{advanced && <th>Sprinty</th>}{advanced && <th>Śr. prędkość</th>}{advanced && <th>Prędkość maks.</th>}</tr></thead>
            <tbody>
              {players.map((player) => <tr key={player.player_id}><td><strong>{playerLabel(player)}</strong></td><td>{player.team_name || player.team_label || 'Team'}</td><td>{formatWorkloadSeconds(player.detected_time_sec || player.playing_time_sec)}</td><td>{formatMeters(player.total_distance_m)}</td>{advanced && <td>{formatMeters(player.high_intensity_distance_m)}</td>}{advanced && <td>{player.sprint_count}</td>}{advanced && <td>{formatSpeed(player.avg_speed_kmh)}</td>}{advanced && <td>{formatSpeed(player.peak_speed_kmh)}</td>}</tr>)}
              {!players.length && <tr><td colSpan={advanced ? 8 : 4}>Brak rozpoznanych z imienia zawodników tej drużyny.</td></tr>}
            </tbody>
          </table>
        </div>
      ) : (
        <div className='stats-table-wrap'>
          <table className='stats-table workload-stats-table'>
            <thead><tr><th>Zawodnik</th><th title='Średni dystans przeliczony na 5 minut czasu wykrytego.'>Dystans / 5 min</th><th title='Dystans wysokiej intensywności przeliczony na 5 minut czasu wykrytego.'>HI / 5 min</th><th title='Liczba zaakceptowanych sprintów przeliczona na 5 minut czasu wykrytego.'>Sprinty / 5 min</th><th title='Udział dystansu wysokiej intensywności w całkowitym dystansie.'>HI %</th><th>Dystans sprintem</th><th>Czas sprintu</th><th>Max sprint</th><th>Najlepsze okno</th></tr></thead>
            <tbody>
              {players.map((player) => {
                const workload = player.workload;
                const best = workload?.best_activity_window;
                return <tr key={player.player_id}><td><strong>{playerLabel(player)}</strong></td><td>{formatRate(workload?.distance_per_5min_m, 'm')}</td><td>{formatRate(workload?.high_intensity_distance_per_5min_m, 'm')}</td><td>{formatRate(workload?.sprints_per_5min, 'sprints')}</td><td>{formatHiRatio(workload?.high_intensity_distance_ratio)}</td><td>{formatMeters(player.sprint_distance_m)}</td><td>{formatWorkloadSeconds(player.sprint_time_sec)}</td><td>{player.max_sprint_speed_kmh ? formatSpeed(player.max_sprint_speed_kmh) : '—'}</td><td>{best ? <><strong>{best.display_label}</strong><br />{formatRate(best.distance_per_5min_m, 'm')}<br /><span className='muted'>{formatWorkloadSeconds(best.detected_time_sec)} czasu wykrytego</span></> : '—'}</td></tr>;
              })}
            </tbody>
          </table>
        </div>
      )}
      {workloadAvailable && <p className='team-comparison-note'>Metryki „/ 5 min” pozwalają porównywać zawodników, którzy byli potwierdzeni w danych przez różny czas. Czas wykryty nie jest oficjalnym czasem gry.</p>}
    </section>
  );
}
