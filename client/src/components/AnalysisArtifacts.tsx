import type { Match } from '../types';
import { artifactUrl } from '../api';
import { pretty } from '../lib/helpers';
import { ChangeCandidatesReview } from './ChangeCandidatesReview';
import { ContactCandidatesReview } from './ContactCandidatesReview';
import { MatchPhaseConfigPanel } from './MatchPhaseConfigPanel';
import { PassCandidatesReview } from './PassCandidatesReview';

interface AnalysisArtifactsProps {
  match: Match;
}

export function AnalysisArtifacts({ match }: AnalysisArtifactsProps) {
  const report = match.analysis_report;
  const heatmap = report?.artifacts?.heatmap_all_tracks;
  const stableOverlay = report?.artifacts?.stable_overlay_preview;
  const analysisChunkManifest = report?.artifacts?.analysis_chunk_manifest || (match.analysis_chunk_manifest ? 'analysis_chunk_manifest.json' : undefined);
  const debugIdentityOverlay = report?.artifacts?.debug_identity_overlay;
  const rawOverlay = report?.artifacts?.overlay_preview;
  const frameDetectionCounts = report?.artifacts?.frame_detection_counts;
  const globalIdentity = report?.artifacts?.global_identity;
  const globalIdentityReport = report?.artifacts?.global_identity_report;
  const analysisQualityReport = report?.artifacts?.analysis_quality_report || (match.analysis_quality_report ? 'analysis_quality_report.json' : undefined);
  const teamConfig = report?.artifacts?.team_config;
  const teamStats = report?.artifacts?.team_stats;
  const movementStats = report?.artifacts?.movement_stats;
  const playerStats = report?.artifacts?.player_stats;
  const resolvedPlayerStats = report?.artifacts?.resolved_player_stats || (match.resolved_player_stats ? 'resolved_player_stats.json' : undefined);
  const playerHeatmaps = report?.artifacts?.player_heatmaps;
  const changeCandidates = report?.artifacts?.change_candidates || (match.change_candidates ? 'change_candidates.json' : undefined);
  const changeReviewReport = report?.artifacts?.change_review_report || (match.change_review_report ? 'change_review_report.json' : undefined);
  const tracklets = report?.artifacts?.tracklets;
  const trackingQualityReport = report?.artifacts?.tracking_quality_report;
  const ballOverlay = report?.artifacts?.ball_overlay_preview;
  const ballCandidates = report?.artifacts?.ball_candidates;
  const ballTracks = report?.artifacts?.ball_tracks;
  const ballAnalysisReport = report?.artifacts?.ball_analysis_report;
  const ballTrackingReport = report?.artifacts?.ball_tracking_report;
  const ballQualityReport = report?.artifacts?.ball_quality_report;
  const possessionOverlay = report?.artifacts?.possession_overlay_preview;
  const possessionCandidates = report?.artifacts?.possession_candidates;
  const possessionSegments = report?.artifacts?.possession_segments;
  const contactCandidates = report?.artifacts?.contact_candidates;
  const matchPhaseConfig = report?.artifacts?.match_phase_config || (match.match_phase_config ? 'match_phase_config.json' : undefined);
  const eventCandidates = report?.artifacts?.event_candidates || (match.event_candidates ? 'event_candidates.json' : undefined);
  const eventReviewReport = report?.artifacts?.event_review_report || (match.event_review_report ? 'event_review_report.json' : undefined);
  const passCandidates = report?.artifacts?.pass_candidates || (match.pass_candidates ? 'pass_candidates.json' : undefined);
  const passReviewReport = report?.artifacts?.pass_review_report || (match.pass_review_report ? 'pass_review_report.json' : undefined);
  const possessionReport = report?.artifacts?.possession_report;
  const ballSummary = match.ball_tracking_report?.summary;
  const ballQuality = match.ball_quality_report;
  const possessionSummary = match.possession_report?.summary;
  const eventSummary = match.event_review_report?.summary || match.event_candidates?.summary;
  const passSummary = match.pass_review_report?.summary || match.pass_candidates?.summary;

  return (
    <section className='card'>
      <h2>Widok analizy</h2>
      <div className='grid two'>
        <div>
          <h3>Artefakty lokalne</h3>
          {report?.run_id && (
            <p className='muted'>
              Analysis run: {report.run_id}
            </p>
          )}
          {report?.run_manifest && (
            <a href={artifactUrl(match.id, report.run_manifest)}>
              Pobierz run_metadata.json
            </a>
          )}
          {stableOverlay && (
            <video
              controls
              src={artifactUrl(match.id, stableOverlay)}
              className='video'
            />
          )}
          {!stableOverlay && rawOverlay && (
            <p className='muted'>
              Brak stable overlay. Uruchom ponownie analize, zeby wygenerowac
              stabilne ID zawodnikow.
            </p>
          )}
          {match.match_package && (
            <a href={artifactUrl(match.id, 'match_package.json')}>
              Pobierz match_package.json
            </a>
          )}
          {analysisChunkManifest && (
            <a href={artifactUrl(match.id, analysisChunkManifest)}>
              Pobierz analysis_chunk_manifest.json
            </a>
          )}
          {match.player_assignments && (
            <a href={artifactUrl(match.id, 'player_assignments.json')}>
              Pobierz player_assignments.json
            </a>
          )}
          {match.stable_players && (
            <a href={artifactUrl(match.id, 'stable_players.json')}>
              Pobierz stable_players.json
            </a>
          )}
          {globalIdentity && (
            <a href={artifactUrl(match.id, globalIdentity)}>
              Pobierz global_identity.json
            </a>
          )}
          {globalIdentityReport && (
            <a href={artifactUrl(match.id, globalIdentityReport)}>
              Pobierz global_identity_report.json
            </a>
          )}
          {analysisQualityReport && (
            <a href={artifactUrl(match.id, analysisQualityReport)}>
              Pobierz analysis_quality_report.json
            </a>
          )}
          {frameDetectionCounts && (
            <a href={artifactUrl(match.id, frameDetectionCounts)}>
              Pobierz frame_detection_counts.json
            </a>
          )}
          {movementStats && (
            <a href={artifactUrl(match.id, movementStats)}>
              Pobierz movement_stats.json
            </a>
          )}
          {teamConfig && (
            <a href={artifactUrl(match.id, teamConfig)}>
              Pobierz team_config.json
            </a>
          )}
          {teamStats && (
            <a href={artifactUrl(match.id, teamStats)}>
              Pobierz team_stats.json
            </a>
          )}
          {playerStats && (
            <a href={artifactUrl(match.id, playerStats)}>
              Pobierz player_stats.json
            </a>
          )}
          {resolvedPlayerStats && (
            <a href={artifactUrl(match.id, resolvedPlayerStats)}>
              Pobierz resolved_player_stats.json
            </a>
          )}
          {playerHeatmaps && (
            <a href={artifactUrl(match.id, playerHeatmaps)}>
              Pobierz player_heatmaps.json
            </a>
          )}
          {changeCandidates && (
            <a href={artifactUrl(match.id, changeCandidates)}>
              Pobierz change_candidates.json
            </a>
          )}
          {changeReviewReport && (
            <a href={artifactUrl(match.id, changeReviewReport)}>
              Pobierz change_review_report.json
            </a>
          )}
          {tracklets && (
            <a href={artifactUrl(match.id, tracklets)}>
              Pobierz tracklets.json
            </a>
          )}
          {trackingQualityReport && (
            <a href={artifactUrl(match.id, trackingQualityReport)}>
              Pobierz tracking_quality_report.json
            </a>
          )}
          {(ballOverlay || ballSummary || ballTrackingReport) && (
            <div className='artifact-box'>
              <h3>Ball tracking (experimental)</h3>
              {ballOverlay && (
                <video
                  controls
                  src={artifactUrl(match.id, ballOverlay)}
                  className='video'
                />
              )}
              {possessionOverlay && (
                <>
                  <h4>Possession candidates (experimental)</h4>
                  <video
                    controls
                    src={artifactUrl(match.id, possessionOverlay)}
                    className='video'
                  />
                </>
              )}
              {ballSummary && (
                <div className='chips'>
                  <span>Detected: {formatPercent(ballSummary.detected_coverage)}</span>
                  <span>Interpolated: {formatPercent(ballSummary.interpolated_coverage)}</span>
                  <span>Known: {formatPercent(ballSummary.known_coverage)}</span>
                  <span>Candidates: {formatCount(ballSummary.candidate_count)}</span>
                  <span>Rejected: {formatCount(ballSummary.rejected_candidate_count)}</span>
                </div>
              )}
              {ballQuality?.recommendation && (
                <div className='quality-alert'>
                  <strong>
                    Decision: {ballQuality.recommendation.decision || 'n/a'}
                  </strong>
                  <span>
                    Custom dataset:{' '}
                    {ballQuality.recommendation.custom_dataset_recommended
                      ? 'recommended'
                      : 'not yet'}
                  </span>
                  {ballQuality.recommendation.next_step && (
                    <span>{ballQuality.recommendation.next_step}</span>
                  )}
                  {(ballQuality.recommendation.reasons || []).map((reason) => (
                    <span key={reason}>{reason}</span>
                  ))}
                </div>
              )}
              {ballQuality?.summary && (
                <div className='chips'>
                  <span>
                    Candidate frames:{' '}
                    {formatPercent(ballQuality.summary.candidate_frame_ratio)}
                  </span>
                  <span>
                    Multi candidates:{' '}
                    {formatPercent(ballQuality.summary.multi_candidate_ratio)}
                  </span>
                  <span>
                    Unknown longest:{' '}
                    {formatCount(ballQuality.summary.longest_unknown_streak_frames)}f
                  </span>
                  <span>
                    Unknown:{' '}
                    {formatPercent(ballQuality.summary.unknown_coverage)}
                  </span>
                </div>
              )}
              {possessionSummary && (
                <div className='chips'>
                  <span>
                    Controlled:{' '}
                    {formatPercent(possessionSummary.controlled_coverage)}
                  </span>
                  <span>
                    Contested:{' '}
                    {formatPercent(possessionSummary.contested_coverage)}
                  </span>
                  <span>
                    Free: {formatPercent(possessionSummary.free_coverage)}
                  </span>
                  <span>
                    Unknown:{' '}
                    {formatPercent(possessionSummary.unknown_coverage)}
                  </span>
                  <span>
                    Contacts:{' '}
                    {formatCount(possessionSummary.contact_candidates)}
                  </span>
                  <span>
                    Player interp:{' '}
                    {formatCount(possessionSummary.interpolated_player_position_frames)}
                  </span>
                </div>
              )}
              {eventSummary && (
                <div className='quality-alert'>
                  <strong>Reviewed ball contacts</strong>
                  <span>
                    Events: {formatCount(eventSummary.events_total)}
                    {' '}accepted: {formatCount(eventSummary.accepted_events)}
                    {' '}review: {formatCount(eventSummary.review_required_events)}
                    {' '}rejected contacts: {formatCount(eventSummary.rejected_contacts)}
                  </span>
                  <span>
                    Only accepted events are eligible for final downstream stats.
                  </span>
                </div>
              )}
              {passSummary && (
                <div className='quality-alert'>
                  <strong>Pass candidates</strong>
                  <span>
                    Candidates: {formatCount(passSummary.pass_candidates)}
                    {' '}same team: {formatCount(passSummary.same_team_pass_candidates)}
                    {' '}turnover/interception: {formatCount(passSummary.turnover_or_interception_candidates)}
                    {' '}with positions: {formatCount(passSummary.candidates_with_positions)}
                    {' '}forward: {formatCount(passSummary.forward_pass_candidates)}
                    {' '}progressive: {formatCount(passSummary.progressive_pass_candidates)}
                    {' '}final: {formatCount(passSummary.final_stat_passes)}
                  </span>
                  <span>
                    Experimental candidate layer only. Final pass stats are still disabled.
                  </span>
                </div>
              )}
              <MatchPhaseConfigPanel match={match} enabled={Boolean(ballSummary || possessionSummary || passSummary)} />
              {match.possession_report?.warnings?.length ? (
                <p className='muted'>
                  {match.possession_report.warnings[0]}
                </p>
              ) : null}
              {match.ball_tracking_report?.warnings?.length ? (
                <p className='muted'>
                  {match.ball_tracking_report.warnings[0]}
                </p>
              ) : null}
              <div className='row'>
                {ballTracks && (
                  <a href={artifactUrl(match.id, ballTracks)}>
                    Pobierz ball_tracks.json
                  </a>
                )}
                {ballTrackingReport && (
                  <a href={artifactUrl(match.id, ballTrackingReport)}>
                    Pobierz ball_tracking_report.json
                  </a>
                )}
                {ballAnalysisReport && (
                  <a href={artifactUrl(match.id, ballAnalysisReport)}>
                    Pobierz ball_analysis_report.json
                  </a>
                )}
                {ballQualityReport && (
                  <a href={artifactUrl(match.id, ballQualityReport)}>
                    Pobierz ball_quality_report.json
                  </a>
                )}
                {ballCandidates && (
                  <a href={artifactUrl(match.id, ballCandidates)}>
                    Pobierz ball_candidates.json
                  </a>
                )}
                {possessionReport && (
                  <a href={artifactUrl(match.id, possessionReport)}>
                    Pobierz possession_report.json
                  </a>
                )}
                {possessionCandidates && (
                  <a href={artifactUrl(match.id, possessionCandidates)}>
                    Pobierz possession_candidates.json
                  </a>
                )}
                {possessionSegments && (
                  <a href={artifactUrl(match.id, possessionSegments)}>
                    Pobierz possession_segments.json
                  </a>
                )}
                {contactCandidates && (
                  <a href={artifactUrl(match.id, contactCandidates)}>
                    Pobierz contact_candidates.json
                  </a>
                )}
                {matchPhaseConfig && (
                  <a href={artifactUrl(match.id, matchPhaseConfig)}>
                    Pobierz match_phase_config.json
                  </a>
                )}
                {eventCandidates && (
                  <a href={artifactUrl(match.id, eventCandidates)}>
                    Pobierz event_candidates.json
                  </a>
                )}
                {eventReviewReport && (
                  <a href={artifactUrl(match.id, eventReviewReport)}>
                    Pobierz event_review_report.json
                  </a>
                )}
                {passCandidates && (
                  <a href={artifactUrl(match.id, passCandidates)}>
                    Pobierz pass_candidates.json
                  </a>
                )}
                {passReviewReport && (
                  <a href={artifactUrl(match.id, passReviewReport)}>
                    Pobierz pass_review_report.json
                  </a>
                )}
              </div>
              <ContactCandidatesReview match={match} enabled={Boolean(contactCandidates)} />
              <PassCandidatesReview match={match} enabled={Boolean(passCandidates)} />
            </div>
          )}
          <ChangeCandidatesReview match={match} enabled={Boolean(match.stable_players || changeCandidates)} />
          {(heatmap || debugIdentityOverlay || rawOverlay || report) && (
            <details className='debug-details'>
              <summary>Developer debug artifacts</summary>
              {heatmap && (
                <img
                  src={artifactUrl(match.id, heatmap)}
                  className='heatmap'
                  alt='Raw all-tracks heatmap'
                />
              )}
              {debugIdentityOverlay && (
                <video
                  controls
                  src={artifactUrl(match.id, debugIdentityOverlay)}
                  className='video'
                />
              )}
              {rawOverlay && (
                <video
                  controls
                  src={artifactUrl(match.id, rawOverlay)}
                  className='video'
                />
              )}
              <pre>{pretty(report || { status: 'not analyzed' })}</pre>
            </details>
          )}
        </div>
      </div>
    </section>
  );
}

function formatPercent(value: unknown): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return '--';
  }
  return `${(numeric * 100).toFixed(1)}%`;
}

function formatCount(value: unknown): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return '--';
  }
  return String(Math.round(numeric));
}
