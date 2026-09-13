import type { ShotReviewSuggestion } from '../types';
import { formatKeyMomentTime } from '../lib/keyMomentTime';

type Props = {
  suggestions: ShotReviewSuggestion[];
  unavailable?: boolean;
  disabled?: boolean;
  onPlayAt: (timeSec: number) => void;
  onAccept: (suggestion: ShotReviewSuggestion) => void;
  onReject: (suggestion: ShotReviewSuggestion) => void;
};

export function shotSuggestionTime(suggestion: ShotReviewSuggestion): number {
  return suggestion.time_sec ?? suggestion.logical_timestamp_sec ?? suggestion.candidate_timestamp_sec ?? 0;
}

function suggestedTeamName(suggestion: ShotReviewSuggestion): string {
  const label = suggestion.suggested_team_name || suggestion.suggested_team_label;
  if (label) return label;
  return 'Nieprzypisana drużyna';
}

export function SuggestedShots({ suggestions, unavailable = false, disabled = false, onPlayAt, onAccept, onReject }: Props) {
  if (unavailable) return <section className='key-moment-suggestions'><p className='muted'>Sugestie strzałów nie są teraz dostępne.</p></section>;
  return <section className='key-moment-suggestions' aria-label='Sugerowane strzały'>
    <h3>Sugerowane strzały ({suggestions.length})</h3>
    {!suggestions.length ? <p className='muted'>Brak nieprzejrzanych sugestii.</p> : suggestions.map((suggestion) => <article className='redesign-moment-row suggested-key-moment-card' key={suggestion.candidate_id}>
      <time>{formatKeyMomentTime(shotSuggestionTime(suggestion))}</time>
      <div>
        <h3>{suggestedTeamName(suggestion)}</h3>
        {typeof suggestion.confidence === 'number' ? <p>Pewność sugestii: {suggestion.confidence.toFixed(2)}</p> : <p>Wymaga oceny operatora</p>}
      </div>
      <div className='key-moment-action-list'>
        <button type='button' className='key-moment-action-button' aria-label='Odtwórz' title='Odtwórz' onClick={() => onPlayAt(shotSuggestionTime(suggestion))}><span aria-hidden='true'>▶</span></button>
        <button type='button' className='key-moment-action-button accept' aria-label='Akceptuj' title='Akceptuj' disabled={disabled} onClick={() => onAccept(suggestion)}><span aria-hidden='true'>✓</span></button>
        <button type='button' className='key-moment-action-button reject' aria-label='Odrzuć' title='Odrzuć' disabled={disabled} onClick={() => onReject(suggestion)}><span aria-hidden='true'>×</span></button>
      </div>
    </article>)}
  </section>;
}
