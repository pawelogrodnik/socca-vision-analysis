import { useState } from 'react';
import type { KeyMomentEditorialMoment, PublicMatchReport } from '../types';
import { formatKeyMomentTime, parseKeyMomentTime } from '../lib/keyMomentTime';

type Props = {
  mode: 'create' | 'edit' | 'accept';
  report: PublicMatchReport;
  initial: KeyMomentEditorialMoment;
  onCancel: () => void;
  onSave: (moment: KeyMomentEditorialMoment) => Promise<void>;
};

const categories = [
  ['goal_for_us', 'Gol dla nas'], ['goal_for_opponent', 'Gol dla rywali'], ['chance', 'Okazja'],
  ['good_action', 'Dobra akcja'], ['mistake', 'Błąd'], ['goalkeeper_intervention', 'Interwencja bramkarza'],
  ['defensive_action', 'Akcja defensywna'], ['tactical_note', 'Notatka taktyczna'],
  ['momentum_peak', 'Mocny okres'], ['possession_dominance', 'Przewaga w posiadaniu'], ['other', 'Inne'],
];

export function KeyMomentForm({ mode, report, initial, onCancel, onSave }: Props) {
  const [moment, setMoment] = useState(initial);
  const [timeText, setTimeText] = useState(initial.time_sec >= 0 ? formatKeyMomentTime(initial.time_sec) : '');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const existing = mode === 'edit';

  async function submit() {
    const timeSec = parseKeyMomentTime(timeText);
    if (timeSec == null || timeSec < 0 || timeSec > (report.match.duration_sec ?? 0)) {
      return setError('Podaj czas w zakresie meczu jako MM:SS, MM:SS.s lub liczbę sekund.');
    }
    if (!moment.category) return setError('Wybierz kategorię momentu.');
    if (!moment.headline.trim()) return setError('Tytuł momentu jest wymagany.');
    if (!existing && !moment.team_id) return setError('Wybierz drużynę dla nowego momentu.');
    setSaving(true);
    setError('');
    try {
      await onSave({ ...moment, time_sec: timeSec, headline: moment.headline.trim() });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Nie udało się zapisać momentu.');
    } finally {
      setSaving(false);
    }
  }

  const title = mode === 'create' ? 'Nowy Key Moment' : mode === 'accept' ? 'Akceptuj sugerowany moment' : 'Edytuj Key Moment';
  return <section className='key-moment-focus-form' aria-labelledby='key-moment-focus-title'>
    <h3 id='key-moment-focus-title'>{title}</h3>
    <label>Czas *<input aria-label='Czas momentu' value={timeText} onChange={(event) => setTimeText(event.target.value)} /></label>
    <label>Drużyna {existing ? '' : '*'}<select aria-label='Drużyna momentu' value={moment.team_id || ''} onChange={(event) => setMoment((value) => ({ ...value, team_id: event.target.value || null, player_id: null }))}><option value=''>—</option>{report.teams.map((team) => <option key={team.team_id} value={team.team_id || ''}>{team.team_name || team.team_label}</option>)}</select></label>
    <label>Kategoria *<select aria-label='Kategoria momentu' value={moment.category} onChange={(event) => setMoment((value) => ({ ...value, category: event.target.value }))}>{categories.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    <label>Tytuł *<input aria-label='Tytuł momentu' value={moment.headline} onChange={(event) => setMoment((value) => ({ ...value, headline: event.target.value }))} /></label>
    <label>Notatka<input aria-label='Notatka momentu' value={moment.note || ''} onChange={(event) => setMoment((value) => ({ ...value, note: event.target.value }))} /></label>
    <label>Zawodnik<select aria-label='Zawodnik momentu' value={moment.player_id || ''} onChange={(event) => setMoment((value) => ({ ...value, player_id: event.target.value || null }))}><option value=''>—</option>{report.players.filter((player) => !moment.team_id || player.team_id === moment.team_id).map((player) => <option key={player.player_id} value={player.player_id}>{player.player_name}</option>)}</select></label>
    {error ? <p className='status'>{error}</p> : null}
    <div className='row end'><button type='button' className='secondary' disabled={saving} onClick={onCancel}>Anuluj</button><button type='button' disabled={saving} onClick={() => void submit()}>{saving ? 'Zapisuję…' : 'Zapisz moment'}</button></div>
  </section>;
}
