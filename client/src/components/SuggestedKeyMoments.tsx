import { useState } from 'react';
import type { KeyMomentEditorialMoment, KeyMomentSuggestionsState, PublicMatchReport, SuggestedKeyMomentCandidate } from '../types';

type Props = {
  state?: KeyMomentSuggestionsState;
  report: PublicMatchReport;
  disabled?: boolean;
  onAccept?: (candidate: SuggestedKeyMomentCandidate, moment: KeyMomentEditorialMoment) => Promise<void>;
  onReject?: (candidate: SuggestedKeyMomentCandidate) => Promise<void>;
};

const categories = [
  ['goal_for_us', 'Gol dla nas'], ['goal_for_opponent', 'Gol dla rywali'], ['chance', 'Okazja'],
  ['good_action', 'Dobra akcja'], ['mistake', 'Błąd'], ['goalkeeper_intervention', 'Interwencja bramkarza'],
  ['defensive_action', 'Akcja defensywna'], ['tactical_note', 'Notatka taktyczna'], ['other', 'Inne'],
];

function time(value: number): string {
  return `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, '0')}`;
}

function evidence(candidate: SuggestedKeyMomentCandidate): string {
  return (candidate.evidence || []).map((row) => String(row.kind || '')).filter(Boolean).join(' · ') || 'brak dodatkowych sygnałów';
}

function initialDraft(candidate: SuggestedKeyMomentCandidate): KeyMomentEditorialMoment {
  return { time_sec: candidate.peak_time_sec ?? candidate.start_time_sec, team_id: candidate.team_id ?? null, category: 'other', headline: 'Moment do weryfikacji', note: '', origin: 'manual' };
}

export function SuggestedKeyMoments({ state, report, disabled, onAccept, onReject }: Props) {
  const [drafts, setDrafts] = useState<Record<string, KeyMomentEditorialMoment>>({});
  const [busy, setBusy] = useState('');
  const candidates = state?.candidates || [];
  if (state?.status === 'not_available') return <section className='key-moment-suggestions'><h3>Sugerowane Key Moments</h3><p className='muted'>Sugestie nie są teraz dostępne. Edycja własnych momentów działa normalnie.</p></section>;
  return <section className='key-moment-suggestions' aria-labelledby='suggested-key-moments-title'>
    <h3 id='suggested-key-moments-title'>Sugerowane Key Moments ({state?.unreviewed_count ?? candidates.length})</h3>
    {!candidates.length ? <p className='muted'>Brak nieprzejrzanych sugestii.</p> : candidates.map((candidate) => {
      const draft = drafts[candidate.candidate_id] || initialDraft(candidate);
      const overlap = state?.overlaps?.[candidate.candidate_id];
      const update = (patch: Partial<KeyMomentEditorialMoment>) => setDrafts((rows) => ({ ...rows, [candidate.candidate_id]: { ...draft, ...patch } }));
      return <article className='suggested-key-moment-card' key={candidate.candidate_id}>
        <div className='row between'><strong>{time(candidate.start_time_sec)}–{time(candidate.end_time_sec)}</strong><span className='muted'>wynik {candidate.interestingness_score?.toFixed(2) ?? '—'} · pewność {candidate.confidence?.toFixed(2) ?? '—'}</span></div>
        <p className='muted'>Sygnały: {evidence(candidate)}</p>
        {overlap ? <p className='suggestion-overlap'>⚠ {overlap.kind === 'duplicate' ? 'Możliwy duplikat' : overlap.kind === 'extension' ? 'Możliwe rozszerzenie istniejącego momentu' : 'Nachodzi na istniejący Key Moment'}: {overlap.headline || time(overlap.start_time_sec)}</p> : null}
        <div className='suggested-key-moment-form'>
          <label>Czas <input value={String(draft.time_sec)} onChange={(event) => update({ time_sec: Number(event.target.value) })} /></label>
          <label>Tytuł <input value={draft.headline} onChange={(event) => update({ headline: event.target.value })} /></label>
          <label>Kategoria <select value={draft.category} onChange={(event) => update({ category: event.target.value })}>{categories.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label>Drużyna <select value={draft.team_id || ''} onChange={(event) => update({ team_id: event.target.value || null })}><option value=''>—</option>{report.teams.map((team) => <option key={team.team_id} value={team.team_id || ''}>{team.team_name}</option>)}</select></label>
          <label>Notatka <input value={draft.note || ''} onChange={(event) => update({ note: event.target.value })} /></label>
        </div>
        <div className='row end'><button type='button' className='secondary' disabled={disabled || busy === candidate.candidate_id || !onReject} onClick={() => { setBusy(candidate.candidate_id); void onReject?.(candidate).finally(() => setBusy('')); }}>Odrzuć</button><button type='button' disabled={disabled || busy === candidate.candidate_id || !onAccept} onClick={() => { setBusy(candidate.candidate_id); void onAccept?.(candidate, draft).finally(() => setBusy('')); }}>{busy === candidate.candidate_id ? 'Zapisuję…' : 'Akceptuj jako Key Moment'}</button></div>
      </article>;
    })}
  </section>;
}
