import { useEffect, useMemo, useState } from 'react';
import type { KeyMomentEditorialManual, KeyMomentEditorState, PublicMatchReport } from '../types';

type Props = {
  state: KeyMomentEditorState;
  report: PublicMatchReport;
  currentVideoTime?: (() => number | null) | null;
  onClose: () => void;
  onSave: (draft: {
    expected_revision: string;
    manual_moments: KeyMomentEditorialManual[];
    generated_suppressions: Array<{ generated_editorial_key: string }>;
    generated_overrides: Array<{ generated_editorial_key: string; presentation: Record<string, unknown> }>;
  }) => Promise<void>;
};

const categories = [
  ['goal_for_us', 'Gol dla nas'], ['goal_for_opponent', 'Gol dla rywali'], ['chance', 'Okazja'],
  ['good_action', 'Dobra akcja'], ['mistake', 'Błąd'], ['goalkeeper_intervention', 'Interwencja bramkarza'],
  ['defensive_action', 'Akcja defensywna'], ['tactical_note', 'Notatka taktyczna'], ['other', 'Inne'],
];

type GeneratedOverride = Record<string, unknown>;

function initialOverrides(state: KeyMomentEditorState): Record<string, GeneratedOverride> {
  return Object.fromEntries((state.generated_moments || [])
    .filter((moment) => moment.override)
    .map((moment) => [moment.generated_editorial_key, moment.override as GeneratedOverride]));
}

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

export function KeyMomentsEditorModal({ state, report, currentVideoTime, onClose, onSave }: Props) {
  const [manual, setManual] = useState<KeyMomentEditorialManual[]>(() => state.manual_moments || []);
  const [suppressed, setSuppressed] = useState(() => new Set((state.generated_moments || []).filter((item) => item.suppressed).map((item) => item.generated_editorial_key)));
  const [overrides, setOverrides] = useState<Record<string, GeneratedOverride>>(() => initialOverrides(state));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const dirty = useMemo(() => JSON.stringify(manual) !== JSON.stringify(state.manual_moments || []) || [...suppressed].sort().join('|') !== (state.generated_moments || []).filter((item) => item.suppressed).map((item) => item.generated_editorial_key).sort().join('|') || JSON.stringify(overrides) !== JSON.stringify(initialOverrides(state)), [manual, overrides, suppressed, state]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  });

  function close() {
    if (dirty && !window.confirm('Odrzucić niezapisane zmiany?')) return;
    onClose();
  }

  function add(time?: number | null) {
    const value = time ?? currentVideoTime?.() ?? 0;
    setManual((items) => [...items, { time_sec: value, category: 'other', headline: 'Nowy moment', note: '' }]);
  }

  function update(index: number, patch: Partial<KeyMomentEditorialManual>) {
    setManual((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  }

  async function save() {
    if (!state.revision) return;
    setSaving(true); setError('');
    try {
      await onSave({
        expected_revision: state.revision,
        manual_moments: manual,
        generated_suppressions: [...suppressed].map((generated_editorial_key) => ({ generated_editorial_key })),
        generated_overrides: Object.entries(overrides).map(([generated_editorial_key, presentation]) => ({ generated_editorial_key, presentation })),
      });
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
        {(state.generated_moments || []).map((moment) => <article className='key-moment-editor-row' key={moment.generated_editorial_key}>
          <strong>{formatTime(moment.time_sec)} · Automatyczny</strong><span>{moment.headline}</span>
          <button type='button' className='secondary' onClick={() => setSuppressed((keys) => { const next = new Set(keys); next.has(moment.generated_editorial_key) ? next.delete(moment.generated_editorial_key) : next.add(moment.generated_editorial_key); return next; })}>{suppressed.has(moment.generated_editorial_key) ? 'Przywróć' : 'Ukryj'}</button>
          <button type='button' className='secondary' onClick={() => setOverrides((items) => {
            if (items[moment.generated_editorial_key]) {
              const { [moment.generated_editorial_key]: _, ...rest } = items;
              return rest;
            }
            return { ...items, [moment.generated_editorial_key]: { headline: moment.headline, public_category: moment.public_category || moment.type, note: moment.note || '', team_id: moment.team_id || null, player_id: moment.player_id || null } };
          })}>{overrides[moment.generated_editorial_key] ? 'Cofnij edycję' : 'Edytuj prezentację'}</button>
          {overrides[moment.generated_editorial_key] && <div className='key-moment-editor-override'>
            <label>Tytuł <input value={String(overrides[moment.generated_editorial_key].headline || '')} onChange={(event) => setOverrides((items) => ({ ...items, [moment.generated_editorial_key]: { ...items[moment.generated_editorial_key], headline: event.target.value } }))} /></label>
            <label>Kategoria <select value={String(overrides[moment.generated_editorial_key].public_category || '')} onChange={(event) => setOverrides((items) => ({ ...items, [moment.generated_editorial_key]: { ...items[moment.generated_editorial_key], public_category: event.target.value } }))}>{categories.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>Notatka <input value={String(overrides[moment.generated_editorial_key].note || '')} onChange={(event) => setOverrides((items) => ({ ...items, [moment.generated_editorial_key]: { ...items[moment.generated_editorial_key], note: event.target.value } }))} /></label>
          </div>}
        </article>)}
        {manual.map((moment, index) => <article className='key-moment-editor-row manual' key={moment.moment_id || `new-${index}`}>
          <strong>Ręczny</strong>
          <label>Czas <input value={formatTime(moment.time_sec)} onChange={(event) => { const next = parseTime(event.target.value); if (next != null) update(index, { time_sec: next }); }} /></label>
          <label>Tytuł <input value={moment.headline} onChange={(event) => update(index, { headline: event.target.value })} /></label>
          <label>Kategoria <select value={moment.category} onChange={(event) => update(index, { category: event.target.value })}>{categories.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label>Notatka <input value={moment.note || ''} onChange={(event) => update(index, { note: event.target.value })} /></label>
          <label>Drużyna <select value={moment.team_id ?? ''} onChange={(event) => update(index, { team_id: event.target.value || null, player_id: null })}><option value=''>—</option>{report.teams.map((team) => <option key={team.team_id} value={team.team_id || ''}>{team.team_name}</option>)}</select></label>
          <label>Zawodnik <select value={moment.player_id || ''} onChange={(event) => update(index, { player_id: event.target.value || null })}><option value=''>—</option>{report.players.filter((player) => !moment.team_id || player.team_id === moment.team_id).map((player) => <option key={player.player_id} value={player.player_id}>{player.player_name}</option>)}</select></label>
          <button type='button' className='secondary' onClick={() => setManual((items) => items.filter((_, itemIndex) => itemIndex !== index))}>Usuń</button>
        </article>)}
      </div>
      {error && <p className='status'>{error}</p>}
      <div className='row end'><button type='button' className='secondary' onClick={close} disabled={saving}>Anuluj</button><button type='button' onClick={() => void save()} disabled={saving}>{saving ? 'Zapisuję…' : 'Zapisz zmiany'}</button></div>
    </section>
  </div>;
}
