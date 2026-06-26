import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { artifactUrl, getMatch } from '../api';
import { errorMessage } from '../lib/helpers';
import type { Match } from '../types';

type GenericRow = Record<string, unknown>;

function asRows(value: unknown): GenericRow[] {
  return Array.isArray(value) ? value.filter((item): item is GenericRow => Boolean(item) && typeof item === 'object' && !Array.isArray(item)) : [];
}

function recordNumber(record: GenericRow | undefined | null, key: string): number {
  const value = record?.[key];
  return typeof value === 'number' ? value : 0;
}

function recordText(record: GenericRow | undefined | null, key: string, fallback = 'n/a'): string {
  const value = record?.[key];
  if (value == null || value === '') return fallback;
  return String(value);
}

function nestedRecord(record: GenericRow | undefined | null, key: string): GenericRow {
  const value = record?.[key];
  return value && typeof value === 'object' && !Array.isArray(value) ? value as GenericRow : {};
}

function nestedNumber(record: GenericRow | undefined | null, group: string, key: string): number {
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

function teamName(row: GenericRow): string {
  return recordText(row, 'team_name', recordText(row, 'team_label', 'Unknown'));
}

function stablePlayerRows(match: Match): GenericRow[] {
  return asRows(match.player_stats?.players || match.stable_players?.players || []);
}

function resolvedPlayerRows(match: Match): GenericRow[] {
  return asRows(match.resolved_player_stats?.players || []);
}

function teamRows(match: Match): GenericRow[] {
  return asRows(match.team_stats?.teams || match.player_stats?.teams || []);
}

function visibleMetricSummary(match: Match): GenericRow {
  return match.frame_detection_counts?.summary || match.global_identity_report?.frame_detection_summary || {};
}

function playerDisplayName(row: GenericRow): string {
  const number = recordText(row, 'player_number', '');
  const name = recordText(row, 'player_name', recordText(row, 'player_id', 'Unknown player'));
  return number ? `#${number} ${name}` : name;
}

function stableSlotLabel(row: GenericRow): string {
  return recordText(row, 'stable_player_id', recordText(row, 'slot_id', 'n/a'));
}

export function MatchReportPage() {
  const { matchId } = useParams();
  const [match, setMatch] = useState<Match | null>(null);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!matchId) {
      setStatus('Missing match id.');
      return;
    }
    setLoading(true);
    getMatch(matchId)
      .then((data) => {
        setMatch(data);
        setStatus('');
      })
      .catch((error) => {
        setMatch(null);
        setStatus(errorMessage(error));
      })
      .finally(() => setLoading(false));
  }, [matchId]);

  const stableOverlay = match?.analysis_report?.artifacts?.stable_overlay_preview;
  const stablePlayers = useMemo(() => (match ? stablePlayerRows(match) : []), [match]);
  const resolvedPlayers = useMemo(() => (match ? resolvedPlayerRows(match) : []), [match]);
  const teams = useMemo(() => (match ? teamRows(match) : []), [match]);
  const frameSummary = useMemo(() => (match ? visibleMetricSummary(match) : {}), [match]);
  const movementSummary = match?.player_stats?.summary || match?.movement_stats?.summary || {};
  const stableSummary = match?.stable_players?.summary || {};

  return (
    <main className='app'>
      <section className='hero compact-hero'>
        <p className='eyebrow'>Match report</p>
        <h1>{match?.title || 'Raport meczu'}</h1>
        <p>
          Raport tracking-only dla pojedynczego meczu. Anonimowe sloty sa czescia
          raportu meczowego, ale nie sa agregowane do profili zawodnikow.
        </p>
        <div className='row'>
          <Link to='/admin-panel'>Panel admin</Link>
          <Link to='/teams'>Druzyny</Link>
        </div>
      </section>

      {loading && (
        <p className='loading-line'>
          <span className='spinner' />
          Laduje raport meczu...
        </p>
      )}
      {status && <p className='status'>{status}</p>}

      {match && (
        <>
          <section className='card'>
            <div className='row between'>
              <div>
                <h2>Podsumowanie</h2>
                <p className='muted'>
                  {match.match_date || 'brak daty'} · {match.season || 'brak sezonu'} · {match.venue || 'brak miejsca'}
                </p>
              </div>
              <Link to={`/admin-panel`}>Edytuj analize</Link>
            </div>
            <div className='chips'>
              <span>Status: {match.status || 'uploaded'}</span>
              <span>Format: {match.format || '7v7'}</span>
              <span>Stable slots: {recordNumber(stableSummary, 'stable_players')}</span>
              <span>Resolved players: {recordNumber(match.resolved_player_stats?.summary, 'players')}</span>
              <span>Total distance: {formatMeters(recordNumber(movementSummary, 'total_distance_m'))}</span>
              <span>Peak: {formatSpeed(recordNumber(movementSummary, 'peak_sustained_speed_kmh') || recordNumber(movementSummary, 'top_speed_kmh'))}</span>
              <span>Visible avg: {recordNumber(frameSummary, 'stable_avg').toFixed(1)}</span>
              <span>Warnings: {match.analysis_report?.warnings?.length || 0}</span>
            </div>
          </section>

          {stableOverlay && (
            <section className='card'>
              <h2>Stable overlay</h2>
              <video controls src={artifactUrl(match.id, stableOverlay)} className='video' />
            </section>
          )}

          <section className='card'>
            <h2>Porownanie druzyn</h2>
            {teams.length === 0 ? (
              <p className='muted'>Brak `team_stats.json`. Uruchom ponownie analize dla tego meczu.</p>
            ) : (
              <div className='stats-table-wrap'>
                <table className='stats-table'>
                  <thead>
                    <tr>
                      <th>Druzyna</th>
                      <th>Sloty</th>
                      <th>Czas</th>
                      <th>Dystans</th>
                      <th>Observed</th>
                      <th>Estimated</th>
                      <th>Peak</th>
                      <th>Jakosc</th>
                    </tr>
                  </thead>
                  <tbody>
                    {teams.map((team) => (
                      <tr key={`${recordText(team, 'team_id', '')}-${recordText(team, 'team_label', '')}`}>
                        <td>
                          <strong>{teamName(team)}</strong>
                          <span>{recordText(team, 'team_label', 'n/a')}</span>
                        </td>
                        <td>{recordNumber(team, 'players')}</td>
                        <td>{formatSeconds(recordNumber(team, 'playing_time_sec'))}</td>
                        <td>{formatMeters(recordNumber(team, 'total_distance_m'))}</td>
                        <td>{formatMeters(recordNumber(team, 'observed_distance_m'))}</td>
                        <td>{formatMeters(recordNumber(team, 'estimated_short_gap_distance_m'))}</td>
                        <td>{formatSpeed(recordNumber(team, 'peak_sustained_speed_kmh') || recordNumber(team, 'top_speed_kmh'))}</td>
                        <td>
                          low {recordNumber(team, 'players_low_quality')} / med {recordNumber(team, 'players_medium_quality')} / high {recordNumber(team, 'players_high_quality')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className='card'>
            <h2>Realni zawodnicy</h2>
            {resolvedPlayers.length === 0 ? (
              <p className='muted'>
                Brak przypisanych realnych zawodnikow. Statystyki meczu nadal sa
                widoczne po anonimowych stable slotach.
              </p>
            ) : (
              <div className='stats-table-wrap'>
                <table className='stats-table'>
                  <thead>
                    <tr>
                      <th>Zawodnik</th>
                      <th>Druzyna</th>
                      <th>Stable slot</th>
                      <th>Czas</th>
                      <th>Dystans</th>
                      <th>Peak</th>
                      <th>Profil</th>
                    </tr>
                  </thead>
                  <tbody>
                    {resolvedPlayers.map((player) => {
                      const playerId = recordText(player, 'player_id', '');
                      const slots = asRows(player.source_stable_slots).map(stableSlotLabel).join(', ');
                      return (
                        <tr key={playerId || playerDisplayName(player)}>
                          <td>
                            <strong>{playerDisplayName(player)}</strong>
                            <span>{playerId || 'n/a'}</span>
                          </td>
                          <td>{teamName(player)}</td>
                          <td>{slots || 'n/a'}</td>
                          <td>{formatSeconds(nestedNumber(player, 'time', 'playing_time_sec'))}</td>
                          <td>{formatMeters(nestedNumber(player, 'distance', 'total_distance_m'))}</td>
                          <td>{formatSpeed(nestedNumber(player, 'speed', 'peak_sustained_speed_kmh'))}</td>
                          <td>
                            {playerId ? <Link to={`/players/${encodeURIComponent(playerId)}`}>Profil</Link> : 'n/a'}
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
            <h2>Wszyscy stable zawodnicy w meczu</h2>
            {stablePlayers.length === 0 ? (
              <p className='muted'>Brak `player_stats.json` albo `stable_players.json`.</p>
            ) : (
              <div className='stats-table-wrap'>
                <table className='stats-table'>
                  <thead>
                    <tr>
                      <th>Slot</th>
                      <th>Druzyna</th>
                      <th>Status</th>
                      <th>Czas</th>
                      <th>Dystans</th>
                      <th>Observed</th>
                      <th>Estimated</th>
                      <th>Avg</th>
                      <th>Peak</th>
                      <th>Jakosc</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stablePlayers.map((player) => (
                      <tr key={recordText(player, 'stable_subject_id', stableSlotLabel(player))}>
                        <td>
                          <strong>{stableSlotLabel(player)}</strong>
                          <span>{recordText(player, 'stable_subject_id', 'n/a')}</span>
                        </td>
                        <td>{teamName(player)}</td>
                        <td>{recordText(player, 'status', 'active')}</td>
                        <td>{formatSeconds(nestedNumber(player, 'time', 'playing_time_sec') || recordNumber(player, 'duration_sec'))}</td>
                        <td>{formatMeters(nestedNumber(player, 'distance', 'total_distance_m') || nestedNumber(player, 'movement_stats', 'total_distance_m'))}</td>
                        <td>{formatMeters(nestedNumber(player, 'distance', 'observed_distance_m') || nestedNumber(player, 'movement_stats', 'observed_distance_m'))}</td>
                        <td>{formatMeters(nestedNumber(player, 'distance', 'estimated_short_gap_distance_m') || nestedNumber(player, 'movement_stats', 'estimated_gap_distance_m'))}</td>
                        <td>{formatSpeed(nestedNumber(player, 'speed', 'avg_speed_kmh') || nestedNumber(player, 'movement_stats', 'avg_speed_kmh'))}</td>
                        <td>{formatSpeed(nestedNumber(player, 'speed', 'peak_sustained_speed_kmh') || nestedNumber(player, 'movement_stats', 'peak_sustained_speed_kmh') || nestedNumber(player, 'movement_stats', 'top_speed_kmh'))}</td>
                        <td>{recordText(nestedRecord(player, 'distance'), 'quality', recordText(nestedRecord(player, 'movement_stats'), 'distance_quality', 'unknown'))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </main>
  );
}
