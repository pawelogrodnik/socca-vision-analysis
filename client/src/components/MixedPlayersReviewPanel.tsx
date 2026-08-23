import { useEffect, useMemo, useState } from 'react';

import { artifactUrl, finalizeReviewedIdentityCorrections, getMixedBoundaryRefinement, getMixedPlayersReview, saveMixedPlayerResolution } from '../api';
import { errorMessage } from '../lib/helpers';
import type { Match, MixedBoundaryRefinement, MixedPlayersReviewQueue, MixedSegmentAssignment, ReviewWorkflow } from '../types';
import { assignmentLabel, mixedQueueAfterSuccessfulSave, mixedSegments, mixedTimeForFrame, remapMixedAssignments, replaceMixedBoundaryInInterval, sortedMixedEvidenceCrops, validMixedResolution } from '../utils/mixedPlayersReview';
import { matchTeamName } from '../utils/identityExceptionTeamFilter';
import { formatReviewTime } from '../utils/reviewedOutputPresentation';

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
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
  const [refinement, setRefinement] = useState<MixedBoundaryRefinement | null>(null);
  const [refinementBusy, setRefinementBusy] = useState(false);
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
    setHasUnsavedChanges(false);
    setRefinement(null);
    setMessage('');
  }, [reviewCase?.case_id, reviewCase?.candidate_subject_id]);

  const segments = useMemo(
    () => reviewCase ? mixedSegments(reviewCase, boundaries) : [],
    [boundaries, reviewCase],
  );

  function applyBoundaries(next: number[]) {
    if (!reviewCase) return false;
    const remapped = remapMixedAssignments(reviewCase, boundaries, next, assignments);
    if (remapped.requiresConfirmation && !window.confirm(
      'Zmiana podziału zmieni strukturę fragmentów. Niektóre zapisane przypisania trzeba będzie wyczyścić.\n\nAnuluj albo wybierz „OK”, aby zmienić podział.',
    )) return false;
    setBoundaries(next);
    setAssignments(remapped.assignments);
    setHasUnsavedChanges(true);
    setSelectedSegment(Math.min(selectedSegment, next.length));
    if (remapped.requiresConfirmation) {
      setMessage('Zmieniono granicę. Niejednoznaczne przypisania zmienionych fragmentów zostały wyczyszczone — przypisz je ponownie.');
    } else {
      setMessage('Zmieniono granicę bez utraty istniejących przypisań.');
    }
    return true;
  }

  async function openRefinement(afterFrame: number, beforeFrame: number) {
    if (!reviewCase) return;
    setRefinementBusy(true);
    setMessage('');
    try {
      const value = await getMixedBoundaryRefinement(
        match.id,
        reviewCase.candidate_subject_id,
        afterFrame,
        beforeFrame,
        reviewCase.case_id,
      );
      if (value.anchor_crops.length < 2) {
        if (applyBoundaries(replaceMixedBoundaryInInterval(boundaries, afterFrame, beforeFrame, afterFrame))) {
          setMessage('Przedział jest już wystarczająco dokładny — ustawiono podział bez dodatkowego kroku.');
        }
      } else {
        setRefinement(value);
      }
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setRefinementBusy(false);
    }
  }

  function selectRefinedBoundary(frame: number) {
    if (!refinement) return;
    const applied = applyBoundaries(replaceMixedBoundaryInInterval(
      boundaries,
      refinement.after_frame,
      refinement.before_frame,
      frame,
    ));
    if (applied) setRefinement(null);
  }

  function removeRefinedBoundary() {
    if (!refinement) return;
    const applied = applyBoundaries(boundaries.filter(
      (frame) => frame < refinement.after_frame || frame >= refinement.before_frame,
    ));
    if (applied) setRefinement(null);
  }

  function directBoundary(afterFrame: number) {
    applyBoundaries(boundaries.includes(afterFrame)
      ? boundaries.filter((frame) => frame !== afterFrame)
      : [...boundaries, afterFrame].sort((left, right) => left - right));
  }

  function assign(assignment: MixedSegmentAssignment) {
    setHasUnsavedChanges(true);
    setAssignments((current) => {
      const next = current.map((value, segmentIndex) => segmentIndex === selectedSegment ? assignment : value);
      const nextUnassigned = next.findIndex((value, segmentIndex) => segmentIndex > selectedSegment && value === null);
      if (nextUnassigned >= 0) setSelectedSegment(nextUnassigned);
      return next;
    });
  }

  async function saveSplit() {
    if (!reviewCase || !validMixedResolution(reviewCase, boundaries, assignments)) return;
    setBusy(true);
    setMessage('');
    try {
      await saveMixedPlayerResolution(match.id, {
        candidate_subject_id: reviewCase.candidate_subject_id,
        case_id: reviewCase.case_id,
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
        case_id: reviewCase.case_id,
        source_subject_digest: reviewCase.source_subject_digest,
        resolution: 'unresolved_complex_mix',
      });
      setHasUnsavedChanges(false);
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
    const next = mixedQueueAfterSuccessfulSave(
      queue.cases,
      reviewCase.case_id || reviewCase.candidate_subject_id,
      index,
    );
    setHasUnsavedChanges(false);
    setQueue({ ...queue, cases: next.cases });
    setIndex(next.index);
    if (next.cases.length === 0) {
      setMessage('Przeliczam Review po zapisaniu podziałów…');
      const result = await finalizeReviewedIdentityCorrections(match.id);
      onWorkflowChanged(result.workflow);
    }
  }

  function navigateTo(nextIndex: number) {
    if (!queue || nextIndex === index) return;
    if (hasUnsavedChanges && !window.confirm(
      'Masz niezapisany podział lub przypisania. Przejście do innego przypadku je odrzuci.\n\nWybierz „OK”, aby przejść bez zapisywania.',
    )) return;
    setIndex(Math.max(0, Math.min(queue.cases.length - 1, nextIndex)));
  }

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;
      if (event.key === 'ArrowLeft' && index > 0) {
        event.preventDefault();
        navigateTo(index - 1);
      } else if (event.key === 'ArrowRight' && queue && index < queue.cases.length - 1) {
        event.preventDefault();
        navigateTo(index + 1);
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [hasUnsavedChanges, index, queue]);

  if (busy && !queue) return <p className='loading-line'><span className='spinner' /> Ładuję zmieszane przypadki…</p>;
  if (!reviewCase || !queue) return <section className='identity-exception-review'><div className='status'>Brak zmieszanych przypadków do rozdzielenia.</div>{message && <p className='status'>{message}</p>}</section>;

  const crops = sortedMixedEvidenceCrops(reviewCase.temporal_evidence.anchor_crops);
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
    {reviewCase.reviewed_complex && <div className='mixed-complex-reviewed' role='status'>
      <strong>⚠ Przejrzano: brak prostego podziału czasowego</strong>
      <span>Przypadek nadal wymaga rozwiązania. Możesz spróbować podziału ponownie albo pozostawić go jako złożony.</span>
    </div>}
    <div className='mixed-review-workstation'>
      <section className='mixed-temporal-column'>
        <div className='identity-exception-column-heading'><strong>Materiał w kolejności czasu</strong><span>Wybierz przedział i doprecyzuj moment przejścia</span></div>
        <div className='mixed-temporal-strip'>
          {crops.map((crop, cropIndex) => <div className='mixed-crop-group' key={crop.anchor_crop_id}>
            <figure className={`team-${(crop.team_label || 'u').toLowerCase()}`}>
              <img src={artifactUrl(match.id, crop.artifact)} alt='Czasowy widok zmieszanego przypadku' />
              <figcaption>{formatReviewTime(crop.time_sec || 0)}</figcaption>
            </figure>
            {cropIndex < crops.length - 1 && <button
              type='button'
              className={boundaries.some((frame) => frame >= crop.frame && frame < crops[cropIndex + 1].frame) ? 'split-boundary active' : 'split-boundary'}
              onClick={() => crops.length <= 12 && reviewCase.observation_count <= 12
                ? directBoundary(crop.frame)
                : void openRefinement(crop.frame, crops[cropIndex + 1].frame)}
              disabled={busy || refinementBusy}
            >{boundaries.some((frame) => frame >= crop.frame && frame < crops[cropIndex + 1].frame)
              ? 'Zmień podział'
              : reviewCase.observation_count <= 12 ? 'Podziel tutaj' : 'Doprecyzuj'}</button>}
          </div>)}
        </div>
        {refinement && <section className='mixed-boundary-refinement' aria-label='Doprecyzowanie granicy podziału'>
          <header>
            <div><strong>Doprecyzuj moment przejścia</strong><span>Wybierz dokładniejszą granicę między sąsiednimi podglądami.</span></div>
            <button type='button' className='secondary' onClick={() => setRefinement(null)}>Zamknij</button>
          </header>
          <div className='mixed-refinement-strip'>
            <button type='button' className='mixed-refinement-leading-action' onClick={() => selectRefinedBoundary(refinement.after_frame)} disabled={busy}>Podziel zaraz po poprzednim podglądzie</button>
            {sortedMixedEvidenceCrops(refinement.anchor_crops).map((crop, cropIndex, refinementCrops) => <div className='mixed-refinement-crop' key={crop.anchor_crop_id}>
              <figure className={`team-${(crop.team_label || 'u').toLowerCase()}`}>
                <img src={artifactUrl(match.id, crop.artifact)} alt='Dokładniejszy widok przejścia między osobami' />
                <figcaption>{formatReviewTime(crop.time_sec || 0)}</figcaption>
              </figure>
              {cropIndex < refinementCrops.length - 1 && <button type='button' onClick={() => selectRefinedBoundary(crop.frame)} disabled={busy}>Ustaw tutaj</button>}
            </div>)}
          </div>
          {boundaries.some((frame) => frame >= refinement.after_frame && frame < refinement.before_frame) && <button type='button' className='secondary' onClick={removeRefinedBoundary}>Usuń podział z tego przedziału</button>}
        </section>}
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
              {(['A', 'B'] as const).map((team) => <optgroup key={team} label={matchTeamName(match.teams || [], team)}>{queue.assignment_options.roster.filter((player) => player.team_label === team).map((player) => <option key={player.player_id} value={player.player_id}>{player.player_name}{player.roster_number ? ` #${player.roster_number}` : ''}</option>)}</optgroup>)}
            </select>
          </label>
          <label>Ten sam gracz co Axx/Bxx
            <select value={selectedAssignment?.action === 'assign_existing_slot' ? selectedAssignment.stable_slot_id : ''} onChange={(event) => event.target.value && assign({ action: 'assign_existing_slot', stable_slot_id: event.target.value })}>
              <option value=''>Wybierz gracza</option>
              {queue.assignment_options.slots.map((slot) => <option key={slot.stable_slot_id} value={slot.stable_slot_id}>{slot.stable_slot_id}</option>)}
            </select>
          </label>
          <div className='reviewed-action-cards'>
            <button type='button' onClick={() => assign({ action: 'assign_team', team_label: 'A' })}>{matchTeamName(match.teams || [], 'A')} — nieznany</button>
            <button type='button' onClick={() => assign({ action: 'assign_team', team_label: 'B' })}>{matchTeamName(match.teams || [], 'B')} — nieznany</button>
            <button type='button' onClick={() => assign({ action: 'create_new_stable_player', team_label: 'A' })}>Nowy zawodnik ({matchTeamName(match.teams || [], 'A')})</button>
            <button type='button' onClick={() => assign({ action: 'create_new_stable_player', team_label: 'B' })}>Nowy zawodnik ({matchTeamName(match.teams || [], 'B')})</button>
            <button type='button' onClick={() => assign({ action: 'referee' })}>Sędzia</button>
            <button type='button' onClick={() => assign({ action: 'false_detection' })}>Fałszywa detekcja</button>
            <button type='button' onClick={() => assign({ action: 'team_unknown' })}>Nieznana drużyna</button>
            <button type='button' onClick={() => assign({ action: 'unresolved' })}>Nie wiem</button>
          </div>
        </div>
      </aside>
    </div>
    <footer className='mixed-review-footer'>
      <button type='button' className='secondary' onClick={() => navigateTo(index - 1)} disabled={busy || index === 0}>Poprzedni</button>
      <button type='button' onClick={() => void saveSplit()} disabled={busy || !validMixedResolution(reviewCase, boundaries, assignments)}>Zapisz podział + następny</button>
      <button type='button' className='secondary' onClick={() => void deferComplex()} disabled={busy}>Nie ma prostego podziału czasowego</button>
      <button type='button' className='secondary' onClick={() => navigateTo(index + 1)} disabled={busy || index >= queue.cases.length - 1}>Następny</button>
    </footer>
    {message && <p className='status' role='status'>{message}</p>}
  </section>;
}
