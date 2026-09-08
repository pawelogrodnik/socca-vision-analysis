import { useMemo, useState, type CSSProperties } from 'react';
import type { PublicReportPlayer } from '../types';
import {
  exactWindowLabel,
  hasWorkloadMetrics,
  isUnavailableWorkloadCell,
  metricWindowMaximum,
  metricWindowRange,
  workloadCellTooltip,
  workloadMetrics,
  workloadPresentationMode,
  windowIntensity,
  windowRelativeIntensity,
  windowValue,
  type WorkloadMetric,
} from '../lib/publicPlayerWorkloadPresentation';

type PublicPlayerWorkloadSectionProps = {
  players: PublicReportPlayer[];
  teamName?: string | null;
  teamColor?: string | null;
  variant?: 'default' | 'redesigned';
};

export function redesignedWorkloadHue(intensity: number): number {
  const clamped = Math.max(0, Math.min(1, intensity));
  return 150 * clamped;
}

export function PublicPlayerWorkloadSection({
  players,
  teamName,
  teamColor,
  variant = 'default',
}: PublicPlayerWorkloadSectionProps) {
  const [metric, setMetric] = useState<WorkloadMetric>('distance');
  const workloadPlayers = players.filter((player) => (
    player.workload?.activity_windows.length
    && (variant !== 'redesigned' || player.player_role !== 'goalkeeper')
  ));
  const windows = workloadPlayers[0]?.workload?.activity_windows || [];
  const mode = workloadPresentationMode(workloadPlayers);
  const metrics = workloadMetrics(mode);
  const maximum = useMemo(() => metricWindowMaximum(workloadPlayers, metric, mode), [metric, mode, workloadPlayers]);
  const range = useMemo(() => metricWindowRange(workloadPlayers, metric, mode), [metric, mode, workloadPlayers]);
  if (!hasWorkloadMetrics(players) || !windows.length) return null;

  return (
    <section className={`card player-workload-card${variant === 'redesigned' ? ' redesign-workload-card' : ''}`}>
      <h2>Aktywność w 5-minutowych oknach{teamName ? ` — ${teamName}` : ''}</h2>
      <p className='muted'>Jak zmieniały się aktywność i obciążenie zawodników w kolejnych fragmentach dostępnego nagrania.</p>
      <div className='chart-filter-bar' aria-label='Metryka aktywności zawodników'>
        {metrics.map((option) => (
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
      {variant === 'redesigned' ? <div className='redesign-workload-legend' aria-label='Skala intensywności'><span>Niższa</span><i /><i /><i /><i /><span>Wyższa</span></div> : null}
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
                    const unavailable = isUnavailableWorkloadCell(window, metric, mode);
                    const intensity = variant === 'redesigned'
                      ? windowRelativeIntensity(window, metric, range, mode)
                      : windowIntensity(window, metric, maximum, mode);
                    const title = workloadCellTooltip(player.player_name, window, referenceWindow, metric, mode);
                    return (
                      <td
                        key={referenceWindow.window_index}
                        aria-label={title}
                        title={title}
                        className={unavailable ? 'workload-unavailable' : metric === 'detectedTime' ? 'workload-evidence-cell' : 'workload-measured'}
                        data-workload-state={unavailable ? 'unavailable' : 'measured'}
                        style={{
                          '--workload-intensity': intensity,
                          '--redesign-workload-hue': variant === 'redesigned' ? redesignedWorkloadHue(intensity) : undefined,
                          '--workload-team-color': teamColor || '#38bdf8',
                        } as CSSProperties}
                      >
                        {windowValue(window, metric, mode)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {variant !== 'redesigned' ? <div className='player-workload-notes'>
        <p>Macierz pokazuje kolejne pięciominutowe fragmenty dostępnego nagrania. Ostatnie okno może być krótsze.</p>
        <p>Jeśli część meczu nie znajduje się w materiale, raport nie próbuje sztucznie odtwarzać brakujących minut.</p>
        <p>Kolor oznacza niższą lub wyższą zmierzoną aktywność, a nie ocenę dobrej lub złej gry.</p>
        <p>— oznacza za mało danych do wiarygodnego porównania, a nie automatycznie pobyt na ławce.</p>
        <p>Sprint jest liczony z zachowaniem ciągłości wiarygodnych obserwacji i progu dopasowanego do bezpiecznie zmierzonego tempa zawodnika.</p>
      </div> : null}
    </section>
  );
}
