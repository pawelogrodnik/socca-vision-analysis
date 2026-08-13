import { useEffect, useMemo, useState } from 'react';

import { artifactUrl, finalizeReviewedIdentityCorrections, getMixedPlayersReview, saveMixedPlayerResolution } from '../api';
import { errorMessage } from '../lib/helpers';
import type { Match, MixedPlayersReviewQueue, MixedSegmentAssignment, ReviewWorkflow } from '../types';
import { assignmentLabel, mixedQueueAfterSuccessfulSave, mixedSegments, mixedTimeForFrame, toggleMixedBoundary, validMixedResolution } from '../utils/mixedPlayersReview';
import { formatReviewTime, teamLabelForOperator } from '../utils/reviewedOutputPresentation';

type Props = {
  match: Match;
  workflow: ReviewWorkflow;
  onWorkflowChanged: (workflow: ReviewWorkflow) => void;
};

export function MixedPlayersReviewPanel({ match, workflow, onWorkflowChanged }: Props) {
  const [queue, setQueue] = useState<MixedPlayersReviewQueue | null>(null);
  const [index, setIndex] = useState(0);
  const reviewCase = queue?.cases[index] || null;
  const [boundaries, setBoundaries] = useState<number[]>([]);
  const [assignments, setAssignments] = useState<Array<MixedSegmentAssignment | null>>([]);
  const [selectedSegment, setSelectedSegment] = useState(0);
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState('');
  void workflow;

  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    getMixedPlayersReview(match.id)
      .then((value) => { if (!cancelled) setQueue(value); })
      .catch((error) => { if (!cancelled) setMessage(errorMessage(error)); })
      .finally(() => { if (!cancelled) setBusy(false); });
    return () => { cancelled = true; };
  }, [match.id]);

  useEffect(() => {
    if (!reviewCase) return;
    const nextBoundaries: number[] = [];
    setBoundaries(nextBoundaries);
    setAssignments(Array(nextBoundaries.length + 1).fill(null));
    setSelectedSegment(0);
    setMessage('');
  }, [reviewCase?.candidate_subject_id]);

  const segments = useMemo(
    () => reviewCase ? mixedSegments(reviewCase, boundaries) : [],
    [boundaries, reviewCase],
  );

  function toggleBoundary(frame: number) {
    if (!reviewCase) return;
    const next = toggleMixedBoundary(boundaries, frame);
    setBoundaries(next);
    setAssignments(Array(next.length + 1).fill(null));
    setSelectedSegment(Math.min(selectedSegment, next.length));
  }

  function assign(assignment: MixedSegmentAssignment) {
    setAssignments((current) => current.map((value, segmentIndex) => segmentIndex === selectedSegment ? assignment : value));
  }

  async function saveSplit() {
    if (!reviewCase || !validMixedResolution(reviewCase, boundaries, assignments)) return;
    setBusy(true);
    setMessage('');
    try {
      await saveMixedPlayerResolution(match.id, {
        candidate_subject_id: reviewCase.candidate_subject_id,
        source_subject_digest: reviewCase.source_subject_digest,
        resolution: 'split',
        split_after_frames: boundaries,
        segment_assignments: assignments.filter((value): value is MixedSegmentAssignment => value !== null),
      });
      await advanceAfterSave();
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function deferComplex() {
    if (!reviewCase) return;
    setBusy(true);
    setMessage('');
    try {
      await saveMixedPlayerResolution(match.id, {
        candidate_subject_id: reviewCase.candidate_subject_id,
        source_subject_digest: reviewCase.source_subject_digest,
        resolution: 'unresolved_complex_mix',
      });
      setMessage('Zapisano jako przypadek bez prostego podziału czasowego. Tożsamość nie została zgadnięta.');
      if (queue && index < queue.cases.length - 1) setIndex(index + 1);
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function advanceAfterSave() {
    if (!queue || !reviewCase) return;
    const next = mixedQueueAfterSuccessfulSave(queue.cases, reviewCase.candidate_subject_id, index);
    setQueue({ ...queue, cases: next.cases });
    setIndex(next.index);
    if (next.cases.length === 0) {
      setMessage('Przeliczam Review po zapisaniu podziałów…');
      const result = await finalizeReviewedIdentityCorrections(match.id);
      onWorkflowChanged(result.workflow);
    }
  }

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;
      if (event.key === 'ArrowLeft' && index > 0) {
        event.preventDefault();
        setIndex(index - 1);
      } else if (event.key === 'ArrowRight' && queue && index < queue.cases.length - 1) {
        event.preventDefault();
        setIndex(index + 1);
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [index, queue]);

  if (busy && !queue) return <p className='loading-line'><span className='spinner' /> Ładuję zmieszane przypadki…</p>;
  if (!reviewCase || !queue) return <section className='identity-exception-review'><div className='status'>Brak zmieszanych przypadków do rozdzielenia.</div>{message && <p className='status'>{message}</p>}</section>;

  const crops = [...reviewCase.temporal_evidence.anchor_crops].sort((left, right) => left.frame - right.frame);
  const selectedAssignment = assignments[selectedSegment] || null;
  const selected = segments[selectedSegment];
  const segmentTimeLabel = (frameStart: number, frameEnd: number) => {
    const timeStart = mixedTimeForFrame(reviewCase, frameStart);
    const timeEnd = mixedTimeForFrame(reviewCase, frameEnd);
    return timeStart !== null && timeEnd !== null
      ? `${formatReviewTime(timeStart)}–${formatReviewTime(timeEnd)}`
      : `Klatki ${frameStart}–${frameEnd}`;
  };
  return <section className='identity-exception-review mixed-player-review'>
    <header className='identity-exception-header'>
      <div className='identity-exception-heading'>
        <p className='eyebrow'>Zmieszani gracze</p>
        <h2>Rozdziel zmieszane tracki</h2>
        <p>Ustaw granice między osobami, a następnie przypisz każdy fragment osobno.</p>
      </div>
      <div className='identity-exception-case-context'>
        <span className='reviewed-status-badge'>Przypadek {index + 1} z {queue.cases.length}</span>
        <span>{segmentTimeLabel(reviewCase.frame_start, reviewCase.frame_end)}</span>
        <span>{reviewCase.observation_count} wykrytych obserwacji</span>
      </div>
    </header>
    <div className='mixed-review-workstation'>
      <section className='mixed-temporal-column'>
        <div className='identity-exception-column-heading'><strong>Materiał w kolejności czasu</strong><span>Kliknij „Podziel tutaj”</span></div>
        <div className='mixed-temporal-strip'>
          {crops.map((crop, cropIndex) => <div className='mixed-crop-group' key={crop.anchor_crop_id}>
            <figure className={`team-${(crop.team_label || 'u').toLowerCase()}`}>
              <img src={artifactUrl(match.id, crop.artifact)} alt='Czasowy widok zmieszanego przypadku' />
              <figcaption>{formatReviewTime(crop.time_sec || 0)}</figcaption>
            </figure>
            {cropIndex < crops.length - 1 && <button
              type='button'
              className={boundaries.includes(crop.frame) ? 'split-boundary active' : 'split-boundary'}
              onClick={() => toggleBoundary(crop.frame)}
              disabled={busy}
            >{boundaries.includes(crop.frame) ? 'Usuń podział' : 'Podziel tutaj'}</button>}
          </div>)}
        </div>
        <div className='mixed-segment-list' aria-label='Fragmenty po podziale'>
          {segments.map((segment) => {
            const assignment = assignments[segment.index];
            const rosterName = queue.assignment_options.roster.find((player) => player.player_id === assignment?.player_id)?.player_name;
            return <button type='button' key={`${segment.frameStart}-${segment.frameEnd}`} className={selectedSegment === segment.index ? 'selected' : ''} onClick={() => setSelectedSegment(segment.index)}>
              <strong>Fragment {segment.index + 1}</strong>
              <span>{segmentTimeLabel(segment.frameStart, segment.frameEnd)}</span>
              <small>{assignment ? `✓ ${assignmentLabel(assignment, rosterName)}` : '! Nie przypisano'}</small>
            </button>;
          })}
        </div>
      </section>
      <aside className='mixed-assignment-panel'>
        <header><h3>Wybrany fragment {selectedSegment + 1}</h3><p>{selected ? segmentTimeLabel(selected.frameStart, selected.frameEnd) : ''}</p><strong>{assignmentLabel(selectedAssignment)}</strong></header>
        <div className='mixed-assignment-scroll'>
          <label>Zawodnik z kadry
            <select value={selectedAssignment?.action === 'assign_roster_player' ? selectedAssignment.player_id : ''} onChange={(event) => event.target.value && assign({ action: 'assign_roster_player', player_id: event.target.value })}>
              <option value=''>Wybierz zawodnika</option>
              {['A', 'B'].map((team) => <optgroup key={team} label={teamLabelForOperator(team)}>{queue.assignment_options.roster.filter((player) => player.team_label === team).map((player) => <option key={player.player_id} value={player.player_id}>{player.player_name}{player.roster_number ? ` #${player.roster_number}` : ''}</option>)}</optgroup>)}
            </select>
          </label>
          <label>Ten sam gracz co Axx/Bxx
            <select value={selectedAssignment?.action === 'assign_existing_slot' ? selectedAssignment.stable_slot_id : ''} onChange={(event) => event.target.value && assign({ action: 'assign_existing_slot', stable_slot_id: event.target.value })}>
              <option value=''>Wybierz gracza</option>
              {queue.assignment_options.slots.map((slot) => <option key={slot.stable_slot_id} value={slot.stable_slot_id}>{slot.stable_slot_id}</option>)}
            </select>
          </label>
          <div className='reviewed-action-cards'>
            <button type='button' onClick={() => assign({ action: 'assign_team', team_label: 'A' })}>Team A — nieznany</button>
            <button type='button' onClick={() => assign({ action: 'assign_team', team_label: 'B' })}>Team B — nieznany</button>
            <button type='button' onClick={() => assign({ action: 'create_new_stable_player', team_label: 'A' })}>Nowy zawodnik A</button>
            <button type='button' onClick={() => assign({ action: 'create_new_stable_player', team_label: 'B' })}>Nowy zawodnik B</button>
            <button type='button' onClick={() => assign({ action: 'referee' })}>Sędzia</button>
            <button type='button' onClick={() => assign({ action: 'false_detection' })}>Fałszywa detekcja</button>
            <button type='button' onClick={() => assign({ action: 'team_unknown' })}>Nieznana drużyna</button>
            <button type='button' onClick={() => assign({ action: 'unresolved' })}>Nie wiem</button>
          </div>
        </div>
      </aside>
    </div>
    <footer className='mixed-review-footer'>
      <button type='button' className='secondary' onClick={() => setIndex(Math.max(0, index - 1))} disabled={busy || index === 0}>Poprzedni</button>
      <button type='button' onClick={() => void saveSplit()} disabled={busy || !validMixedResolution(reviewCase, boundaries, assignments)}>Zapisz podział + następny</button>
      <button type='button' className='secondary' onClick={() => void deferComplex()} disabled={busy}>Nie ma prostego podziału czasowego</button>
      <button type='button' className='secondary' onClick={() => setIndex(Math.min(queue.cases.length - 1, index + 1))} disabled={busy || index >= queue.cases.length - 1}>Następny</button>
    </footer>
    {message && <p className='status' role='status'>{message}</p>}
  </section>;
}
