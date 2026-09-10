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
      return <article className='suggested-key-moment-card' key={candidate.candidate_id}>
        <div className='row between'><strong>{formatKeyMomentTime(candidate.start_time_sec)}–{formatKeyMomentTime(candidate.end_time_sec)}</strong><span>{candidate.team_id ? teams.get(candidate.team_id) || candidate.team_id : '—'}</span></div>
        <p className='muted'>Sygnały: {evidence(candidate)}</p>
        {overlap ? <p className='suggestion-overlap'>⚠ {overlap.kind === 'duplicate' ? 'Możliwy duplikat' : overlap.kind === 'extension' ? 'Możliwe rozszerzenie istniejącego momentu' : 'Nachodzi na istniejący Key Moment'}: {overlap.headline || formatKeyMomentTime(overlap.start_time_sec)}</p> : null}
        <p className='muted'>Wynik {candidate.interestingness_score?.toFixed(2) ?? '—'} · pewność {candidate.confidence?.toFixed(2) ?? '—'}</p>
        <div className='row end'><button type='button' disabled={!onPlayAt} onClick={() => onPlayAt?.(candidate.start_time_sec)}>Odtwórz</button><button type='button' disabled={disabled || !onAccept} onClick={() => void onAccept?.(candidate)}>Akceptuj</button><button type='button' className='secondary' disabled={disabled || !onReject} onClick={() => void onReject?.(candidate)}>Odrzuć</button></div>
      </article>;
    })}
  </section>;
}
