import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getTeamProfileStats } from '../api';
import { errorMessage } from '../lib/helpers';
import type { TeamProfileStatsDocument } from '../types';

type SortKey = 'name' | 'matches' | 'playing_time' | 'distance' | 'sprints' | 'peak';

function recordNumber(record: Record<string, unknown> | undefined, key: string): number {
  const value = record?.[key];
  return typeof value === 'number' ? value : 0;
}

function recordText(
  record: Record<string, unknown> | undefined,
  key: string,
  fallback = 'n/a',
): string {
  const value = record?.[key];
  if (value == null || value === '') return fallback;
  return String(value);
}

function nestedRecord(record: Record<string, unknown> | undefined, key: string): Record<string, unknown> {
  const value = record?.[key];
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function nestedNumber(record: Record<string, unknown> | undefined, group: string, key: string): number {
  return recordNumber(nestedRecord(record, group), key);
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

function playerDisplayName(row: Record<string, unknown>): string {
  const number = recordText(row, 'player_number', '');
  const name = recordText(row, 'player_name', recordText(row, 'player_id', 'Unknown player'));
  return number ? `#${number} ${name}` : name;
}

function playerSortValue(row: Record<string, unknown>, sortKey: SortKey): number | string {
  if (sortKey === 'name') return playerDisplayName(row).toLocaleLowerCase();
  if (sortKey === 'matches') return recordNumber(row, 'matches');
  if (sortKey === 'playing_time') return nestedNumber(row, 'time', 'playing_time_sec');
  if (sortKey === 'distance') return nestedNumber(row, 'distance', 'total_distance_m');
  if (sortKey === 'sprints') return nestedNumber(row, 'intensity', 'sprint_count');
  return nestedNumber(row, 'speed', 'peak_sustained_speed_kmh');
}

function missingReasonLabel(reason: string): string {
  if (reason === 'missing_resolved_player_stats') return 'brak resolved_player_stats.json';
  if (reason === 'no_assigned_players_for_team') return 'brak przypisanych zawodnikow';
  return reason || 'ok';
}

export function TeamStatsPage() {
  const { teamId } = useParams();
  const [profile, setProfile] = useState<TeamProfileStatsDocument | null>(null);
  const [season, setSeason] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('distance');
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!teamId) {
      setStatus('Brakuje team_id.');
      return;
    }
    setLoading(true);
    getTeamProfileStats(teamId, season || undefined)
      .then((data) => {
        setProfile(data);
        setStatus('');
      })
      .catch((error) => {
        setProfile(null);
        setStatus(errorMessage(error));
      })
      .finally(() => setLoading(false));
  }, [teamId, season]);

  const summary = profile?.summary;
  const sortedPlayers = useMemo(() => {
    const rows = [...(profile?.players || [])];
    rows.sort((left, right) => {
      const leftValue = playerSortValue(left, sortKey);
      const rightValue = playerSortValue(right, sortKey);
      if (typeof leftValue === 'string' || typeof rightValue === 'string') {
        return String(leftValue).localeCompare(String(rightValue));
      }
      return rightValue - leftValue;
    });
    return rows;
  }, [profile?.players, sortKey]);

  const teamName = profile?.team.team_name || profile?.team.team_id || teamId || 'Druzyna';

  return (
    <main className='app'>
      <section className='hero compact-hero'>
        <p className='eyebrow'>Tracking-only dashboard</p>
        <h1>{teamName}</h1>
        <p>
          Agregacja po realnych zawodnikach przypisanych do meczow. Anonimowe
          sloty przeciwnika zostaja tylko w raportach pojedynczych meczow.
        </p>
        <div className='row'>
          <Link to='/teams'>Druzyny</Link>
          {teamId && <Link to={`/teams/${encodeURIComponent(teamId)}`}>Edytuj roster</Link>}
          <Link to='/admin-panel'>Panel meczu</Link>
        </div>
      </section>

      {loading && (
        <p className='loading-line'>
          <span className='spinner' />
          Laduje dashboard druzyny...
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
                  Dane pochodza z `resolved_player_stats.json`; ball events nie sa
                  jeszcze liczone.
                </p>
              </div>
              <label className='inline-filter'>
                Sezon
                <select value={season} onChange={(event) => setSeason(event.target.value)}>
                  <option value=''>Wszystkie</option>
                  {profile.available_seasons.map((item) => (
                    <option value={item} key={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className='chips'>
              <span>Mecze ze statystykami: {recordNumber(summary, 'matches_with_stats')}</span>
              <span>Mecze z druzyna: {recordNumber(summary, 'matches_with_team')}</span>
              <span>Zawodnicy: {recordNumber(summary, 'players')}</span>
              <span>Roster: {recordNumber(summary, 'roster_players')}</span>
              <span>Czas: {formatSeconds(recordNumber(summary, 'playing_time_sec'))}</span>
              <span>Dystans: {formatMeters(recordNumber(summary, 'total_distance_m'))}</span>
              <span>Avg: {formatSpeed(recordNumber(summary, 'avg_speed_kmh'))}</span>
              <span>Peak: {formatSpeed(recordNumber(summary, 'peak_sustained_speed_kmh'))}</span>
              <span>HI dist: {formatMeters(recordNumber(summary, 'high_intensity_distance_m'))}</span>
              <span>Sprinty: {recordNumber(summary, 'sprint_count')}</span>
              <span>Kandydaci sprintu: {recordNumber(summary, 'sprint_candidate_count')}</span>
              <span>Braki danych: {recordNumber(summary, 'matches_missing_resolved_stats')}</span>
              <span>Anonimowi agregowani: {recordNumber(summary, 'anonymous_slots_aggregated')}</span>
            </div>
          </section>

          <section className='card'>
            <div className='row between'>
              <h2>Zawodnicy</h2>
              <label className='inline-filter'>
                Sortuj
                <select value={sortKey} onChange={(event) => setSortKey(event.target.value as SortKey)}>
                  <option value='distance'>Dystans</option>
                  <option value='playing_time'>Czas gry</option>
                  <option value='sprints'>Sprinty</option>
                  <option value='peak'>Peak speed</option>
                  <option value='matches'>Mecze</option>
                  <option value='name'>Nazwisko</option>
                </select>
              </label>
            </div>
            {sortedPlayers.length === 0 ? (
              <p className='muted'>
                Brak przypisanych zawodnikow dla tej druzyny. Najpierw przypisz
                stable sloty do rosteru w review meczu.
              </p>
            ) : (
              <div className='stats-table-wrap'>
                <table className='stats-table'>
                  <thead>
                    <tr>
                      <th>Zawodnik</th>
                      <th>Mecze</th>
                      <th>Czas</th>
                      <th>Dystans</th>
                      <th>Avg</th>
                      <th>Peak</th>
                      <th>Sprinty</th>
                      <th>HI dist</th>
                      <th>Jakosc</th>
                      <th>Profil</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedPlayers.map((player) => {
                      const playerId = recordText(player, 'player_id', '');
                      return (
                        <tr key={playerId || playerDisplayName(player)}>
                          <td>
                            <strong>{playerDisplayName(player)}</strong>
                            <span>{playerId || 'n/a'}</span>
                          </td>
                          <td>{recordNumber(player, 'matches')}</td>
                          <td>{formatSeconds(nestedNumber(player, 'time', 'playing_time_sec'))}</td>
                          <td>{formatMeters(nestedNumber(player, 'distance', 'total_distance_m'))}</td>
                          <td>{formatSpeed(nestedNumber(player, 'speed', 'avg_speed_kmh'))}</td>
                          <td>{formatSpeed(nestedNumber(player, 'speed', 'peak_sustained_speed_kmh'))}</td>
                          <td>
                            {nestedNumber(player, 'intensity', 'sprint_count')}
                            <span>
                              cand {nestedNumber(player, 'intensity', 'sprint_candidate_count')} / rej{' '}
                              {nestedNumber(player, 'intensity', 'rejected_sprint_candidate_count')}
                            </span>
                          </td>
                          <td>{formatMeters(nestedNumber(player, 'intensity', 'high_intensity_distance_m'))}</td>
                          <td>
                            {recordText(player, 'distance_quality', 'unknown')}
                            <span>speed {recordText(player, 'speed_quality', 'unknown')}</span>
                          </td>
                          <td>
                            {playerId ? (
                              <Link to={`/players/${encodeURIComponent(playerId)}`}>Profil</Link>
                            ) : (
                              'n/a'
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className='card'>
            <h2>Mecze w agregacji</h2>
            <div className='stats-table-wrap'>
              <table className='stats-table'>
                <thead>
                  <tr>
                    <th>Mecz</th>
                    <th>Sezon</th>
                    <th>Zawodnicy</th>
                    <th>Dystans</th>
                    <th>Sprinty</th>
                    <th>Peak</th>
                    <th>Status danych</th>
                  </tr>
                </thead>
                <tbody>
                  {profile.matches.map((match) => {
                    const matchId = recordText(match, 'match_id', '');
                    const missingReason = recordText(match, 'missing_reason', '');
                    return (
                      <tr key={matchId || recordText(match, 'match_title')}>
                        <td>
                          <strong>{recordText(match, 'match_title', matchId)}</strong>
                          <span>{recordText(match, 'match_date', 'brak daty')}</span>
                        </td>
                        <td>{recordText(match, 'season', 'n/a')}</td>
                        <td>{recordNumber(match, 'players')}</td>
                        <td>{formatMeters(recordNumber(match, 'total_distance_m'))}</td>
                        <td>
                          {recordNumber(match, 'sprint_count')}
                          <span>cand {recordNumber(match, 'sprint_candidate_count')}</span>
                        </td>
                        <td>{formatSpeed(recordNumber(match, 'peak_sustained_speed_kmh'))}</td>
                        <td>
                          {missingReason ? missingReasonLabel(missingReason) : 'ok'}
                          {matchId && !missingReason && (
                            <span>
                              <Link to={`/matches/${encodeURIComponent(matchId)}/report`}>Raport meczu</Link>
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>

          <section className='card'>
            <h2>Kontrakt danych</h2>
            <div className='chips'>
              <span>Scope: {profile.scope}</span>
              <span>Identity: {profile.identity_semantics}</span>
              <span>Known registry: {profile.team.known_from_registry ? 'yes' : 'no'}</span>
              <span>Scanned matches: {recordNumber(summary, 'scanned_matches')}</span>
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
