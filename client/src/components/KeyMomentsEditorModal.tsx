import { useEffect, useMemo, useState } from 'react';
import type { KeyMomentEditorialMoment, KeyMomentEditorState, PublicMatchReport } from '../types';

type Props = {
  state: KeyMomentEditorState;
  report: PublicMatchReport;
  currentVideoTime?: (() => number | null) | null;
  onClose: () => void;
  onSave: (draft: { expected_revision: string; moments: KeyMomentEditorialMoment[] }) => Promise<void>;
};

const categories = [
  ['goal_for_us', 'Gol dla nas'], ['goal_for_opponent', 'Gol dla rywali'], ['chance', 'Okazja'],
  ['good_action', 'Dobra akcja'], ['mistake', 'Błąd'], ['goalkeeper_intervention', 'Interwencja bramkarza'],
  ['defensive_action', 'Akcja defensywna'], ['tactical_note', 'Notatka taktyczna'], ['other', 'Inne'],
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

export function KeyMomentsEditorModal({ state, report, currentVideoTime, onClose, onSave }: Props) {
  const [moments, setMoments] = useState<KeyMomentEditorialMoment[]>(() => state.moments || []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const dirty = useMemo(() => JSON.stringify(ordered(moments)) !== JSON.stringify(ordered(state.moments || [])), [moments, state.moments]);

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
    setMoments((items) => [...items, {
      time_sec: time ?? currentVideoTime?.() ?? 0,
      category: 'other', headline: 'Nowy moment', note: '', origin: 'manual',
    }]);
  }

  function update(index: number, patch: Partial<KeyMomentEditorialMoment>) {
    setMoments((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  }

  async function save() {
    if (!state.revision) return;
    setSaving(true); setError('');
    try {
      await onSave({ expected_revision: state.revision, moments: ordered(moments) });
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
        {ordered(moments).map((moment) => {
          const index = moments.indexOf(moment);
          return <article className='key-moment-editor-row' key={moment.moment_id || `new-${index}`}>
            <strong>{formatTime(moment.time_sec)}{moment.origin === 'generated' ? ' · Automatyczny' : ''}</strong>
            <label>Czas <input value={formatTime(moment.time_sec)} onChange={(event) => { const next = parseTime(event.target.value); if (next != null) update(index, { time_sec: next }); }} /></label>
            <label>Tytuł <input value={moment.headline} onChange={(event) => update(index, { headline: event.target.value })} /></label>
            <label>Kategoria <select value={moment.category} onChange={(event) => update(index, { category: event.target.value })}>{categories.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>Notatka <input value={moment.note || ''} onChange={(event) => update(index, { note: event.target.value })} /></label>
            <label>Drużyna <select value={moment.team_id ?? ''} onChange={(event) => update(index, { team_id: event.target.value || null, player_id: null })}><option value=''>—</option>{report.teams.map((team) => <option key={team.team_id} value={team.team_id || ''}>{team.team_name}</option>)}</select></label>
            <label>Zawodnik <select value={moment.player_id || ''} onChange={(event) => update(index, { player_id: event.target.value || null })}><option value=''>—</option>{report.players.filter((player) => !moment.team_id || player.team_id === moment.team_id).map((player) => <option key={player.player_id} value={player.player_id}>{player.player_name}</option>)}</select></label>
            <button type='button' className='secondary' onClick={() => setMoments((items) => items.filter((_, itemIndex) => itemIndex !== index))}>Usuń</button>
          </article>;
        })}
      </div>
      {error && <p className='status'>{error}</p>}
      <div className='row end'><button type='button' className='secondary' onClick={close} disabled={saving}>Anuluj</button><button type='button' onClick={() => void save()} disabled={saving}>{saving ? 'Zapisuję…' : 'Zapisz'}</button></div>
    </section>
  </div>;
}
