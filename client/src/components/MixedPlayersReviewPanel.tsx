import { useEffect, useMemo, useRef, useState } from 'react';

import { artifactUrl, getMixedBoundaryRefinement, getMixedPlayerReviewCase, getMixedPlayersReview, getReviewWorkflow, reprojectReviewWorkflow, saveMixedPlayerResolution } from '../api';
import { errorMessage } from '../lib/helpers';
import type { Match, MixedBoundaryRefinement, MixedPlayersReviewQueue, MixedSegmentAssignment, ReviewWorkflow } from '../types';
import { assignmentLabel, mixedQueueAfterSuccessfulSave, mixedSegments, mixedTimeForFrame, remapMixedAssignments, replaceMixedBoundaryInInterval, sortedMixedEvidenceCrops, validMixedResolution } from '../utils/mixedPlayersReview';
import { matchTeamName } from '../utils/identityExceptionTeamFilter';
import { formatReviewTime } from '../utils/reviewedOutputPresentation';
import { exactMixedFocusIndex, loadExactMixedFocus, mixedPostSaveDestination, mixedQueueForFocusedCase, reconciledMixedFocusCaseId, type ExactMixedFocusResult, type MixedEntryMode, type MixedNavigationDirection } from '../utils/mixedReviewNavigation';

export type MixedPlayersReviewApi = {
  getBoundaryRefinement: typeof getMixedBoundaryRefinement;
  getFocusedCase: typeof getMixedPlayerReviewCase;
  getQueue: typeof getMixedPlayersReview;
  getWorkflow: typeof getReviewWorkflow;
  reprojectWorkflow: typeof reprojectReviewWorkflow;
  saveResolution: typeof saveMixedPlayerResolution;
};

const defaultReviewApi: MixedPlayersReviewApi = {
  getBoundaryRefinement: getMixedBoundaryRefinement,
  getFocusedCase: getMixedPlayerReviewCase,
  getQueue: getMixedPlayersReview,
  getWorkflow: getReviewWorkflow,
  reprojectWorkflow: reprojectReviewWorkflow,
  saveResolution: saveMixedPlayerResolution,
};

type Props = {
  match: Match;
  workflow: ReviewWorkflow;
  onWorkflowChanged: (workflow: ReviewWorkflow) => void;
  focusCaseId?: string | null;
  entryMode?: MixedEntryMode;
  onReturnToRequired?: () => void;
  onResolveNowComplete?: () => void;
  onLeaveGuard?: (guard: () => boolean) => void;
  reviewApi?: Partial<MixedPlayersReviewApi>;
};

export function MixedPlayersReviewPanel({
  match,
  workflow,
  onWorkflowChanged,
  focusCaseId,
  entryMode = 'manual',
  onReturnToRequired,
  onResolveNowComplete,
  onLeaveGuard,
  reviewApi,
}: Props) {
  const api = useMemo(() => ({ ...defaultReviewApi, ...reviewApi }), [reviewApi]);
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
  const [focusMissing, setFocusMissing] = useState(false);
  const [reprojectFailed, setReprojectFailed] = useState(false);
  const caseNavigationRequestRef = useRef(0);
  void workflow;

  useEffect(() => {
    let cancelled = false;
    caseNavigationRequestRef.current += 1;
    setBusy(true);
    setQueue(null);
    setIndex(0);
    setFocusMissing(false);
    setReprojectFailed(false);
    async function loadInitialQueue() {
      try {
        let value: MixedPlayersReviewQueue | null;
        if (focusCaseId) {
          const focused = await loadExactMixedFocus(
            (caseId) => api.getFocusedCase(match.id, caseId),
            focusCaseId,
          );
          value = focused.kind === 'visible' ? mixedQueueForFocusedCase(focused.response) : null;
        } else {
          value = await api.getQueue(match.id);
        }
        if (cancelled) return;
        if (!value || exactMixedFocusIndex(value.cases.map((item) => item.case_id || ''), focusCaseId) === null) {
          // Resolve-now is exact-source handoff. Never silently fall through
          // to the first unrelated Mixed card if the durable case vanished.
          setQueue(null);
          setFocusMissing(true);
          setMessage('Wybrany przypadek Mixed nie jest już aktualny. Kolejka została odświeżona bez otwierania innego przypadku.');
          return;
        }
        setQueue(value);
      } catch (error) {
        if (!cancelled) setMessage(errorMessage(error));
      } finally {
        if (!cancelled) setBusy(false);
      }
    }
    void loadInitialQueue();
    return () => { cancelled = true; };
  }, [api, focusCaseId, match.id]);

  useEffect(() => {
    if (!focusCaseId || !queue || focusMissing) return;
    const focusedIndex = exactMixedFocusIndex(queue.cases.map((item) => item.case_id || ''), focusCaseId);
    if (focusedIndex !== null) setIndex(focusedIndex);
  }, [focusCaseId, queue]);

  useEffect(() => {
    onLeaveGuard?.(() => !hasUnsavedChanges || window.confirm(
      'Masz niezapisany podział lub przypisania. Przejście do pozostałych przypadków je odrzuci.\n\nWybierz „OK”, aby przejść bez zapisywania.',
    ));
    return () => onLeaveGuard?.(() => true);
  }, [hasUnsavedChanges, onLeaveGuard]);

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
      const value = await api.getBoundaryRefinement(
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
      await api.saveResolution(match.id, {
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
      await api.saveResolution(match.id, {
        candidate_subject_id: reviewCase.candidate_subject_id,
        case_id: reviewCase.case_id,
        source_subject_digest: reviewCase.source_subject_digest,
        resolution: 'unresolved_complex_mix',
      });
      setHasUnsavedChanges(false);
      setMessage('Zapisano jako przypadek bez prostego podziału czasowego. Tożsamość nie została zgadnięta.');
      if (queue && index < queue.cases.length - 1) {
        await materializeAndFocusCase(index + 1, true);
      }
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  function completeResolveNowIntent() {
    if (entryMode === 'resolve_now') onResolveNowComplete?.();
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
    // A temporal split can alter ownership, coverage and Required ordering.
    // It is a structural Review reprojection, not a finalization shortcut.
    setMessage('Odświeżam Review po zapisaniu podziału…');
    let nextWorkflow: ReviewWorkflow;
    try {
      nextWorkflow = await api.reprojectWorkflow(match.id);
    } catch (error) {
      // Persistence has already succeeded. The old local queue is invalid
      // after a structural save, so abandon it rather than pretending the
      // split failed or offering another pre-split card.
      setQueue(null);
      setIndex(0);
      setReprojectFailed(true);
      setMessage(`Podział został zapisany, ale odświeżenie Review nie powiodło się. ${errorMessage(error)}`);
      return;
    }
    onWorkflowChanged(nextWorkflow);
    const normalRemaining = nextWorkflow.issues.normal_blocking ?? 0;
    const mixedRemaining = nextWorkflow.issues.mixed_blocking ?? 0;
    const destination = mixedPostSaveDestination(entryMode, normalRemaining, mixedRemaining);
    if (destination === 'required') {
      onReturnToRequired?.();
      return;
    }
    completeResolveNowIntent();
    if (destination === 'workflow') return;
    const refreshedQueue = await api.getQueue(match.id);
    setQueue(refreshedQueue);
    setIndex(0);
    setMessage(refreshedQueue.cases.length === 0
      ? 'Zapisano podział. Wymagane kolejki Review zostały odświeżone.'
      : 'Zapisano podział. Kolejka Mixed została odświeżona.');
  }

  async function retryStructuralReproject() {
    setBusy(true);
    setMessage('Odświeżam kolejkę Review po zapisanym podziale…');
    try {
      const nextWorkflow = await api.reprojectWorkflow(match.id);
      onWorkflowChanged(nextWorkflow);
      setReprojectFailed(false);
      const normalRemaining = nextWorkflow.issues.normal_blocking ?? 0;
      const mixedRemaining = nextWorkflow.issues.mixed_blocking ?? 0;
      const destination = mixedPostSaveDestination(entryMode, normalRemaining, mixedRemaining);
      if (destination === 'required') {
        onReturnToRequired?.();
        return;
      }
      completeResolveNowIntent();
      if (destination === 'workflow') return;
      const refreshedQueue = await api.getQueue(match.id);
      setQueue(refreshedQueue);
      setIndex(0);
      setMessage('Kolejka Mixed została zsynchronizowana.');
    } catch (error) {
      setMessage(`Podział jest zapisany, ale Review nadal wymaga odświeżenia. ${errorMessage(error)}`);
    } finally {
      setBusy(false);
    }
  }

  function showFocusedCase(
    baseQueue: MixedPlayersReviewQueue,
    targetCaseId: string,
    focused: Extract<ExactMixedFocusResult<Awaited<ReturnType<MixedPlayersReviewApi['getFocusedCase']>>>, { kind: 'visible' }>,
  ) {
    const focusedIndex = exactMixedFocusIndex(
      baseQueue.cases.map((item) => item.case_id || ''),
      targetCaseId,
    );
    if (focusedIndex === null) return false;
    const nextCases = [...baseQueue.cases];
    nextCases[focusedIndex] = focused.case;
    setQueue({
      ...baseQueue,
      assignment_options: focused.response.assignment_options,
      cases: nextCases,
    });
    setIndex(focusedIndex);
    setFocusMissing(false);
    setMessage('');
    return true;
  }

  async function routeAfterEmptyReconciledQueue(refreshedQueue: MixedPlayersReviewQueue) {
    const nextWorkflow = await api.getWorkflow(match.id);
    onWorkflowChanged(nextWorkflow);
    setQueue(refreshedQueue);
    setIndex(0);
    setFocusMissing(false);
    if ((nextWorkflow.issues.normal_blocking ?? 0) > 0) {
      onReturnToRequired?.();
      return;
    }
    setMessage('Kolejka Mixed została zsynchronizowana. Workflow wskaże następny aktualny krok Review.');
  }

  async function reconcileAfterMissingFocusedCase(
    previousQueue: MixedPlayersReviewQueue,
    currentCaseId: string | null,
    attemptedIndex: number,
    direction: MixedNavigationDirection,
    requestId: number,
  ) {
    // This is deliberately bounded: one full authoritative reconciliation,
    // followed by at most one exact focused read of its selected case.
    const refreshedQueue = await api.getQueue(match.id);
    if (requestId !== caseNavigationRequestRef.current) return;
    const nextCaseId = reconciledMixedFocusCaseId(
      previousQueue.cases.map((item) => item.case_id || ''),
      currentCaseId,
      attemptedIndex,
      refreshedQueue.cases.map((item) => item.case_id || ''),
      direction,
    );
    if (!nextCaseId) {
      await routeAfterEmptyReconciledQueue(refreshedQueue);
      return;
    }
    const focused = await loadExactMixedFocus(
      (caseId) => api.getFocusedCase(match.id, caseId),
      nextCaseId,
    );
    if (requestId !== caseNavigationRequestRef.current) return;
    if (focused.kind !== 'visible' || !showFocusedCase(refreshedQueue, nextCaseId, focused)) {
      setQueue(null);
      setFocusMissing(true);
      setMessage('Kolejka Mixed zmieniła się ponownie podczas jednokrotnej synchronizacji. Odśwież Review przed dalszą pracą.');
    }
  }

  async function materializeAndFocusCase(nextIndex: number, discardCurrent = false) {
    if (!queue) return;
    const previousQueue = queue;
    const currentCaseId = reviewCase?.case_id || null;
    const direction: MixedNavigationDirection = nextIndex < index ? 'previous' : 'next';
    const boundedIndex = Math.max(0, Math.min(previousQueue.cases.length - 1, nextIndex));
    const targetCaseId = previousQueue.cases[boundedIndex]?.case_id;
    if (!targetCaseId) {
      setFocusMissing(true);
      setMessage('Nie można ustalić dokładnego przypadku Mixed do otwarcia.');
      return;
    }
    const requestId = ++caseNavigationRequestRef.current;
    setBusy(true);
    if (discardCurrent) {
      setQueue(null);
      setIndex(0);
    }
    setMessage('Przygotowuję widoki wybranego przypadku…');
    try {
      const focused = await loadExactMixedFocus(
        (caseId) => api.getFocusedCase(match.id, caseId),
        targetCaseId,
      );
      if (requestId !== caseNavigationRequestRef.current) return;
      if (focused.kind === 'membership_changed' && entryMode === 'manual') {
        setMessage('Synchronizuję aktualną kolejkę Mixed…');
        await reconcileAfterMissingFocusedCase(
          previousQueue,
          currentCaseId,
          boundedIndex,
          direction,
          requestId,
        );
        return;
      }
      if (focused.kind !== 'visible') {
        setQueue(null);
        setFocusMissing(true);
        setMessage('Wybrany przypadek Mixed zniknął z aktualnej kolejki. Nie otwarto innego przypadku.');
        return;
      }
      // The backend has now materialized this exact case's missing evidence.
      // Only at this point may it become the visible review card.
      if (!showFocusedCase(previousQueue, targetCaseId, focused)) {
        setQueue(null);
        setFocusMissing(true);
        setMessage('Wybrany przypadek Mixed nie należy już do lokalnej kolejki.');
      }
    } catch (error) {
      if (requestId === caseNavigationRequestRef.current) {
        if (discardCurrent) setFocusMissing(true);
        setMessage(errorMessage(error));
      }
    } finally {
      if (requestId === caseNavigationRequestRef.current) setBusy(false);
    }
  }

  function navigateTo(nextIndex: number) {
    if (!queue || busy || nextIndex === index) return;
    if (hasUnsavedChanges && !window.confirm(
      'Masz niezapisany podział lub przypisania. Przejście do innego przypadku je odrzuci.\n\nWybierz „OK”, aby przejść bez zapisywania.',
    )) return;
    void materializeAndFocusCase(nextIndex);
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
  }, [busy, hasUnsavedChanges, index, queue]);

  if (busy && !queue) return <p className='loading-line'><span className='spinner' /> Ładuję zmieszane przypadki…</p>;
  if (focusMissing) return <section className='identity-exception-review'><div className='status error'><strong>Nie można bezpiecznie otworzyć wskazanego przypadku Mixed.</strong><p>{message}</p></div></section>;
  if (reprojectFailed) return <section className='identity-exception-review'><div className='status error'><strong>Podział został zapisany, ale kolejka Review wymaga odświeżenia.</strong><p>{message}</p><button type='button' onClick={() => void retryStructuralReproject()} disabled={busy}>Spróbuj odświeżyć Review</button></div></section>;
  if (!reviewCase || !queue) return <section className='identity-exception-review'><div className='status'>Brak zmieszanych przypadków do rozdzielenia.</div>{message && <p className='status'>{message}</p>}</section>;
  if (reviewCase.scope_status === 'stale_or_unclassifiable_blocking') return <section className='identity-exception-review mixed-player-review'>
    <div className='status'><strong>Nie można bezpiecznie odtworzyć źródła Mixed.</strong><p>Przypadek nadal blokuje Review. Odśwież lub uruchom bezpieczne przeliczenie Review; nie przypisuj tej historycznej własności na podstawie niepełnych danych.</p></div>
  </section>;

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
