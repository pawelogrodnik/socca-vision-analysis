import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getPlayerProfileStats } from '../api';
import { errorMessage } from '../lib/helpers';
import type { PlayerProfileStatsDocument } from '../types';

function recordNumber(record: Record<string, unknown> | undefined, key: string): number {
  const value = record?.[key];
  return typeof value === 'number' ? value : 0;
}

function nestedNumber(record: Record<string, unknown> | undefined, key: string): number {
  const value = record?.[key];
  return typeof value === 'number' ? value : 0;
}

function candidateLabel(candidate: unknown): string {
  if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) {
    return 'n/a';
  }
  const row = candidate as Record<string, unknown>;
  const speed = nestedNumber(row, 'max_speed_kmh');
  const duration = nestedNumber(row, 'duration_sec');
  const reason = row.reason ? String(row.reason) : 'unknown';
  if (speed <= 0) return 'n/a';
  return `${formatSpeed(speed)} / ${duration.toFixed(2)}s / ${reason}`;
}

function formatMeters(value: number): string {
  return `${value.toFixed(1)} m`;
}

function formatSpeed(value: number): string {
  return `${value.toFixed(1)} km/h`;
}

function formatSeconds(value: number): string {
  if (value < 60) return `${value.toFixed(1)}s`;
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value % 60);
  return `${minutes}m ${seconds}s`;
}

function formatPercentRatio(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function playerTitle(profile: PlayerProfileStatsDocument): string {
  const player = profile.player;
  const number = player.player_number ? `#${player.player_number} ` : '';
  return `${number}${player.player_name || player.player_id}`;
}

export function PlayerProfilePage() {
  const { playerId } = useParams();
  const [profile, setProfile] = useState<PlayerProfileStatsDocument | null>(null);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!playerId) {
      setStatus('Brakuje player_id.');
      return;
    }
    setLoading(true);
    getPlayerProfileStats(playerId)
      .then((data) => {
        setProfile(data);
        setStatus('');
      })
      .catch((error) => {
        setProfile(null);
        setStatus(errorMessage(error));
      })
      .finally(() => setLoading(false));
  }, [playerId]);

  const summary = profile?.summary;

  return (
    <main className='app'>
      <section className='hero compact-hero'>
        <p className='eyebrow'>Profil zawodnika</p>
        <h1>{profile ? playerTitle(profile) : 'Profil zawodnika'}</h1>
        <p>
          Agregacja tracking-only po realnym player_id. Anonimowe sloty typu A03/B05
          sa widoczne tylko w raporcie pojedynczego meczu.
        </p>
        <div className='row'>
          <Link to='/admin-panel'>Panel meczu</Link>
          <Link to='/teams'>Druzyny</Link>
        </div>
      </section>

      {loading && (
        <p className='loading-line'>
          <span className='spinner' />
          Laduje profil zawodnika...
        </p>
      )}
      {status && <p className='status'>{status}</p>}

      {profile && (
        <>
          <section className='card'>
            <div className='row between'>
              <div>
                <h2>Podsumowanie</h2>
                <p className='muted'>
                  Liczymy tylko mecze, w ktorych stable slot/stint zostal jawnie
                  przypisany do tego zawodnika.
                </p>
              </div>
              <span className='confidence-pill'>
                {profile.player.known_from_registry ? 'Roster player' : 'Resolved player'}
              </span>
            </div>
            <div className='chips'>
              <span>Mecze: {recordNumber(summary, 'matches')}</span>
              <span>Czas: {formatSeconds(recordNumber(summary, 'playing_time_sec'))}</span>
              <span>Dystans: {formatMeters(recordNumber(summary, 'total_distance_m'))}</span>
              <span>Observed: {formatMeters(recordNumber(summary, 'observed_distance_m'))}</span>
              <span>Estimated gaps: {formatMeters(recordNumber(summary, 'estimated_short_gap_distance_m'))}</span>
              <span>Estimated ratio: {formatPercentRatio(recordNumber(summary, 'estimated_distance_ratio'))}</span>
              <span>Avg: {formatSpeed(recordNumber(summary, 'avg_speed_kmh'))}</span>
              <span>Peak: {formatSpeed(recordNumber(summary, 'peak_sustained_speed_kmh'))}</span>
              <span>HI dist: {formatMeters(recordNumber(summary, 'high_intensity_distance_m'))}</span>
              <span>Sprinty: {recordNumber(summary, 'sprint_count')}</span>
              <span>Sprint dist: {formatMeters(recordNumber(summary, 'sprint_distance_m'))}</span>
              <span>Max sprint: {formatSpeed(recordNumber(summary, 'max_sprint_speed_kmh'))}</span>
              <span>Sprint candidates: {recordNumber(summary, 'sprint_candidate_count')}</span>
              <span>Rejected candidates: {recordNumber(summary, 'rejected_sprint_candidate_count')}</span>
              <span>Best candidate: {formatSpeed(recordNumber(summary, 'best_sprint_candidate_speed_kmh'))}</span>
              <span>Distance quality: {String(summary?.distance_quality || 'unknown')}</span>
              <span>Warnings: {recordNumber(summary, 'matches_with_warnings')}</span>
            </div>
          </section>

          <section className='card'>
            <h2>Wystepy w meczach</h2>
            {profile.appearances.length === 0 ? (
              <p className='muted'>
                Ten zawodnik jest w rosterze, ale nie ma jeszcze zadnego jawnego
                przypisania do stable slotu w przeanalizowanym meczu.
              </p>
            ) : (
              <div className='stats-table-wrap'>
                <table className='stats-table'>
                  <thead>
                    <tr>
                      <th>Mecz</th>
                      <th>Data</th>
                      <th>Druzyna</th>
                      <th>Slot</th>
                      <th>Czas</th>
                      <th>Dystans</th>
                      <th>Sprinty</th>
                      <th>Peak</th>
                      <th>Jakosc</th>
                      <th>Uwagi</th>
                    </tr>
                  </thead>
                  <tbody>
                    {profile.appearances.map((appearance) => (
                      <tr key={`${appearance.match_id}-${appearance.stable_player_ids?.join('-') || 'slot'}`}>
                        <td>
                          <strong>{appearance.match_title || appearance.match_id}</strong>
                          <span>{appearance.match_id}</span>
                        </td>
                        <td>{appearance.match_date || 'n/a'}</td>
                        <td>{appearance.team_name || appearance.team_label || 'n/a'}</td>
                        <td>{appearance.stable_player_ids?.join(', ') || 'n/a'}</td>
                        <td>{formatSeconds(nestedNumber(appearance.time, 'playing_time_sec'))}</td>
                        <td>{formatMeters(nestedNumber(appearance.distance, 'total_distance_m'))}</td>
                        <td>
                          {nestedNumber(appearance.intensity, 'sprint_count')}
                          <span>{formatMeters(nestedNumber(appearance.intensity, 'sprint_distance_m'))}</span>
                          <span>
                            cand {nestedNumber(appearance.intensity, 'sprint_candidate_count')} / rej{' '}
                            {nestedNumber(appearance.intensity, 'rejected_sprint_candidate_count')}
                          </span>
                          {nestedNumber(appearance.intensity, 'sprint_count') === 0 &&
                            nestedNumber(appearance.intensity, 'rejected_sprint_candidate_count') > 0 && (
                              <span>
                                best rejected{' '}
                                {candidateLabel(appearance.intensity?.best_rejected_sprint_candidate)}
                              </span>
                            )}
                        </td>
                        <td>{formatSpeed(nestedNumber(appearance.speed, 'peak_sustained_speed_kmh'))}</td>
                        <td>{appearance.distance_quality || 'unknown'}</td>
                        <td>{appearance.review_warnings?.length ? appearance.review_warnings.join(', ') : '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className='card'>
            <h2>Kontrakt danych</h2>
            <div className='chips'>
              <span>Scope: {profile.scope}</span>
              <span>Identity: {profile.identity_semantics}</span>
              <span>Scanned matches: {recordNumber(summary, 'scanned_matches')}</span>
              <span>Anonymous aggregated: {recordNumber(summary, 'anonymous_slots_aggregated')}</span>
            </div>
            <ul className='muted'>
              {(profile.notes || []).map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </section>
        </>
      )}
    </main>
  );
}
