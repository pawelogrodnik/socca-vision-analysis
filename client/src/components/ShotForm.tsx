import { useRef, useState } from 'react';
import type { CanonicalShot, PublicMatchReport, ShotFrameLocationOverride, ShotFrameLocationProjection, ShotOutcome, ShotPitchLocation, ShotReviewShotInput } from '../types';
import { formatKeyMomentTime, parseKeyMomentTime } from '../lib/keyMomentTime';
import { ShotPitchPointSelector } from './ShotPitchPointSelector';
import { ShotFrameLocationCorrection } from './ShotFrameLocationCorrection';

type Mode = 'create' | 'edit' | 'accept';

type ShotFormInitial = Pick<CanonicalShot, 'time_sec' | 'team_id' | 'player_id' | 'location_m' | 'location_source'> & {
  outcome: ShotOutcome | '';
};

type Props = {
  mode: Mode;
  report: PublicMatchReport;
  initial: ShotFormInitial;
  onCancel: () => void;
  onSave: (shot: ShotReviewShotInput) => Promise<void>;
  onUseCurrentVideoTime?: () => number | null;
  publishedMatchId?: string;
};

const outcomes: Array<[ShotOutcome, string]> = [
  ['goal', 'Gol'], ['on_target', 'Celny'], ['off_target', 'Niecelny'], ['blocked', 'Zablokowany'],
];

export function ShotForm({ mode, report, initial, onCancel, onSave, onUseCurrentVideoTime, publishedMatchId = report.id }: Props) {
  const [shot, setShot] = useState(initial);
  const [timeText, setTimeText] = useState(initial.time_sec >= 0 ? formatKeyMomentTime(initial.time_sec) : '');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [manualPoint, setManualPoint] = useState<ShotPitchLocation | null>(null);
  const [frameLocationOverride, setFrameLocationOverride] = useState<ShotFrameLocationOverride | null>(null);
  const [frameProjection, setFrameProjection] = useState<ShotFrameLocationProjection | null>(null);
  const [showFrameCorrection, setShowFrameCorrection] = useState(false);
  const frameCorrectionTriggerRef = useRef<HTMLButtonElement>(null);
  const [showPitchPicker, setShowPitchPicker] = useState(initial.location_source === 'unavailable');
  const hasResolvedSuggestedTeam = mode === 'accept'
    && Boolean(initial.team_id)
    && report.teams.some((team) => team.team_id === initial.team_id);
  const [teamConfirmed, setTeamConfirmed] = useState(mode !== 'accept' || hasResolvedSuggestedTeam);
  const [outcomeConfirmed, setOutcomeConfirmed] = useState(mode !== 'accept');
  const dimensions = report.team_shape?.pitch_dimensions_m || { width_m: 30, length_m: 47.4 };

  async function submit() {
    const timeSec = parseKeyMomentTime(timeText);
    if (timeSec == null || timeSec < 0 || timeSec > (report.match.duration_sec ?? 0)) {
      return setError('Podaj czas w zakresie meczu jako MM:SS, MM:SS.s lub liczbę sekund.');
    }
    if (!shot.team_id) return setError('Wybierz drużynę.');
    if (!shot.outcome) return setError('Wybierz wynik strzału.');
    if (!teamConfirmed || !outcomeConfirmed) return setError('Potwierdź drużynę i wynik strzału.');
    const payload: ShotReviewShotInput = {
      time_sec: timeSec,
      team_id: shot.team_id,
      outcome: shot.outcome,
      player_id: shot.player_id || null,
    };
    if (frameLocationOverride) {
      payload.frame_location_override = frameLocationOverride;
    } else if (manualPoint) {
      if (mode === 'edit') payload.manual_location_override = manualPoint;
      else payload.location_m = manualPoint;
    }
    setSaving(true); setError('');
    try {
      await onSave(payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Nie udało się zapisać strzału.');
    } finally {
      setSaving(false);
    }
  }

  const title = mode === 'create' ? 'Nowy strzał' : mode === 'accept' ? 'Akceptuj sugerowany strzał' : 'Edytuj strzał';
  return <section className='key-moment-focus-form shot-focus-form' aria-labelledby='shot-focus-title'>
    <h3 id='shot-focus-title'>{title}</h3>
    {mode === 'accept' ? <p className='muted'>Sprawdź drużynę i wybierz wynik przed zapisem.</p> : null}
    <label>Czas *<input aria-label='Czas strzału' value={timeText} onChange={(event) => setTimeText(event.target.value)} /></label>
    {mode === 'accept' && onUseCurrentVideoTime ? <button type='button' className='secondary shot-location-toggle' onClick={() => { const time = onUseCurrentVideoTime(); if (typeof time === 'number' && Number.isFinite(time)) setTimeText(formatKeyMomentTime(time)); }}>Użyj aktualnego czasu filmu</button> : null}
    <label>Drużyna *<select aria-label='Drużyna strzału' value={shot.team_id} onChange={(event) => { setShot((value) => ({ ...value, team_id: event.target.value, player_id: null })); setTeamConfirmed(true); }}><option value=''>—</option>{report.teams.map((team) => <option key={team.team_id} value={team.team_id || ''}>{team.team_name || team.team_label}</option>)}</select></label>
    <label>Wynik *<select aria-label='Wynik strzału' value={shot.outcome || ''} onChange={(event) => { setShot((value) => ({ ...value, outcome: event.target.value as ShotOutcome })); setOutcomeConfirmed(true); }}><option value=''>—</option>{outcomes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    <label>Zawodnik<select aria-label='Zawodnik strzału' value={shot.player_id || ''} onChange={(event) => setShot((value) => ({ ...value, player_id: event.target.value || null }))}><option value=''>—</option>{report.players.filter((player) => player.team_id === shot.team_id).map((player) => <option key={player.player_id} value={player.player_id}>{player.player_name}</option>)}</select></label>
    <div className='shot-location-summary'><span>{frameProjection ? 'Pozycja z wybranej klatki' : locationText(shot.location_source)}</span>{frameProjection ? <span>{frameProjection.location_m.x.toFixed(2)} × {frameProjection.location_m.y.toFixed(2)} m</span> : shot.location_m ? <span>Pozycja zapisana automatycznie.</span> : null}</div>
    <button ref={frameCorrectionTriggerRef} type='button' className='secondary shot-location-toggle' onClick={() => setShowFrameCorrection(true)}>Ustaw pozycję z klatki</button>
    {showFrameCorrection ? <ShotFrameLocationCorrection publishedMatchId={publishedMatchId} initialFrameTimeSec={parseKeyMomentTime(timeText) ?? initial.time_sec} value={frameLocationOverride} projection={frameProjection} onChange={(value, projection) => { setFrameLocationOverride(value); setFrameProjection(projection); if (value) setManualPoint(null); }} onClose={() => { setShowFrameCorrection(false); window.setTimeout(() => frameCorrectionTriggerRef.current?.focus(), 0); }} /> : null}
    {!showPitchPicker ? <button type='button' className='secondary shot-location-toggle' onClick={() => setShowPitchPicker(true)}>Popraw pozycję ręcznie</button> : <ShotPitchPointSelector value={manualPoint} widthM={dimensions.width_m} lengthM={dimensions.length_m} onChange={(point) => { setManualPoint(point); setFrameLocationOverride(null); setFrameProjection(null); }} />}
    {error ? <p className='status'>{error}</p> : null}
    <div className='row end'><button type='button' className='secondary' disabled={saving} onClick={onCancel}>Anuluj</button><button type='button' disabled={saving} onClick={() => void submit()}>{saving ? 'Zapisuję…' : 'Zapisz strzał'}</button></div>
  </section>;
}

function locationText(source: CanonicalShot['location_source']): string {
  return ({ ball: 'Pozycja z piłki', player: 'Pozycja zawodnika', manual: 'Pozycja ręczna', unavailable: 'Brak pozycji' })[source];
}
