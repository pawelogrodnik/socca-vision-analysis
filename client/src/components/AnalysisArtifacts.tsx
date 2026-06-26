import type { Match } from '../types';
import { artifactUrl } from '../api';
import { pretty } from '../lib/helpers';

interface AnalysisArtifactsProps {
  match: Match;
}

export function AnalysisArtifacts({ match }: AnalysisArtifactsProps) {
  const report = match.analysis_report;
  const heatmap = report?.artifacts?.heatmap_all_tracks;
  const stableOverlay = report?.artifacts?.stable_overlay_preview;
  const debugIdentityOverlay = report?.artifacts?.debug_identity_overlay;
  const rawOverlay = report?.artifacts?.overlay_preview;
  const frameDetectionCounts = report?.artifacts?.frame_detection_counts;
  const globalIdentity = report?.artifacts?.global_identity;
  const globalIdentityReport = report?.artifacts?.global_identity_report;
  const teamConfig = report?.artifacts?.team_config;
  const teamStats = report?.artifacts?.team_stats;
  const movementStats = report?.artifacts?.movement_stats;
  const playerStats = report?.artifacts?.player_stats;
  const resolvedPlayerStats = report?.artifacts?.resolved_player_stats || (match.resolved_player_stats ? 'resolved_player_stats.json' : undefined);
  const playerHeatmaps = report?.artifacts?.player_heatmaps;
  const tracklets = report?.artifacts?.tracklets;
  const trackingQualityReport = report?.artifacts?.tracking_quality_report;

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
