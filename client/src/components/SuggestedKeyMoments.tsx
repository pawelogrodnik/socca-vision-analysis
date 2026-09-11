import type { KeyMomentSuggestionsState, PublicMatchReport, SuggestedKeyMomentCandidate } from '../types';
import { formatKeyMomentTime } from '../lib/keyMomentTime';

type Props = {
  state?: KeyMomentSuggestionsState;
  report: PublicMatchReport;
  disabled?: boolean;
  onPlayAt?: (timeSec: number) => void;
  onAccept?: (candidate: SuggestedKeyMomentCandidate) => void | Promise<void>;
  onReject?: (candidate: SuggestedKeyMomentCandidate) => Promise<void>;
};

function evidence(candidate: SuggestedKeyMomentCandidate): string {
  return (candidate.evidence || []).map((row) => String(row.kind || '')).filter(Boolean).join(' · ') || 'brak dodatkowych sygnałów';
}

export function SuggestedKeyMoments({ state, report, disabled, onPlayAt, onAccept, onReject }: Props) {
  const candidates = state?.candidates || [];
  const teams = new Map(report.teams.map((team) => [team.team_id, team.team_name || team.team_label || team.team_id]));
  if (state?.status === 'not_available') return <section className='key-moment-suggestions'><p className='muted'>Sugestie nie są teraz dostępne.</p></section>;
  return <section className='key-moment-suggestions' aria-label='Sugerowane Key Moments'>
    <h3>Sugerowane Key Moments ({state?.unreviewed_count ?? candidates.length})</h3>
    {!candidates.length ? <p className='muted'>Brak nieprzejrzanych sugestii.</p> : candidates.map((candidate) => {
      const overlap = state?.overlaps?.[candidate.candidate_id];
      const teamName = candidate.team_id ? teams.get(candidate.team_id) || candidate.team_id : 'Nieprzypisana drużyna';
      return <article className='redesign-moment-row suggested-key-moment-card' key={candidate.candidate_id}>
        <time className='suggested-key-moment-range'>
          <span>{formatKeyMomentTime(candidate.start_time_sec)}</span>
          <span aria-hidden='true'>–</span>
          <span>{formatKeyMomentTime(candidate.end_time_sec)}</span>
        </time>
        <div>
          <h3>{teamName}</h3>
          <p>Sygnały: {evidence(candidate)}</p>
          {overlap ? <p className='suggestion-overlap'>⚠ {overlap.kind === 'duplicate' ? 'Możliwy duplikat' : overlap.kind === 'extension' ? 'Możliwe rozszerzenie istniejącego momentu' : 'Nachodzi na istniejący Key Moment'}: {overlap.headline || formatKeyMomentTime(overlap.start_time_sec)}</p> : null}
          <p>Wynik {candidate.interestingness_score?.toFixed(2) ?? '—'} · pewność {candidate.confidence?.toFixed(2) ?? '—'}</p>
        </div>
        <div className='key-moment-action-list'>
          <button type='button' className='key-moment-action-button' aria-label='Odtwórz' title='Odtwórz' disabled={!onPlayAt} onClick={() => onPlayAt?.(candidate.start_time_sec)}><span aria-hidden='true'>▶</span></button>
          <button type='button' className='key-moment-action-button accept' aria-label='Akceptuj' title='Akceptuj' disabled={disabled || !onAccept} onClick={() => void onAccept?.(candidate)}><span aria-hidden='true'>✓</span></button>
          <button type='button' className='key-moment-action-button reject' aria-label='Odrzuć' title='Odrzuć' disabled={disabled || !onReject} onClick={() => void onReject?.(candidate)}><span aria-hidden='true'>×</span></button>
        </div>
      </article>;
    })}
  </section>;
}
