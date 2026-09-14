import type { ShotReviewSuggestion, ShotReviewSuggestionCluster } from '../types';
import { formatKeyMomentTime } from '../lib/keyMomentTime';

type Props = {
  clusters: ShotReviewSuggestionCluster[];
  unavailable?: boolean;
  disabled?: boolean;
  onPlayAt: (timeSec: number) => void;
  onAccept: (cluster: ShotReviewSuggestionCluster) => void;
  onReject: (cluster: ShotReviewSuggestionCluster) => void;
};

export function shotSuggestionTime(suggestion: ShotReviewSuggestion): number {
  return suggestion.time_sec ?? suggestion.logical_timestamp_sec ?? suggestion.candidate_timestamp_sec ?? 0;
}

function suggestedTeamName(suggestion: ShotReviewSuggestion): string {
  const label = suggestion.suggested_team_name || suggestion.suggested_team_label;
  if (label) return label;
  return 'Nieprzypisana drużyna';
}

export function shotClusterPreferredTime(cluster: ShotReviewSuggestionCluster): number {
  return shotSuggestionTime(cluster.preferred_candidate);
}

function clusterTimes(cluster: ShotReviewSuggestionCluster): string {
  return `${formatKeyMomentTime(cluster.review_start_time_sec)}–${formatKeyMomentTime(cluster.review_end_time_sec)}`;
}

export function SuggestedShots({ clusters, unavailable = false, disabled = false, onPlayAt, onAccept, onReject }: Props) {
  if (unavailable) return <section className='key-moment-suggestions'><p className='muted'>Sugestie strzałów nie są teraz dostępne.</p></section>;
  return <section className='key-moment-suggestions' aria-label='Sugerowane strzały'>
    <h3>Potencjalne akcje ({clusters.length})</h3>
    {!clusters.length ? <p className='muted'>Brak nieprzejrzanych potencjalnych akcji.</p> : clusters.map((cluster) => <article className='redesign-moment-row suggested-key-moment-card' key={cluster.cluster_id}>
      <time>{clusterTimes(cluster)}</time>
      <div>
        <h3>{cluster.member_count} {cluster.member_count === 1 ? 'sygnał systemu' : 'sygnały systemu'}</h3>
        <p>{suggestedTeamName(cluster.preferred_candidate)} · {cluster.member_candidates.map((candidate) => formatKeyMomentTime(shotSuggestionTime(candidate))).join(', ')}</p>
      </div>
      <div className='key-moment-action-list'>
        <button type='button' className='key-moment-action-button' aria-label='Odtwórz' title='Odtwórz' onClick={() => onPlayAt(cluster.review_start_time_sec)}><span aria-hidden='true'>▶</span></button>
        <button type='button' className='key-moment-action-button accept' aria-label='Akceptuj' title='Akceptuj' disabled={disabled} onClick={() => onAccept(cluster)}><span aria-hidden='true'>✓</span></button>
        <button type='button' className='key-moment-action-button reject' aria-label='Odrzuć' title='Odrzuć' disabled={disabled} onClick={() => onReject(cluster)}><span aria-hidden='true'>×</span></button>
      </div>
    </article>)}
  </section>;
}
