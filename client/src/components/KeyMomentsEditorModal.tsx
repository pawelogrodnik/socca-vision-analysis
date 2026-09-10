import { useEffect, useMemo, useRef, useState } from 'react';
import type { KeyMomentEditorialMoment, KeyMomentEditorState, PublicMatchReport } from '../types';
import { SuggestedKeyMoments } from './SuggestedKeyMoments';

type Props = {
  state: KeyMomentEditorState;
  report: PublicMatchReport;
  currentVideoTime?: (() => number | null) | null;
  onClose: () => void;
  onSave: (draft: { expected_revision: string; moments: KeyMomentEditorialMoment[] }) => Promise<void>;
  onAcceptSuggestion?: (payload: { expected_revision: string; candidate_id: string; candidate_generation_digest: string; moment: KeyMomentEditorialMoment }) => Promise<KeyMomentEditorState>;
  onRejectSuggestion?: (payload: { candidate_id: string; candidate_generation_digest: string }) => Promise<KeyMomentEditorState>;
};

const categories = [
  ['goal_for_us', 'Gol dla nas'], ['goal_for_opponent', 'Gol dla rywali'], ['chance', 'Okazja'],
  ['good_action', 'Dobra akcja'], ['mistake', 'Błąd'], ['goalkeeper_intervention', 'Interwencja bramkarza'],
  ['defensive_action', 'Akcja defensywna'], ['tactical_note', 'Notatka taktyczna'],
  ['momentum_peak', 'Mocny okres'], ['possession_dominance', 'Przewaga w posiadaniu'], ['other', 'Inne'],
];

function parseTime(value: string): number | null {
  const trimmed = value.trim();
  if (/^\d+(\.\d+)?$/.test(trimmed)) return Number(trimmed);
  const match = /^(\d+):(\d{2}(?:\.\d+)?)$/.exec(trimmed);
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
}

function formatTime(value: number): string {
  const safe = Math.max(0, value);
  return `${Math.floor(safe / 60)}:${String(Math.floor(safe % 60)).padStart(2, '0')}.${Math.round((safe % 1) * 10)}`;
}

function ordered(moments: KeyMomentEditorialMoment[]) {
  return [...moments].sort((left, right) => left.time_sec - right.time_sec || (left.moment_id || '').localeCompare(right.moment_id || ''));
}

function timestampKey(moment: KeyMomentEditorialMoment, index: number) {
  return moment.moment_id || `new-${index}`;
}

export function KeyMomentsEditorModal({ state, report, currentVideoTime, onClose, onSave, onAcceptSuggestion, onRejectSuggestion }: Props) {
  const [moments, setMoments] = useState<KeyMomentEditorialMoment[]>(() => ordered(state.moments || []));
  const [baselineMoments, setBaselineMoments] = useState<KeyMomentEditorialMoment[]>(() => ordered(state.moments || []));
  const [revision, setRevision] = useState(state.revision || '');
  const [suggestions, setSuggestions] = useState(state.suggestions);
  const [timestampTexts, setTimestampTexts] = useState<Record<string, string>>({});
  const timestampTextsRef = useRef<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const dirty = useMemo(() => JSON.stringify(ordered(moments)) !== JSON.stringify(ordered(baselineMoments)), [moments, baselineMoments]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') close(); };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  });

  function close() {
    if (dirty && !window.confirm('Odrzucić niezapisane zmiany?')) return;
    onClose();
  }

  function add(time?: number | null) {
    setMoments((items) => [{
      time_sec: time ?? currentVideoTime?.() ?? 0,
      category: 'other', headline: 'Nowy moment', note: '', origin: 'manual',
    }, ...items]);
  }

  function update(index: number, patch: Partial<KeyMomentEditorialMoment>) {
    setMoments((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  }

  function commitTimestamp(index: number) {
    const key = timestampKey(moments[index], index);
    const text = timestampTextsRef.current[key];
    if (text === undefined) return true;
    const parsed = parseTime(text);
    if (parsed === null) {
      setError('Podaj czas jako MM:SS, MM:SS.s lub liczbę sekund.');
      return false;
    }
    update(index, { time_sec: parsed });
    setTimestampTexts((values) => {
      const { [key]: _, ...rest } = values;
      timestampTextsRef.current = rest;
      return rest;
    });
    return true;
  }

  async function save() {
    if (!revision) return;
    setSaving(true); setError('');
    try {
      const draft = moments.map((moment, index) => {
        const text = timestampTextsRef.current[timestampKey(moment, index)];
        if (text === undefined) return moment;
        const parsed = parseTime(text);
        if (parsed === null) throw new Error('Podaj czas jako MM:SS, MM:SS.s lub liczbę sekund.');
        return { ...moment, time_sec: parsed };
      });
      await onSave({ expected_revision: revision, moments: ordered(draft) });
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Nie udało się zapisać zmian.');
    } finally { setSaving(false); }
  }

  return <div className='modal-backdrop' role='presentation' onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
    <section className='modal key-moments-editor' role='dialog' aria-modal='true' aria-labelledby='key-moments-editor-title'>
      <div className='row between'><h2 id='key-moments-editor-title'>Edycja Key Moments</h2><button type='button' aria-label='Zamknij edytor' className='secondary' onClick={close}>×</button></div>
      <div className='row'>
        <button type='button' onClick={() => add()}>Dodaj moment</button>
        {currentVideoTime && <button type='button' className='secondary' onClick={() => add(currentVideoTime())}>Dodaj z aktualnego czasu</button>}
      </div>
      <div className='key-moment-editor-list'>
        {moments.map((moment, index) => {
          const key = timestampKey(moment, index);
          return <article className='key-moment-editor-row' key={moment.moment_id || `new-${index}`}>
            <strong>{formatTime(moment.time_sec)}{moment.origin === 'generated' ? ' · Automatyczny' : ''}</strong>
            <label>Czas <input value={timestampTexts[key] ?? formatTime(moment.time_sec)} onInput={(event) => {
              const value = event.currentTarget.value;
              timestampTextsRef.current = { ...timestampTextsRef.current, [key]: value };
              setTimestampTexts((values) => ({ ...values, [key]: value }));
            }} onBlur={() => { void commitTimestamp(index); }} /></label>
            <label>Tytuł <input value={moment.headline} onChange={(event) => update(index, { headline: event.target.value })} /></label>
            <label>Kategoria <select value={moment.category} onChange={(event) => update(index, { category: event.target.value })}>{categories.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>Notatka <input value={moment.note || ''} onChange={(event) => update(index, { note: event.target.value })} /></label>
            <label>Drużyna <select value={moment.team_id ?? ''} onChange={(event) => update(index, { team_id: event.target.value || null, player_id: null })}><option value=''>—</option>{report.teams.map((team) => <option key={team.team_id} value={team.team_id || ''}>{team.team_name}</option>)}</select></label>
            <label>Zawodnik <select value={moment.player_id || ''} onChange={(event) => update(index, { player_id: event.target.value || null })}><option value=''>—</option>{report.players.filter((player) => !moment.team_id || player.team_id === moment.team_id).map((player) => <option key={player.player_id} value={player.player_id}>{player.player_name}</option>)}</select></label>
            <button type='button' className='secondary' onClick={() => setMoments((items) => items.filter((_, itemIndex) => itemIndex !== index))}>Usuń</button>
          </article>;
        })}
      </div>
      <SuggestedKeyMoments
        state={suggestions}
        report={report}
        disabled={saving || dirty}
        onAccept={onAcceptSuggestion && (async (candidate, moment) => {
          if (!suggestions?.candidate_generation_digest) return;
          try {
            setSaving(true); setError('');
            const saved = await onAcceptSuggestion({ expected_revision: revision, candidate_id: candidate.candidate_id, candidate_generation_digest: suggestions.candidate_generation_digest, moment });
            const accepted = saved.accepted_moment;
            if (accepted) setMoments((items) => [accepted, ...items]);
            setBaselineMoments(saved.moments || baselineMoments);
            setRevision(saved.revision || revision);
            setSuggestions(saved.suggestions);
          } catch (acceptError) { setError(acceptError instanceof Error ? acceptError.message : 'Nie udało się zaakceptować sugestii.'); }
          finally { setSaving(false); }
        })}
        onReject={onRejectSuggestion && (async (candidate) => {
          if (!suggestions?.candidate_generation_digest) return;
          try {
            setSaving(true); setError('');
            const saved = await onRejectSuggestion({ candidate_id: candidate.candidate_id, candidate_generation_digest: suggestions.candidate_generation_digest });
            setRevision(saved.revision || revision);
            setSuggestions(saved.suggestions);
          } catch (rejectError) { setError(rejectError instanceof Error ? rejectError.message : 'Nie udało się odrzucić sugestii.'); }
          finally { setSaving(false); }
        })}
      />
      {error && <p className='status'>{error}</p>}
      <div className='row end'><button type='button' className='secondary' onClick={close} disabled={saving}>Anuluj</button><button type='button' onClick={() => void save()} disabled={saving}>{saving ? 'Zapisuję…' : 'Zapisz'}</button></div>
    </section>
  </div>;
}
