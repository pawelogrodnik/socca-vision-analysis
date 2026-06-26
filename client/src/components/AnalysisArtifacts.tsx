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
  const movementStats = report?.artifacts?.movement_stats;

  return (
    <section className='card'>
      <h2>Widok analizy</h2>
      <div className='grid two'>
        <div>
          <h3>Artefakty lokalne</h3>
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
          {heatmap && (
            <img
              src={artifactUrl(match.id, heatmap)}
              className='heatmap'
              alt='Heatmap'
            />
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
          {debugIdentityOverlay && (
            <details className='debug-details'>
              <summary>Debug identity overlay</summary>
              <video
                controls
                src={artifactUrl(match.id, debugIdentityOverlay)}
                className='video'
              />
            </details>
          )}
          {rawOverlay && (
            <details className='debug-details'>
              <summary>Raw tracker overlay debug</summary>
              <video
                controls
                src={artifactUrl(match.id, rawOverlay)}
                className='video'
              />
            </details>
          )}
        </div>
        <div>
          <h3>Analysis report</h3>
          <pre>{pretty(report || { status: 'not analyzed' })}</pre>
        </div>
      </div>
    </section>
  );
}
