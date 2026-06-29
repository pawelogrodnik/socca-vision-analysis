import { Link } from 'react-router-dom';
import type { AnalysisReport, MatchPackage } from '../types';

type GenericRow = Record<string, unknown>;

export type MatchReportSource = {
  id: string;
  title?: string | null;
  matchDate?: string | null;
  season?: string | null;
  venue?: string | null;
  format?: string | null;
  status?: string | null;
  analysisReport?: AnalysisReport;
  stablePlayers?: GenericRow;
  globalIdentityReport?: GenericRow;
  frameDetectionCounts?: GenericRow;
  movementStats?: GenericRow;
  playerStats?: GenericRow;
  teamStats?: GenericRow;
  resolvedPlayerStats?: GenericRow;
  artifactMatchId?: string;
  stableOverlay?: string;
};

type MatchReportContentProps = {
  source: MatchReportSource;
  mode: 'local' | 'published';
  artifactHref?: (artifactName: string) => string;
};

export function sourceFromLocalMatch(match: MatchPackage['match']): MatchReportSource {
  return {
    id: match.id,
    title: match.title,
    matchDate: match.match_date,
    season: match.season,
    venue: match.venue,
    format: match.format,
    status: match.status,
    analysisReport: match.analysis_report,
    stablePlayers: match.stable_players as GenericRow | undefined,
    globalIdentityReport: match.global_identity_report as GenericRow | undefined,
    frameDetectionCounts: match.frame_detection_counts as GenericRow | undefined,
    movementStats: match.movement_stats as GenericRow | undefined,
    playerStats: match.player_stats as GenericRow | undefined,
    teamStats: match.team_stats as GenericRow | undefined,
    resolvedPlayerStats: match.resolved_player_stats as GenericRow | undefined,
    artifactMatchId: match.id,
    stableOverlay: match.analysis_report?.artifacts?.stable_overlay_preview,
  };
}

export function sourceFromPublishedPackage(packageData: MatchPackage): MatchReportSource {
  const match = packageData.match;
  const analysisReport = packageData.analysis_report as AnalysisReport | undefined;
  return {
    id: match.id,
    title: match.title,
    matchDate: match.match_date,
    season: match.season,
    venue: match.venue,
    format: match.format,
    status: match.status,
    analysisReport,
    stablePlayers: packageData.stable_players as GenericRow | undefined,
    globalIdentityReport: packageData.global_identity_report as GenericRow | undefined,
    frameDetectionCounts: packageData.frame_detection_counts as GenericRow | undefined,
    movementStats: packageData.movement_stats as GenericRow | undefined,
    playerStats: packageData.player_stats as GenericRow | undefined,
    teamStats: packageData.team_stats as GenericRow | undefined,
    resolvedPlayerStats: packageData.resolved_player_stats as GenericRow | undefined,
    artifactMatchId: match.id,
    stableOverlay: analysisReport?.artifacts?.stable_overlay_preview,
  };
}

function asRows(value: unknown): GenericRow[] {
  return Array.isArray(value)
    ? value.filter(
        (item): item is GenericRow =>
          Boolean(item) && typeof item === 'object' && !Array.isArray(item),
      )
    : [];
}

function recordNumber(record: GenericRow | undefined | null, key: string): number {
  const value = record?.[key];
  return typeof value === 'number' ? value : 0;
}

function recordText(
  record: GenericRow | undefined | null,
  key: string,
  fallback = 'n/a',
): string {
  const value = record?.[key];
  if (value == null || value === '') return fallback;
  return String(value);
}

function nestedRecord(record: GenericRow | undefined | null, key: string): GenericRow {
  return maybeNestedRecord(record, key) || {};
}

function maybeNestedRecord(
  record: GenericRow | undefined | null,
  key: string,
): GenericRow | undefined {
  const value = record?.[key];
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as GenericRow)
    : undefined;
}

function nestedNumber(
  record: GenericRow | undefined | null,
  group: string,
  key: string,
): number {
  return recordNumber(nestedRecord(record, group), key);
}

function doubleNestedNumber(
  record: GenericRow | undefined | null,
  group: string,
  nestedGroup: string,
  key: string,
): number {
  return recordNumber(nestedRecord(nestedRecord(record, group), nestedGroup), key);
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

function stablePlayerRows(source: MatchReportSource): GenericRow[] {
  return asRows(source.playerStats?.players || source.stablePlayers?.players);
}

function resolvedPlayerRows(source: MatchReportSource): GenericRow[] {
  return asRows(source.resolvedPlayerStats?.players);
}

function teamRows(source: MatchReportSource): GenericRow[] {
  return asRows(source.teamStats?.teams || source.playerStats?.teams);
}

function visibleMetricSummary(source: MatchReportSource): GenericRow {
  return (
    maybeNestedRecord(source.frameDetectionCounts, 'summary') ||
    maybeNestedRecord(source.globalIdentityReport, 'frame_detection_summary') ||
    {}
  );
}

function playerDisplayName(row: GenericRow): string {
  const number = recordText(row, 'player_number', '');
  const name = recordText(
    row,
    'player_name',
    recordText(row, 'player_id', 'Unknown player'),
  );
  return number ? `#${number} ${name}` : name;
}

function stableSlotLabel(row: GenericRow): string {
  return recordText(row, 'stable_player_id', recordText(row, 'slot_id', 'n/a'));
}

function reportDateLine(source: MatchReportSource): string {
  return `${source.matchDate || 'brak daty'} | ${source.season || 'brak sezonu'} | ${source.venue || 'brak miejsca'}`;
}

export function MatchReportContent({
  source,
  mode,
  artifactHref,
}: MatchReportContentProps) {
  const stablePlayers = stablePlayerRows(source);
  const resolvedPlayers = resolvedPlayerRows(source);
  const teams = teamRows(source);
  const frameSummary = visibleMetricSummary(source);
  const movementSummary =
    maybeNestedRecord(source.playerStats, 'summary') ||
    maybeNestedRecord(source.movementStats, 'summary') ||
    {};
  const stableSummary = maybeNestedRecord(source.stablePlayers, 'summary') || {};
  const resolvedSummary =
    maybeNestedRecord(source.resolvedPlayerStats, 'summary') || {};
  const stableOverlayHref =
    source.stableOverlay && artifactHref ? artifactHref(source.stableOverlay) : '';

  return (
    <>
      <section className='card'>
        <div className='row between'>
          <div>
            <h2>Podsumowanie</h2>
            <p className='muted'>{reportDateLine(source)}</p>
          </div>
          {mode === 'local' ? (
            <Link to='/admin-panel'>Edytuj analize</Link>
          ) : (
            <Link to='/'>Lista meczow</Link>
          )}
        </div>
        <div className='chips'>
          <span>Status: {source.status || 'uploaded'}</span>
          <span>Format: {source.format || '7v7'}</span>
          <span>Stable slots: {recordNumber(stableSummary, 'stable_players')}</span>
          <span>Resolved players: {recordNumber(resolvedSummary, 'players')}</span>
          <span>Total distance: {formatMeters(recordNumber(movementSummary, 'total_distance_m'))}</span>
          <span>
            Peak:{' '}
            {formatSpeed(
              recordNumber(movementSummary, 'peak_sustained_speed_kmh') ||
                recordNumber(movementSummary, 'top_speed_kmh'),
            )}
          </span>
          <span>Sprinty: {recordNumber(movementSummary, 'sprint_count')}</span>
          <span>Sprint dist: {formatMeters(recordNumber(movementSummary, 'sprint_distance_m'))}</span>
          <span>HI dist: {formatMeters(recordNumber(movementSummary, 'high_intensity_distance_m'))}</span>
          <span>Visible avg: {recordNumber(frameSummary, 'stable_avg').toFixed(1)}</span>
          <span>Warnings: {source.analysisReport?.warnings?.length || 0}</span>
        </div>
      </section>

      {stableOverlayHref && (
        <section className='card'>
          <h2>Stable overlay</h2>
          <video controls src={stableOverlayHref} className='video' />
        </section>
      )}

      <section className='card'>
        <h2>Porownanie druzyn</h2>
        {teams.length === 0 ? (
          <p className='muted'>Brak team_stats.json. Uruchom ponownie analize albo opublikuj nowszy snapshot.</p>
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
                  <th>HI dist</th>
                  <th>Sprinty</th>
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
                    <td>{formatMeters(recordNumber(team, 'high_intensity_distance_m'))}</td>
                    <td>
                      {recordNumber(team, 'sprint_count')}
                      <span>{formatMeters(recordNumber(team, 'sprint_distance_m'))}</span>
                    </td>
                    <td>
                      {formatSpeed(
                        recordNumber(team, 'peak_sustained_speed_kmh') ||
                          recordNumber(team, 'top_speed_kmh'),
                      )}
                    </td>
                    <td>
                      low {recordNumber(team, 'players_low_quality')} / med{' '}
                      {recordNumber(team, 'players_medium_quality')} / high{' '}
                      {recordNumber(team, 'players_high_quality')}
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
                  <th>Sprinty</th>
                  <th>Peak</th>
                  <th>Profil</th>
                </tr>
              </thead>
              <tbody>
                {resolvedPlayers.map((player) => {
                  const playerId = recordText(player, 'player_id', '');
                  const slots = asRows(player.source_stable_slots)
                    .map(stableSlotLabel)
                    .join(', ');
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
                      <td>
                        {nestedNumber(player, 'intensity', 'sprint_count')}
                        <span>
                          {formatMeters(nestedNumber(player, 'intensity', 'sprint_distance_m'))}
                        </span>
                      </td>
                      <td>{formatSpeed(nestedNumber(player, 'speed', 'peak_sustained_speed_kmh'))}</td>
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
        <h2>Wszyscy stable zawodnicy w meczu</h2>
        {stablePlayers.length === 0 ? (
          <p className='muted'>Brak player_stats.json albo stable_players.json.</p>
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
                  <th>HI dist</th>
                  <th>Sprinty</th>
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
                    <td>
                      {formatSeconds(
                        nestedNumber(player, 'time', 'playing_time_sec') ||
                          recordNumber(player, 'duration_sec'),
                      )}
                    </td>
                    <td>
                      {formatMeters(
                        nestedNumber(player, 'distance', 'total_distance_m') ||
                          nestedNumber(player, 'movement_stats', 'total_distance_m'),
                      )}
                    </td>
                    <td>
                      {formatMeters(
                        nestedNumber(player, 'distance', 'observed_distance_m') ||
                          nestedNumber(player, 'movement_stats', 'observed_distance_m'),
                      )}
                    </td>
                    <td>
                      {formatMeters(
                        nestedNumber(player, 'distance', 'estimated_short_gap_distance_m') ||
                          nestedNumber(player, 'movement_stats', 'estimated_gap_distance_m'),
                      )}
                    </td>
                    <td>
                      {formatMeters(
                        nestedNumber(player, 'intensity', 'high_intensity_distance_m') ||
                          doubleNestedNumber(
                            player,
                            'movement_stats',
                            'intensity',
                            'high_intensity_distance_m',
                          ),
                      )}
                    </td>
                    <td>
                      {nestedNumber(player, 'intensity', 'sprint_count') ||
                        doubleNestedNumber(player, 'movement_stats', 'intensity', 'sprint_count')}
                      <span>
                        {formatMeters(
                          nestedNumber(player, 'intensity', 'sprint_distance_m') ||
                            doubleNestedNumber(
                              player,
                              'movement_stats',
                              'intensity',
                              'sprint_distance_m',
                            ),
                        )}
                      </span>
                    </td>
                    <td>
                      {formatSpeed(
                        nestedNumber(player, 'speed', 'avg_speed_kmh') ||
                          nestedNumber(player, 'movement_stats', 'avg_speed_kmh'),
                      )}
                    </td>
                    <td>
                      {formatSpeed(
                        nestedNumber(player, 'speed', 'peak_sustained_speed_kmh') ||
                          nestedNumber(player, 'movement_stats', 'peak_sustained_speed_kmh') ||
                          nestedNumber(player, 'movement_stats', 'top_speed_kmh'),
                      )}
                    </td>
                    <td>
                      {recordText(
                        nestedRecord(player, 'distance'),
                        'quality',
                        recordText(nestedRecord(player, 'movement_stats'), 'distance_quality', 'unknown'),
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
