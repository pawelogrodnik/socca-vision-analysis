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
  analysisQualityReport?: GenericRow;
  frameDetectionCounts?: GenericRow;
  movementStats?: GenericRow;
  playerStats?: GenericRow;
  teamStats?: GenericRow;
  resolvedPlayerStats?: GenericRow;
  playerHeatmaps?: GenericRow;
  changeCandidates?: GenericRow;
  possessionReport?: GenericRow;
  passCandidates?: GenericRow;
  passReviewReport?: GenericRow;
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
    analysisQualityReport: match.analysis_quality_report as GenericRow | undefined,
    frameDetectionCounts: match.frame_detection_counts as GenericRow | undefined,
    movementStats: match.movement_stats as GenericRow | undefined,
    playerStats: match.player_stats as GenericRow | undefined,
    teamStats: match.team_stats as GenericRow | undefined,
    resolvedPlayerStats: match.resolved_player_stats as GenericRow | undefined,
    playerHeatmaps: match.player_heatmaps as GenericRow | undefined,
    changeCandidates: match.change_candidates as GenericRow | undefined,
    possessionReport: match.possession_report as GenericRow | undefined,
    passCandidates: match.pass_candidates as GenericRow | undefined,
    passReviewReport: match.pass_review_report as GenericRow | undefined,
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
    analysisQualityReport: packageData.analysis_quality_report as GenericRow | undefined,
    frameDetectionCounts: packageData.frame_detection_counts as GenericRow | undefined,
    movementStats: packageData.movement_stats as GenericRow | undefined,
    playerStats: packageData.player_stats as GenericRow | undefined,
    teamStats: packageData.team_stats as GenericRow | undefined,
    resolvedPlayerStats: packageData.resolved_player_stats as GenericRow | undefined,
    playerHeatmaps: packageData.player_heatmaps as GenericRow | undefined,
    changeCandidates: packageData.change_candidates as GenericRow | undefined,
    possessionReport: packageData.possession_report as GenericRow | undefined,
    passCandidates: packageData.pass_candidates as GenericRow | undefined,
    passReviewReport: packageData.pass_review_report as GenericRow | undefined,
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

function objectRows(value: unknown): GenericRow[] {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? Object.values(value).filter(
        (item): item is GenericRow =>
          Boolean(item) && typeof item === 'object' && !Array.isArray(item),
      )
    : [];
}

function stringRows(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

function recordNumber(record: GenericRow | undefined | null, key: string): number {
  const value = record?.[key];
  return typeof value === 'number' ? value : 0;
}

function recordMaybeNumber(
  record: GenericRow | undefined | null,
  key: string,
): number | undefined {
  const value = record?.[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
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

function candidateLabel(candidate: unknown): string {
  if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) {
    return 'n/a';
  }
  const row = candidate as GenericRow;
  const speed = recordNumber(row, 'max_speed_kmh');
  const duration = recordNumber(row, 'duration_sec');
  const reason = recordText(row, 'reason', 'unknown');
  if (speed <= 0) return 'n/a';
  return `${formatSpeed(speed)} / ${duration.toFixed(2)}s / ${reason}`;
}

function bestRejectedCandidateFrom(row: GenericRow): unknown {
  const direct = nestedRecord(row, 'intensity').best_rejected_sprint_candidate;
  if (direct) return direct;
  return nestedRecord(nestedRecord(row, 'movement_stats'), 'intensity').best_rejected_sprint_candidate;
}

function sprintMetric(row: GenericRow, key: string): number {
  return (
    nestedNumber(row, 'intensity', key) ||
    doubleNestedNumber(row, 'movement_stats', 'intensity', key)
  );
}

function formatMeters(value: number): string {
  return `${value.toFixed(1)} m`;
}

function formatSpeed(value: number): string {
  return `${value.toFixed(1)} km/h`;
}

function formatInteger(value: number): string {
  return `${Math.round(value)}`;
}

function formatPercent(value: number): string {
  return `${value.toFixed(0)}%`;
}

function formatSeconds(value: number): string {
  if (value < 60) return `${value.toFixed(1)}s`;
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value % 60);
  return `${minutes}m ${seconds}s`;
}

function formatMaybe(
  value: number | undefined,
  formatter: (value: number) => string,
): string {
  return value == null ? '--' : formatter(value);
}

function formatScore(value: number): string {
  return `${value.toFixed(1)}/100`;
}

function qualityBadgeClass(value: string): string {
  const normalized = value.toLowerCase();
  if (normalized === 'high') return 'quality-badge high';
  if (normalized === 'medium') return 'quality-badge medium';
  if (normalized === 'low') return 'quality-badge low';
  return 'quality-badge';
}

function teamName(row: GenericRow): string {
  return recordText(row, 'team_name', recordText(row, 'team_label', 'Unknown'));
}

type ComparisonMetric = {
  label: string;
  leftText: string;
  rightText: string;
  leftValue?: number;
  rightValue?: number;
  highlightHigher?: boolean;
  experimental?: boolean;
};

function orderedComparisonTeams(teams: GenericRow[]): GenericRow[] {
  const teamA = teams.find((team) => recordText(team, 'team_label', '') === 'A');
  const teamB = teams.find((team) => recordText(team, 'team_label', '') === 'B');
  if (teamA && teamB) return [teamA, teamB];
  return teams.slice(0, 2);
}

function teamColor(row: GenericRow, fallback: string): string {
  const color = recordText(row, 'display_color', '');
  return color || fallback;
}

function firstTeamMetric(row: GenericRow, keys: string[]): number | undefined {
  for (const key of keys) {
    const value = recordMaybeNumber(row, key);
    if (value != null) return value;
  }
  return undefined;
}

function teamAvgSpeed(row: GenericRow): number | undefined {
  const explicit = firstTeamMetric(row, ['avg_speed_kmh', 'average_speed_kmh']);
  if (explicit != null) return explicit;
  const distance = firstTeamMetric(row, ['total_distance_m']);
  const time = firstTeamMetric(row, ['playing_time_sec']);
  if (distance == null || time == null || time <= 0) return undefined;
  return (distance / time) * 3.6;
}

function makeMetric(
  label: string,
  leftValue: number | undefined,
  rightValue: number | undefined,
  formatter: (value: number) => string,
  options: Pick<ComparisonMetric, 'highlightHigher' | 'experimental'> = {},
): ComparisonMetric {
  return {
    label,
    leftText: formatMaybe(leftValue, formatter),
    rightText: formatMaybe(rightValue, formatter),
    leftValue,
    rightValue,
    ...options,
  };
}

function possessionShare(source: MatchReportSource, teamLabel: string): number | undefined {
  const summary = maybeNestedRecord(source.possessionReport, 'summary');
  const controlledFrames = maybeNestedRecord(summary, 'team_controlled_frames');
  if (!controlledFrames) return undefined;
  const values = Object.values(controlledFrames).filter(
    (value): value is number => typeof value === 'number' && Number.isFinite(value),
  );
  const total = values.reduce((sum, value) => sum + value, 0);
  if (total <= 0) return undefined;
  return ((recordMaybeNumber(controlledFrames, teamLabel) || 0) / total) * 100;
}

function teamPassCount(
  source: MatchReportSource,
  teamLabel: string,
  predicate: (candidate: GenericRow) => boolean,
): number | undefined {
  const candidates = asRows(source.passCandidates?.candidates);
  if (candidates.length === 0) return undefined;
  return candidates.filter(
    (candidate) =>
      recordText(candidate, 'from_team_label', '') === teamLabel && predicate(candidate),
  ).length;
}

function isAcceptedPass(candidate: GenericRow): boolean {
  return (
    candidate.final_stat_eligible === true ||
    recordText(candidate, 'review_status', '') === 'accepted'
  );
}

function isSameTeamPassCandidate(candidate: GenericRow): boolean {
  return recordText(candidate, 'pass_type', '') === 'same_team_pass';
}

function comparisonRowsForTeams(
  source: MatchReportSource,
  leftTeam: GenericRow,
  rightTeam: GenericRow,
): ComparisonMetric[] {
  const leftLabel = recordText(leftTeam, 'team_label', '');
  const rightLabel = recordText(rightTeam, 'team_label', '');
  const rows = [
    makeMetric(
      'Sloty zawodnikow',
      firstTeamMetric(leftTeam, ['players']),
      firstTeamMetric(rightTeam, ['players']),
      formatInteger,
    ),
    makeMetric(
      'Czas detected',
      firstTeamMetric(leftTeam, ['detected_time_sec', 'playing_time_sec']),
      firstTeamMetric(rightTeam, ['detected_time_sec', 'playing_time_sec']),
      formatSeconds,
      { highlightHigher: true },
    ),
    makeMetric(
      'Czas missing',
      firstTeamMetric(leftTeam, ['missing_time_sec']),
      firstTeamMetric(rightTeam, ['missing_time_sec']),
      formatSeconds,
    ),
    makeMetric(
      'Dystans',
      firstTeamMetric(leftTeam, ['total_distance_m']),
      firstTeamMetric(rightTeam, ['total_distance_m']),
      formatMeters,
      { highlightHigher: true },
    ),
    makeMetric(
      'Dystans z luk',
      firstTeamMetric(leftTeam, ['estimated_short_gap_distance_m']),
      firstTeamMetric(rightTeam, ['estimated_short_gap_distance_m']),
      formatMeters,
    ),
    makeMetric('Avg speed', teamAvgSpeed(leftTeam), teamAvgSpeed(rightTeam), formatSpeed, {
      highlightHigher: true,
    }),
    makeMetric(
      'Top speed',
      firstTeamMetric(leftTeam, ['peak_sustained_speed_kmh', 'top_speed_kmh']),
      firstTeamMetric(rightTeam, ['peak_sustained_speed_kmh', 'top_speed_kmh']),
      formatSpeed,
      { highlightHigher: true },
    ),
    makeMetric(
      'HI distance',
      firstTeamMetric(leftTeam, ['high_intensity_distance_m']),
      firstTeamMetric(rightTeam, ['high_intensity_distance_m']),
      formatMeters,
      { highlightHigher: true },
    ),
    makeMetric(
      'Sprinty',
      firstTeamMetric(leftTeam, ['sprint_count']),
      firstTeamMetric(rightTeam, ['sprint_count']),
      formatInteger,
      { highlightHigher: true },
    ),
    makeMetric(
      'Low quality slots',
      firstTeamMetric(leftTeam, ['players_low_quality']),
      firstTeamMetric(rightTeam, ['players_low_quality']),
      formatInteger,
    ),
    makeMetric(
      'Posiadanie znane*',
      possessionShare(source, leftLabel),
      possessionShare(source, rightLabel),
      formatPercent,
      { experimental: true, highlightHigher: true },
    ),
    makeMetric(
      'Podania zaakc.*',
      teamPassCount(source, leftLabel, isAcceptedPass),
      teamPassCount(source, rightLabel, isAcceptedPass),
      formatInteger,
      { experimental: true, highlightHigher: true },
    ),
    makeMetric(
      'Podania teamowe cand.*',
      teamPassCount(source, leftLabel, isSameTeamPassCandidate),
      teamPassCount(source, rightLabel, isSameTeamPassCandidate),
      formatInteger,
      { experimental: true, highlightHigher: true },
    ),
    makeMetric(
      'Kandydaci podan*',
      teamPassCount(source, leftLabel, () => true),
      teamPassCount(source, rightLabel, () => true),
      formatInteger,
      { experimental: true, highlightHigher: true },
    ),
    makeMetric(
      'Progresywne*',
      teamPassCount(source, leftLabel, (candidate) => candidate.is_progressive === true),
      teamPassCount(source, rightLabel, (candidate) => candidate.is_progressive === true),
      formatInteger,
      { experimental: true, highlightHigher: true },
    ),
  ];
  return rows.filter((row) => row.leftValue != null || row.rightValue != null);
}

function leaderClass(metric: ComparisonMetric, side: 'left' | 'right'): string {
  if (!metric.highlightHigher || metric.leftValue == null || metric.rightValue == null) {
    return '';
  }
  if (metric.leftValue === metric.rightValue) return '';
  const isLeader =
    side === 'left'
      ? metric.leftValue > metric.rightValue
      : metric.rightValue > metric.leftValue;
  return isLeader ? ' leader' : '';
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

function changeRows(source: MatchReportSource): GenericRow[] {
  return asRows(source.changeCandidates?.candidates);
}

function heatmapRows(source: MatchReportSource): GenericRow[] {
  return asRows(source.playerHeatmaps?.heatmaps);
}

function possessionSummary(source: MatchReportSource): GenericRow {
  return maybeNestedRecord(source.possessionReport, 'summary') || {};
}

function passSummary(source: MatchReportSource): GenericRow {
  return maybeNestedRecord(source.passCandidates, 'summary') || {};
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
  const changes = changeRows(source);
  const heatmaps = heatmapRows(source);
  const frameSummary = visibleMetricSummary(source);
  const movementSummary =
    maybeNestedRecord(source.playerStats, 'summary') ||
    maybeNestedRecord(source.movementStats, 'summary') ||
    {};
  const qualityReport = source.analysisQualityReport;
  const qualitySummary = maybeNestedRecord(qualityReport, 'summary') || {};
  const qualityComponents = objectRows(qualityReport?.components);
  const qualityWarnings = stringRows(qualityReport?.warnings);
  const topProblemFrames = asRows(qualityReport?.top_problem_frames).slice(0, 8);
  const stableSummary = maybeNestedRecord(source.stablePlayers, 'summary') || {};
  const resolvedSummary =
    maybeNestedRecord(source.resolvedPlayerStats, 'summary') || {};
  const stableOverlayHref =
    source.stableOverlay && artifactHref ? artifactHref(source.stableOverlay) : '';
  const comparisonTeams = orderedComparisonTeams(teams);
  const leftTeam = comparisonTeams[0];
  const rightTeam = comparisonTeams[1];
  const comparisonMetrics =
    leftTeam && rightTeam ? comparisonRowsForTeams(source, leftTeam, rightTeam) : [];
  const ballSummary = possessionSummary(source);
  const passesSummary = passSummary(source);
  const hasBallStats = source.possessionReport || source.passCandidates;

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
          <span>Sprint candidates: {recordNumber(movementSummary, 'sprint_candidate_count')}</span>
          <span>Rejected candidates: {recordNumber(movementSummary, 'rejected_sprint_candidate_count')}</span>
          <span>Best candidate: {formatSpeed(recordNumber(movementSummary, 'best_sprint_candidate_speed_kmh'))}</span>
          <span>HI dist: {formatMeters(recordNumber(movementSummary, 'high_intensity_distance_m'))}</span>
          <span>Visible avg: {recordNumber(frameSummary, 'stable_avg').toFixed(1)}</span>
          <span>Warnings: {source.analysisReport?.warnings?.length || 0}</span>
        </div>
      </section>

      {hasBallStats && (
        <section className='card ball-stats-card'>
          <div className='row between'>
            <div>
              <h2>Posiadanie i podania</h2>
              <p className='muted'>
                Warstwa eksperymentalna z pilki: dobra do trendow i review, nie
                traktuj jej jeszcze jak oficjalnych statystyk.
              </p>
            </div>
            <span className='confidence-pill'>experimental</span>
          </div>
          <div className='chips'>
            <span>
              Known possession:{' '}
              {formatPercent(recordNumber(ballSummary, 'known_possession_coverage') * 100)}
            </span>
            <span>Controlled frames: {recordNumber(ballSummary, 'controlled_frames')}</span>
            <span>Free frames: {recordNumber(ballSummary, 'free_frames')}</span>
            <span>Unknown frames: {recordNumber(ballSummary, 'unknown_frames')}</span>
            <span>Pass candidates: {recordNumber(passesSummary, 'pass_candidates')}</span>
            <span>Same-team candidates: {recordNumber(passesSummary, 'same_team_pass_candidates')}</span>
            <span>Turnovers/interceptions: {recordNumber(passesSummary, 'turnover_or_interception_candidates')}</span>
            <span>Progressive candidates: {recordNumber(passesSummary, 'progressive_pass_candidates')}</span>
            <span>Final accepted: {recordNumber(passesSummary, 'final_stat_passes')}</span>
          </div>
        </section>
      )}

      {qualityReport && (
        <section className='card analysis-quality-card'>
          <div className='row between'>
            <div>
              <h2>Jakosc analizy</h2>
              <p className='muted'>{recordText(qualityReport, 'recommendation', 'Brak rekomendacji.')}</p>
            </div>
            <span className={qualityBadgeClass(recordText(qualityReport, 'quality', 'unknown'))}>
              {recordText(qualityReport, 'quality', 'unknown')} |{' '}
              {formatScore(recordNumber(qualityReport, 'score'))}
            </span>
          </div>

          <div className='quality-component-grid'>
            {qualityComponents.map((component) => (
              <div className='quality-component' key={recordText(component, 'name', '')}>
                <div className='row between'>
                  <strong>{recordText(component, 'name', 'quality')}</strong>
                  <span className={qualityBadgeClass(recordText(component, 'quality', 'unknown'))}>
                    {formatScore(recordNumber(component, 'score'))}
                  </span>
                </div>
                <p className='muted'>
                  {recordText(component, 'quality', 'unknown')}
                </p>
              </div>
            ))}
          </div>

          <div className='chips'>
            <span>Target: {recordNumber(qualitySummary, 'target_players')}</span>
            <span>Visible avg: {recordNumber(qualitySummary, 'visible_avg').toFixed(1)}</span>
            <span>Low visible frames: {recordNumber(qualitySummary, 'low_visible_frames')}</span>
            <span>Missing frames: {recordNumber(qualitySummary, 'missing_frame_count')}</span>
            <span>Ambiguous frames: {recordNumber(qualitySummary, 'ambiguous_frame_count')}</span>
            <span>Visual hold: {recordNumber(qualitySummary, 'visual_interpolated_boxes')}</span>
            <span>Ghost boxes: {recordNumber(qualitySummary, 'ghost_bbox_count')}</span>
          </div>

          {qualityWarnings.length > 0 && (
            <div className='quality-alert'>
              {qualityWarnings.map((warning) => (
                <span key={warning}>{warning}</span>
              ))}
            </div>
          )}

          {topProblemFrames.length > 0 && (
            <div className='stats-table-wrap compact'>
              <table className='stats-table'>
                <thead>
                  <tr>
                    <th>Frame</th>
                    <th>Visible</th>
                    <th>Raw</th>
                    <th>Missing</th>
                    <th>Ambiguous</th>
                    <th>Severity</th>
                  </tr>
                </thead>
                <tbody>
                  {topProblemFrames.map((frame) => (
                    <tr key={recordText(frame, 'frame', '')}>
                      <td>
                        <strong>{formatInteger(recordNumber(frame, 'frame'))}</strong>
                        <span>{formatSeconds(recordNumber(frame, 'time_sec'))}</span>
                      </td>
                      <td>{recordNumber(frame, 'visible_stable_boxes')}</td>
                      <td>{recordNumber(frame, 'raw_detections')}</td>
                      <td>{recordNumber(frame, 'slot_missing')}</td>
                      <td>{recordNumber(frame, 'slot_ambiguous')}</td>
                      <td>{recordNumber(frame, 'severity_score')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {stableOverlayHref && (
        <section className='card'>
          <h2>Stable overlay</h2>
          <video controls src={stableOverlayHref} className='video' />
        </section>
      )}

      <section className='card team-comparison-card'>
        <h2>Statystyki druzyn</h2>
        {!leftTeam || !rightTeam ? (
          <p className='muted'>
            Brak pelnego team_stats.json dla dwoch druzyn. Uruchom ponownie
            analize albo opublikuj nowszy snapshot.
          </p>
        ) : (
          <>
            <div className='team-comparison-header'>
              <div className='team-comparison-side left'>
                <span
                  className='team-comparison-swatch'
                  style={{ background: teamColor(leftTeam, '#e5e7eb') }}
                />
                <div>
                  <strong>{teamName(leftTeam)}</strong>
                  <span>{recordText(leftTeam, 'team_label', 'Team A')}</span>
                </div>
              </div>
              <div className='team-comparison-title'>STATYSTYKI DRUZYN</div>
              <div className='team-comparison-side right'>
                <div>
                  <strong>{teamName(rightTeam)}</strong>
                  <span>{recordText(rightTeam, 'team_label', 'Team B')}</span>
                </div>
                <span
                  className='team-comparison-swatch'
                  style={{ background: teamColor(rightTeam, '#2563eb') }}
                />
              </div>
            </div>

            <div className='team-comparison-list'>
              {comparisonMetrics.map((metric) => (
                <div
                  className={`team-comparison-row${metric.experimental ? ' experimental' : ''}`}
                  key={metric.label}
                >
                  <div className={`team-comparison-value left${leaderClass(metric, 'left')}`}>
                    <span>{metric.leftText}</span>
                  </div>
                  <div className='team-comparison-label'>{metric.label}</div>
                  <div className={`team-comparison-value right${leaderClass(metric, 'right')}`}>
                    <span>{metric.rightText}</span>
                  </div>
                </div>
              ))}
            </div>

            <p className='team-comparison-note'>
              Tracking-only: dystans, predkosc, czas i sprinty bazuja na stable
              slotach. Gwiazdka oznacza metryki eksperymentalne z pilki/podan,
              ktore wymagaja dalszego review modelu.
            </p>
          </>
        )}
      </section>

      {source.changeCandidates && (
        <section className='card'>
          <h2>Zmiany zawodnikow</h2>
          <div className='chips'>
            <span>Kandydaci: {recordNumber(maybeNestedRecord(source.changeCandidates, 'summary'), 'change_candidates')}</span>
            <span>Do review: {recordNumber(maybeNestedRecord(source.changeCandidates, 'summary'), 'needs_review_candidates')}</span>
            <span>Confirmed: {recordNumber(maybeNestedRecord(source.changeCandidates, 'summary'), 'confirmed_candidates')}</span>
            <span>Uncertain: {recordNumber(maybeNestedRecord(source.changeCandidates, 'summary'), 'uncertain_candidates')}</span>
          </div>
          {changes.length === 0 ? (
            <p className='muted'>
              Brak wykrytych zmian w tym materiale albo sample jest za krotki.
            </p>
          ) : (
            <div className='stats-table-wrap'>
              <table className='stats-table'>
                <thead>
                  <tr>
                    <th>Czas</th>
                    <th>Druzyna</th>
                    <th>Sugestia</th>
                    <th>Powrot/real player</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {changes.map((candidate) => (
                    <tr key={recordText(candidate, 'candidate_id', '')}>
                      <td>
                        <strong>{formatSeconds(recordNumber(candidate, 'time_sec'))}</strong>
                        <span>gap {formatSeconds(recordNumber(candidate, 'gap_sec'))}</span>
                      </td>
                      <td>{teamName(candidate)}</td>
                      <td>
                        <strong>
                          {recordText(candidate, 'out_stable_player_id', '?')} off -{' '}
                          {recordText(candidate, 'in_stable_player_id', '?')} on
                        </strong>
                        <span>confidence {recordText(candidate, 'confidence', 'n/a')}</span>
                      </td>
                      <td>
                        {recordText(candidate, 'linked_existing_stable_subject_id', '') ||
                          recordText(candidate, 'suggested_existing_stable_subject_id', 'new anonymous slot')}
                        <span>
                          {recordText(candidate, 'reviewed_player_id', '') ||
                            recordText(candidate, 'suggested_real_player_name', 'no roster player')}
                        </span>
                      </td>
                      <td>{recordText(candidate, 'review_status', 'needs_review')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

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
                        <span>
                          cand {nestedNumber(player, 'intensity', 'sprint_candidate_count')} / rej{' '}
                          {nestedNumber(player, 'intensity', 'rejected_sprint_candidate_count')}
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

      {heatmaps.length > 0 && (
        <section className='card'>
          <h2>Heatmapy zawodnikow</h2>
          <p className='muted'>
            Heatmapy bazuja na stable slotach. Po przypisaniu stintow do realnych
            zawodnikow traktuj je jako material pomocniczy do review pokrycia.
          </p>
          <div className='player-heatmap-grid'>
            {heatmaps.map((heatmap) => {
              const path = recordText(
                heatmap,
                'path',
                recordText(heatmap, 'heatmap_path', ''),
              );
              const label = recordText(
                heatmap,
                'stable_player_id',
                recordText(heatmap, 'slot_id', 'slot'),
              );
              return (
                <figure className='player-heatmap' key={`${label}-${path}`}>
                  {path && artifactHref ? (
                    <img src={artifactHref(path)} alt={`Heatmapa ${label}`} />
                  ) : (
                    <div className='player-heatmap-placeholder'>
                      <span>Brak podgladu heatmapy.</span>
                    </div>
                  )}
                  <figcaption>
                    {label} - {recordText(heatmap, 'team_name', recordText(heatmap, 'team_label', 'Team'))}
                    <br />
                    {recordNumber(heatmap, 'samples')} probek -{' '}
                    {recordText(heatmap, 'quality', 'unknown')}
                  </figcaption>
                </figure>
              );
            })}
          </div>
        </section>
      )}

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
                      <span>
                        cand {sprintMetric(player, 'sprint_candidate_count')} / rej{' '}
                        {sprintMetric(player, 'rejected_sprint_candidate_count')}
                      </span>
                      {sprintMetric(player, 'sprint_count') === 0 &&
                        sprintMetric(player, 'rejected_sprint_candidate_count') > 0 && (
                          <span>best rejected {candidateLabel(bestRejectedCandidateFrom(player))}</span>
                        )}
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
