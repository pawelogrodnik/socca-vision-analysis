import { artifactUrl } from '../api';
import type {
  AnalysisRunSummary,
  FrameDetectionCountsDocument,
  GlobalIdentityReport,
  Match,
  PlayerStatsDocument,
  TrackingQualityReport,
} from '../types';

interface AnalysisQualityPanelProps {
  match: Match;
}

function valueOf(record: Record<string, unknown> | undefined | null, key: string): unknown {
  return record?.[key];
}

function numberValue(record: Record<string, unknown> | undefined | null, key: string): number | null {
  const value = valueOf(record, key);
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function peakSpeedValue(record: Record<string, unknown> | undefined | null): number | null {
  return numberValue(record, 'peak_sustained_speed_kmh') ?? numberValue(record, 'top_speed_kmh');
}

function textValue(record: Record<string, unknown> | undefined | null, key: string): string {
  const value = valueOf(record, key);
  if (value == null || value === '') return 'n/a';
  return String(value);
}

function formatNumber(value: number | null, digits = 1): string {
  if (value == null) return 'n/a';
  return value.toFixed(digits);
}

function formatCompact(value: unknown): string {
  if (value == null || value === '') return 'n/a';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(3);
  return String(value);
}

function numericValue(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

function runParam(run: AnalysisRunSummary, key: string): string {
  return formatCompact(run.parameters?.[key]);
}

function eventFrame(event: Record<string, unknown>): string {
  const frame = valueOf(event, 'frame');
  const startFrame = valueOf(event, 'start_frame');
  const endFrame = valueOf(event, 'end_frame');
  if (frame != null) return `f${frame}`;
  if (startFrame != null || endFrame != null) return `f${startFrame ?? '?'}-${endFrame ?? '?'}`;
  return 'frame n/a';
}

function visibleFrameRows(frameCounts?: FrameDetectionCountsDocument): Array<Record<string, unknown>> {
  return (frameCounts?.frames || [])
    .filter((frame) => Number(valueOf(frame, 'visible_stable_boxes') || 0) < Number(frameCounts?.target_players || 14))
    .slice(0, 30);
}

function teamOverCapRows(trackingQuality?: TrackingQualityReport): Array<Record<string, unknown>> {
  return (trackingQuality?.frame_team_counts || [])
    .filter((frame) => Boolean(valueOf(frame, 'team_over_cap')))
    .slice(0, 30);
}

function teamCountsLabel(frame: Record<string, unknown>): string {
  const counts = valueOf(frame, 'team_counts');
  if (!counts || typeof counts !== 'object' || Array.isArray(counts)) {
    return formatCompact(counts);
  }

  const parts = Object.entries(counts as Record<string, unknown>)
    .map(([label, count]) => `${label}:${formatCompact(count)}`)
    .join(' / ');
  return parts || 'n/a';
}

function Sparkline({ frames }: { frames: Array<Record<string, unknown>> }) {
  const values = frames
    .slice(0, 220)
    .map((frame) => Number(valueOf(frame, 'visible_stable_boxes') || 0));
  const max = Math.max(14, ...values);
  const points = values
    .map((value, index) => {
      const x = values.length <= 1 ? 0 : (index / (values.length - 1)) * 100;
      const y = 30 - (value / max) * 28;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(' ');

  return (
    <svg className='quality-sparkline' viewBox='0 0 100 32' role='img'>
      <line x1='0' y1='2' x2='100' y2='2' />
      <line x1='0' y1='30' x2='100' y2='30' />
      {points && <polyline points={points} />}
    </svg>
  );
}

function RunList({ match }: { match: Match }) {
  const runs = match.analysis_runs || [];
  if (runs.length === 0) {
    return <p className='muted'>Brak zapisanych runow analizy.</p>;
  }

  return (
    <div className='quality-list'>
      {runs.map((run) => {
        const isLatest = run.run_id === match.latest_analysis_run_id;
        return (
          <div className={isLatest ? 'quality-row active' : 'quality-row'} key={run.run_id}>
            <div>
              <strong>{run.run_id || 'run n/a'}</strong>
              <span>
                {run.analysis_type || 'analysis'} - {run.status || 'status n/a'} - frames{' '}
                {run.frames_processed ?? 'n/a'} - tracks {run.tracks_count ?? 'n/a'} - stable{' '}
                {run.stable_players_count ?? 'n/a'}
              </span>
              <span>
                model {runParam(run, 'yolo_model')} - conf {runParam(run, 'yolo_conf')} - imgsz{' '}
                {runParam(run, 'yolo_imgsz')} - tracker {runParam(run, 'yolo_tracker')}
              </span>
            </div>
            {run.run_manifest && (
              <a href={artifactUrl(match.id, run.run_manifest)}>
                run_metadata.json
              </a>
            )}
          </div>
        );
      })}
    </div>
  );
}

function QualitySummary({
  frameCounts,
  trackingQuality,
  globalIdentityReport,
  playerStats,
}: {
  frameCounts?: FrameDetectionCountsDocument;
  trackingQuality?: TrackingQualityReport;
  globalIdentityReport?: GlobalIdentityReport;
  playerStats?: PlayerStatsDocument;
}) {
  const frameSummary = frameCounts?.summary;
  const trackletSummary = trackingQuality?.summary;
  const identitySummary = globalIdentityReport?.summary;
  const statsSummary = playerStats?.summary;
  const overCapFrames = numericValue(valueOf(trackletSummary, 'frames_with_team_over_cap'));

  return (
    <>
      {overCapFrames > 0 && (
        <div className='quality-alert'>
          <strong>Team count alert</strong>
          <span>
            W {formatCompact(overCapFrames)} klatkach system widzial wiecej niz 7 aktywnych obiektow dla jednej
            druzyny. Sprawdz Team config oraz stable slots przed traktowaniem statystyk jako finalnych.
          </span>
        </div>
      )}
      <div className='chips'>
        <span>Visible avg: {formatNumber(numberValue(frameSummary, 'stable_avg'))}</span>
        <span>Visible min: {formatCompact(valueOf(frameSummary, 'stable_min'))}</span>
        <span>Raw avg: {formatNumber(numberValue(frameSummary, 'raw_avg'))}</span>
        <span>Raw below target: {formatCompact(valueOf(frameSummary, 'raw_frames_below_target'))}</span>
        <span>Low visible frames: {formatCompact(valueOf(frameSummary, 'stable_frames_below_target'))}</span>
        <span>Ambiguous frames: {formatCompact(valueOf(frameSummary, 'frames_with_ambiguous_slots'))}</span>
        <span>Ghost boxes: {formatCompact(valueOf(frameSummary, 'ghost_bbox_count'))}</span>
      </div>
      <div className='chips'>
        <span>Tracklets clean: {formatCompact(valueOf(trackletSummary, 'clean_tracklets'))}</span>
        <span>Tracklets rejected: {formatCompact(valueOf(trackletSummary, 'rejected_tracklets'))}</span>
        <span>Suspicious events: {formatCompact(valueOf(trackletSummary, 'suspicious_events'))}</span>
        <span>Team over-cap frames: {formatCompact(overCapFrames)}</span>
        <span>Blocked switches: {globalIdentityReport?.blocked_switches?.length ?? 'n/a'}</span>
        <span>Rejected starts: {globalIdentityReport?.rejected_start_candidates?.length ?? 'n/a'}</span>
      </div>
      {statsSummary && (
        <div className='chips'>
          <span>Player stats: {formatCompact(valueOf(statsSummary, 'players'))}</span>
          <span>Total dist: {formatNumber(numberValue(statsSummary, 'total_distance_m'))} m</span>
          <span>Estimated dist: {formatNumber(numberValue(statsSummary, 'estimated_short_gap_distance_m'))} m</span>
          <span>Peak sustained: {formatNumber(peakSpeedValue(statsSummary))} km/h</span>
          <span>Low quality: {formatCompact(valueOf(statsSummary, 'players_low_quality'))}</span>
          <span>Scope: {playerStats?.scope || 'n/a'}</span>
        </div>
      )}
      {identitySummary && (
        <div className='chips'>
          <span>Detected frames: {formatCompact(valueOf(identitySummary, 'detected_frames'))}</span>
          <span>Missing frames: {formatCompact(valueOf(identitySummary, 'missing_frames'))}</span>
          <span>Ambiguous slots: {formatCompact(valueOf(identitySummary, 'ambiguous_frames'))}</span>
          <span>Identity blocks: {formatCompact(valueOf(identitySummary, 'blocked_identity_switches'))}</span>
        </div>
      )}
    </>
  );
}

function Diagnostics({
  frameCounts,
  trackingQuality,
  globalIdentityReport,
}: {
  frameCounts?: FrameDetectionCountsDocument;
  trackingQuality?: TrackingQualityReport;
  globalIdentityReport?: GlobalIdentityReport;
}) {
  const lowFrames = visibleFrameRows(frameCounts);
  const suspicious = (trackingQuality?.suspicious_events || []).slice(0, 20);
  const blocked = (globalIdentityReport?.blocked_switches || []).slice(0, 20);
  const overCap = teamOverCapRows(trackingQuality);

  return (
    <div className='grid three compact'>
      <div className='quality-box'>
        <h3>Low visible count</h3>
        <div className='quality-list compact-list'>
          {lowFrames.length === 0 && <span className='muted'>Brak niskich klatek.</span>}
          {lowFrames.map((frame) => (
            <span key={`${valueOf(frame, 'frame')}-${valueOf(frame, 'time_sec')}`}>
              {eventFrame(frame)}: visible {textValue(frame, 'visible_stable_boxes')} / raw{' '}
              {textValue(frame, 'raw_detections')} / missing {textValue(frame, 'slot_missing')} / amb{' '}
              {textValue(frame, 'slot_ambiguous')}
            </span>
          ))}
        </div>
      </div>
      <div className='quality-box'>
        <h3>Suspicious tracklets</h3>
        <div className='quality-list compact-list'>
          {suspicious.length === 0 && <span className='muted'>Brak zdarzen.</span>}
          {suspicious.map((event, index) => (
            <span key={`${textValue(event, 'type')}-${index}`}>
              {textValue(event, 'type')} {eventFrame(event)} tracklet {textValue(event, 'tracklet_id')}
            </span>
          ))}
        </div>
      </div>
      <div className='quality-box'>
        <h3>Blocked switches</h3>
        <div className='quality-list compact-list'>
          {blocked.length === 0 && <span className='muted'>Brak blokad.</span>}
          {blocked.map((event, index) => (
            <span key={`${textValue(event, 'slot_id')}-${index}`}>
              {textValue(event, 'slot_id')} {eventFrame(event)} {textValue(event, 'reason')}
            </span>
          ))}
        </div>
      </div>
      <div className='quality-box'>
        <h3>Team over-cap</h3>
        <div className='quality-list compact-list'>
          {overCap.length === 0 && <span className='muted'>Brak przekroczen limitu 7/team.</span>}
          {overCap.map((frame) => (
            <span key={`${valueOf(frame, 'frame')}-${valueOf(frame, 'time_sec')}`}>
              {eventFrame(frame)}: active {textValue(frame, 'active_tracklets')} / teams {teamCountsLabel(frame)}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

export function AnalysisQualityPanel({ match }: AnalysisQualityPanelProps) {
  const frameCounts = match.frame_detection_counts;
  const trackingQuality = match.tracking_quality_report;
  const globalIdentityReport = match.global_identity_report;
  const playerStats = match.player_stats;
  const teamStats = match.team_stats;

  if (match.analysis_report?.status !== 'completed') {
    return null;
  }

  return (
    <section className='card'>
      <div className='row between'>
        <div>
          <h2>Jakosc analizy i runy</h2>
          <p className='muted'>
            Latest: {match.latest_analysis_run_id || match.analysis_report.run_id || 'n/a'}
          </p>
        </div>
        <div className='row'>
          {match.analysis_report.run_manifest && (
            <a href={artifactUrl(match.id, match.analysis_report.run_manifest)}>
              Latest run metadata
            </a>
          )}
          {match.analysis_report.artifacts?.player_stats && (
            <a href={artifactUrl(match.id, match.analysis_report.artifacts.player_stats)}>
              player_stats.json
            </a>
          )}
        </div>
      </div>

      <QualitySummary
        frameCounts={frameCounts}
        trackingQuality={trackingQuality}
        globalIdentityReport={globalIdentityReport}
        playerStats={playerStats}
      />

      {frameCounts?.frames && frameCounts.frames.length > 0 && (
        <div className='quality-chart'>
          <span className='muted'>Visible stable boxes per frame</span>
          <Sparkline frames={frameCounts.frames} />
        </div>
      )}

      <Diagnostics
        frameCounts={frameCounts}
        trackingQuality={trackingQuality}
        globalIdentityReport={globalIdentityReport}
      />

      {teamStats?.teams && teamStats.teams.length > 0 && (
        <div className='grid two compact'>
          {teamStats.teams.map((team) => (
            <div className='quality-box' key={String(team.team_label)}>
              <h3>{String(team.team_name || `Team ${team.team_label}`)}</h3>
              <div className='chips'>
                <span>Players: {formatCompact(team.players)}</span>
                <span>Distance: {formatCompact(team.total_distance_m)} m</span>
                <span>Observed: {formatCompact(team.observed_distance_m)} m</span>
                <span>Estimated: {formatCompact(team.estimated_short_gap_distance_m)} m</span>
                <span>
                  Peak sustained: {formatCompact(team.peak_sustained_speed_kmh ?? team.top_speed_kmh)} km/h
                </span>
                <span>Locked: {team.locked ? 'yes' : 'no'}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      <details className='debug-details'>
        <summary>Analysis runs</summary>
        <RunList match={match} />
      </details>
    </section>
  );
}
