import { useMemo, useState, type CSSProperties } from 'react';
import type { PublicReportPlayer } from '../types';
import {
  exactWindowLabel,
  hasWorkloadMetrics,
  metricWindowMaximum,
  WORKLOAD_METRICS,
  windowIntensity,
  windowValue,
  type WorkloadMetric,
} from '../lib/publicPlayerWorkloadPresentation';

type PublicPlayerWorkloadSectionProps = {
  players: PublicReportPlayer[];
  teamName?: string | null;
  teamColor?: string | null;
};

export function PublicPlayerWorkloadSection({
  players,
  teamName,
  teamColor,
}: PublicPlayerWorkloadSectionProps) {
  const [metric, setMetric] = useState<WorkloadMetric>('distance');
  const workloadPlayers = players.filter((player) => player.workload?.activity_windows.length);
  const windows = workloadPlayers[0]?.workload?.activity_windows || [];
  const maximum = useMemo(() => metricWindowMaximum(workloadPlayers, metric), [metric, workloadPlayers]);
  if (!hasWorkloadMetrics(players) || !windows.length) return null;

  return (
    <section className='card player-workload-card'>
      <h2>Aktywność w 5-minutowych oknach{teamName ? ` — ${teamName}` : ''}</h2>
      <p className='muted'>Jak zmieniały się aktywność i obciążenie zawodników w kolejnych fragmentach dostępnego nagrania.</p>
      <div className='chart-filter-bar' aria-label='Metryka aktywności zawodników'>
        {WORKLOAD_METRICS.map((option) => (
          <button
            className={`chart-filter-button${metric === option.key ? ' active' : ''}`}
            key={option.key}
            type='button'
            aria-pressed={metric === option.key}
            onClick={() => setMetric(option.key)}
          >
            {option.label}
          </button>
        ))}
      </div>
      <div className='workload-matrix-wrap'>
        <table className='workload-matrix'>
          <thead>
            <tr>
              <th scope='col'>Zawodnik</th>
              {windows.map((window) => (
                <th key={window.window_index} scope='col' title={exactWindowLabel(window)}>{window.display_label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {workloadPlayers.map((player) => {
              const playerWindows = player.workload?.activity_windows || [];
              return (
                <tr key={player.player_id}>
                  <th scope='row'>{player.player_name}</th>
                  {windows.map((referenceWindow) => {
                    const window = playerWindows.find((item) => item.window_index === referenceWindow.window_index);
                    const intensity = windowIntensity(window, metric, maximum);
                    const title = window
                      ? `${player.player_name}, ${exactWindowLabel(window)} materiału: czas wykryty ${windowValue(window, 'detectedTime')}, dystans ${windowValue(window, 'distance')}, wysoka intensywność ${windowValue(window, 'highIntensity')}, sprinty ${windowValue(window, 'sprints')}.`
                      : `${player.player_name}, ${exactWindowLabel(referenceWindow)} materiału: brak potwierdzonych danych.`;
                    return (
                      <td
                        key={referenceWindow.window_index}
                        aria-label={title}
                        title={title}
                        style={{ '--workload-intensity': intensity, '--workload-team-color': teamColor || '#38bdf8' } as CSSProperties}
                      >
                        {windowValue(window, metric)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className='player-workload-notes'>
        <p>Macierz pokazuje kolejne pięciominutowe fragmenty dostępnego nagrania. Ostatnie okno może być krótsze.</p>
        <p>Jeśli część meczu nie znajduje się w materiale, raport nie próbuje sztucznie odtwarzać brakujących minut.</p>
        <p>Puste pole oznacza brak potwierdzonych danych zawodnika w danym fragmencie, a nie automatycznie pobyt na ławce.</p>
      </div>
    </section>
  );
}
