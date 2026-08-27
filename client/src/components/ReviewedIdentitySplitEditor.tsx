import { useEffect, useMemo, useState } from 'react';

import { artifactUrl, getConcurrentLaneRefinement, getReviewedCorrectionContext, getReviewedHistoricalSplitRepairContext, getReviewedIdentityTemporalSplitRefinement, saveReviewedIdentityTemporalSplit } from '../api';
import { isRecoverableConcurrentLaneConflict, isTemporalSplitNotSeparable } from '../lib/apiErrors';
import { errorMessage } from '../lib/helpers';
import type {
  MixedBoundaryRefinement,
  ConcurrentLaneResolution,
  MixedSegmentAssignment,
  ReviewedCorrectionContext,
  ReviewedTemporalSplitResponse,
  Team,
} from '../types';
import {
  assignmentLabel,
  mixedSegments,
  remapMixedAssignments,
  replaceMixedBoundaryInInterval,
  sortedMixedEvidenceCrops,
  toggleMixedBoundary,
  validMixedResolution,
} from '../utils/mixedPlayersReview';
import {
  reviewedIdentityChildActions,
} from '../utils/reviewedIdentityActions';
import { matchTeamName } from '../utils/identityExceptionTeamFilter';
import { teamLabelForOperator } from '../utils/reviewedOutputPresentation';
import { correctionContextAsSplitCase } from '../utils/reviewedIdentitySplitCase';
import { MixedTemporalTopologyLanes } from './MixedTemporalTopologyLanes';
import { ConcurrentMixedResolver } from './ConcurrentMixedResolver';
import { MixedRefinementBoundaryEvidence } from './MixedRefinementBoundaryEvidence';

type Props = {
  matchId: string;
  context: ReviewedCorrectionContext;
  teams?: Team[];
  onCancel: () => void;
  onSaved: (result: ReviewedTemporalSplitResponse) => void;
};

const CHILD_ACTIONS = reviewedIdentityChildActions();

export function ReviewedIdentitySplitEditor({ matchId, context: suppliedContext, teams, onCancel, onSaved }: Props) {
  const [context, setContext] = useState(suppliedContext);
  const reviewCase = useMemo(() => correctionContextAsSplitCase(context), [context]);
  const [boundaries, setBoundaries] = useState<number[]>([]);
  const [assignments, setAssignments] = useState<Array<MixedSegmentAssignment | null>>([null]);
  const [selectedSegment, setSelectedSegment] = useState(0);
  const [refinement, setRefinement] = useState<MixedBoundaryRefinement | null>(null);
  const [refinementBusy, setRefinementBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [topologyRejected, setTopologyRejected] = useState(false);
  const [concurrentRecoveryRevision, setConcurrentRecoveryRevision] = useState(0);
  const [concurrentDirty, setConcurrentDirty] = useState(false);
  const crops = useMemo(() => sortedMixedEvidenceCrops(reviewCase.temporal_evidence.anchor_crops), [reviewCase]);
  const segments = useMemo(() => mixedSegments(reviewCase, boundaries), [reviewCase, boundaries]);
  const persistedSplit = context.temporal_split?.resolution_status === 'resolved'
    ? context.temporal_split
    : null;
  const simpleSplitAllowed = reviewCase.temporal_topology?.simple_split_allowed === true
    && !topologyRejected;
  const persistedSplitSignature = JSON.stringify(persistedSplit || {
    split_after_frames: [], segment_assignments: [null],
  });
  const operatorTeamName = (teamLabel: string | null | undefined) => (
    teamLabel === 'A' || teamLabel === 'B'
      ? matchTeamName(teams || [], teamLabel)
      : teamLabelForOperator(teamLabel)
  );
  const rosterOptionsByTeam = useMemo(() => ({
    A: context.roster_options.filter((player) => player.team_label === 'A'),
    B: context.roster_options.filter((player) => player.team_label === 'B'),
  }), [context.roster_options]);

  useEffect(() => {
    setContext(suppliedContext);
  }, [suppliedContext]);

  useEffect(() => {
    setTopologyRejected(false);
    if (!persistedSplit) {
      setBoundaries([]);
      setAssignments([null]);
      return;
    }
    setBoundaries(persistedSplit.split_after_frames);
    setAssignments(persistedSplit.segment_assignments);
    setSelectedSegment(0);
  }, [context.source_ownership_digest, persistedSplitSignature]);

  const savedState = useMemo(() => JSON.stringify({
    boundaries: persistedSplit?.split_after_frames || [],
    assignments: persistedSplit?.segment_assignments || [null],
  }), [persistedSplit]);
  const hasUnsavedChanges = concurrentDirty || JSON.stringify({ boundaries, assignments }) !== savedState;

  function updateBoundaries(next: number[]) {
    const remapped = remapMixedAssignments(reviewCase, boundaries, next, assignments);
    if (remapped.requiresConfirmation && !window.confirm(
      'Zmiana podziału zmienia zakres fragmentów. Niejednoznaczne przypisania zostaną wyczyszczone.',
    )) return;
    setBoundaries(next);
    setAssignments(remapped.assignments);
    setSelectedSegment(Math.min(selectedSegment, next.length));
    setError(remapped.requiresConfirmation
      ? 'Zmieniono granicę. Przypisz ponownie fragmenty, których zakres się zmienił.'
      : '');
  }

  function setAssignment(next: MixedSegmentAssignment) {
    setAssignments((current) => current.map((value, index) => index === selectedSegment ? next : value));
    setError('');
  }

  function applyBoundary(next: number[]) {
    updateBoundaries(next);
  }

  async function openRefinement(afterFrame: number, beforeFrame: number) {
    if (!context.source_ownership_digest || !simpleSplitAllowed) return;
    setRefinementBusy(true);
    setError('');
    try {
      const value = await getReviewedIdentityTemporalSplitRefinement(matchId, {
        candidate_subject_id: context.candidate_subject_id,
        review_target_id: context.review_target_id || undefined,
        continuity_group_id: context.continuity_group_id || undefined,
        source_ownership_digest: context.source_ownership_digest,
        after_frame: afterFrame,
        before_frame: beforeFrame,
      });
      if (value.anchor_crops.length < 2) {
        applyBoundary(replaceMixedBoundaryInInterval(boundaries, afterFrame, beforeFrame, afterFrame));
      } else {
        setRefinement(value);
      }
    } catch (reason) {
      if (isTemporalSplitNotSeparable(reason)) {
        rejectStaleTemporalTopology();
      } else {
        setError(errorMessage(reason));
      }
    } finally {
      setRefinementBusy(false);
    }
  }

  function selectRefinedBoundary(frame: number) {
    if (!refinement) return;
    applyBoundary(replaceMixedBoundaryInInterval(
      boundaries,
      refinement.after_frame,
      refinement.before_frame,
      frame,
    ));
    setRefinement(null);
  }

  function cancel() {
    if (hasUnsavedChanges && !window.confirm(
      'Masz niezapisany podział. Wrócić bez zapisywania?',
    )) return;
    onCancel();
  }

  async function save() {
    if (!simpleSplitAllowed || !validMixedResolution(reviewCase, boundaries, assignments) || !context.source_ownership_digest) return;
    setBusy(true);
    setError('');
    try {
      const result = await saveReviewedIdentityTemporalSplit(matchId, {
        candidate_subject_id: context.candidate_subject_id,
        review_target_id: context.review_target_id || undefined,
        continuity_group_id: context.continuity_group_id || undefined,
        source_ownership_digest: context.source_ownership_digest,
        existing_split_semantic_digest: persistedSplit?.split_semantic_digest || undefined,
        resolution: 'split',
        split_after_frames: boundaries,
        segment_assignments: assignments.filter((value): value is MixedSegmentAssignment => value !== null),
        review_state_version: context.review_state_version,
      });
      onSaved(result);
    } catch (reason) {
      if (isTemporalSplitNotSeparable(reason)) {
        rejectStaleTemporalTopology();
      } else {
        setError(errorMessage(reason));
      }
    } finally {
      setBusy(false);
    }
  }

  async function saveComplex() {
    if (!context.source_ownership_digest) return;
    setBusy(true);
    setError('');
    try {
      const result = await saveReviewedIdentityTemporalSplit(matchId, {
        candidate_subject_id: context.candidate_subject_id,
        review_target_id: context.review_target_id || undefined,
        continuity_group_id: context.continuity_group_id || undefined,
        source_ownership_digest: context.source_ownership_digest,
        existing_split_semantic_digest: persistedSplit?.split_semantic_digest || undefined,
        resolution: 'unresolved_complex_mix',
        review_state_version: context.review_state_version,
      });
      onSaved(result);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function saveConcurrent(resolutions: ConcurrentLaneResolution[]) {
    if (!context.source_ownership_digest || topologyRejected) return;
    setBusy(true);
    setError('');
    try {
      const result = await saveReviewedIdentityTemporalSplit(matchId, {
        candidate_subject_id: context.candidate_subject_id,
        review_target_id: context.review_target_id || undefined,
        continuity_group_id: context.continuity_group_id || undefined,
        source_ownership_digest: context.source_ownership_digest,
        existing_resolution_semantic_digest: context.concurrent_resolution?.resolution_semantic_digest || undefined,
        existing_split_semantic_digest: context.temporal_split?.split_semantic_digest || undefined,
        resolution: 'concurrent_lanes',
        lane_resolutions: resolutions,
        review_state_version: context.review_state_version,
      });
      onSaved(result);
    } catch (reason) {
      if (isRecoverableConcurrentLaneConflict(reason)) {
        await recoverConcurrentContext();
      } else {
        setError(errorMessage(reason));
      }
    } finally {
      setBusy(false);
    }
  }

  async function recoverConcurrentContext() {
    setTopologyRejected(true);
    setConcurrentDirty(false);
    setConcurrentRecoveryRevision((value) => value + 1);
    try {
      const fresh = context.historical_concurrent_repair
        ? await getReviewedHistoricalSplitRepairContext(
          matchId,
          context.concurrent_resolution?.parent_case_id || '',
        )
        : await getReviewedCorrectionContext(
          matchId,
          context.candidate_subject_id,
          context.review_target_id,
        );
      const sameTarget = fresh.candidate_subject_id === context.candidate_subject_id
        && (fresh.review_target_id || null) === (context.review_target_id || null);
      if (
        !sameTarget
        || fresh.temporal_topology?.kind !== 'concurrent'
        || !fresh.concurrent_resolution
      ) throw new Error('Dokładny przypadek nie jest już aktualnym przypadkiem równoległym.');
      setContext(fresh);
      setTopologyRejected(false);
      setError('Układ ścieżek został zaktualizowany. Wprowadź przypisania ponownie na podstawie aktualnego materiału.');
    } catch {
      setError(context.historical_concurrent_repair
        ? 'Pierwotny podział zmienił się i nie można go już bezpiecznie otworzyć. Odśwież Review.'
        : 'Układ ścieżek zmienił się i nie udało się pobrać aktualnego przypadku. Nie zapisano żadnych przypisań; zamknij edytor i odśwież Review.');
    }
  }

  function rejectStaleTemporalTopology() {
    setTopologyRejected(true);
    setBoundaries([]);
    setAssignments([]);
    setSelectedSegment(0);
    setRefinement(null);
    setError('Ten materiał nie ma już prostego podziału czasowego, ponieważ tracklety nakładają się w czasie. Zamknij i otwórz przypadek ponownie albo oznacz go jako brak prostego podziału.');
  }

  const selected = segments[selectedSegment];
  if (
    reviewCase.temporal_topology?.kind === 'concurrent'
    && context.concurrent_resolution
    && !topologyRejected
  ) return <ConcurrentMixedResolver
    key={`${reviewCase.case_id || context.candidate_subject_id}:${context.source_ownership_digest}`}
    match={{ id: matchId, teams: teams || [] }}
    reviewCase={{ ...reviewCase, concurrent_resolution: context.concurrent_resolution }}
    assignmentOptions={{ roster: context.roster_options, slots: context.slot_options }}
    busy={busy}
    historicalRepair={context.historical_concurrent_repair}
    statusMessage={error}
    recoveryRevision={concurrentRecoveryRevision}
    onDirtyChange={setConcurrentDirty}
    onSave={saveConcurrent}
    onDefer={async () => { await saveComplex(); }}
    onCancel={cancel}
    loadRefinement={(lane, afterFrame, beforeFrame) => getConcurrentLaneRefinement(matchId, {
      candidate_subject_id: context.candidate_subject_id,
      parent_case_id: context.concurrent_resolution?.parent_case_id || reviewCase.case_id || context.candidate_subject_id,
      parent_source_digest: context.concurrent_resolution?.parent_source_digest || context.source_ownership_digest || '',
      lane_id: lane.lane_id,
      lane_source_digest: lane.source_ownership_digest,
      after_frame: afterFrame,
      before_frame: beforeFrame,
      review_target_id: context.review_target_id || undefined,
      continuity_group_id: context.continuity_group_id || undefined,
    })}
    onRecoverableRefinementConflict={recoverConcurrentContext}
  />;
  return <section className='reviewed-inline-split' aria-label='Podział kilku zawodników'>
    <header><strong>{simpleSplitAllowed ? 'To kilku zawodników — podziel' : 'Równoległe tracklety'}</strong><p>{simpleSplitAllowed ? 'Podział obejmie wyłącznie dokładnie pokazane obserwacje.' : 'Ten materiał nie może być bezpiecznie rozdzielony jedną granicą czasu.'}</p></header>
    {!simpleSplitAllowed && reviewCase.temporal_topology?.kind === 'concurrent' && <MixedTemporalTopologyLanes matchId={matchId} reviewCase={reviewCase} />}
    {!simpleSplitAllowed && reviewCase.temporal_topology?.kind !== 'concurrent' && <div className='mixed-topology-warning' role='alert'><strong>Nie można potwierdzić bezpiecznej topologii czasowej.</strong><span>Zamknij i otwórz przypadek ponownie albo pozostaw go jako brak prostego podziału.</span></div>}
    {simpleSplitAllowed && <>
      <div className='mixed-temporal-strip'>
      {crops.map((crop, index) => <div className='mixed-crop-group' key={crop.anchor_crop_id}>
        <figure className={`team-${(crop.team_label || 'u').toLowerCase()}`}>
          <img src={artifactUrl(matchId, crop.artifact)} alt='Widok fragmentu w kolejności czasu' />
          <figcaption>{crop.time_sec?.toFixed(1) ?? `Klatka ${crop.frame}`}</figcaption>
        </figure>
        {index < crops.length - 1 && (() => {
          const nextCrop = crops[index + 1];
          const hasBoundaryInInterval = boundaries.some((frame) => frame >= crop.frame && frame < nextCrop.frame);
          const needsRefinement = Boolean(context.detected_observation_count && context.detected_observation_count > crops.length);
          return <button
          type='button'
          className={hasBoundaryInInterval ? 'split-boundary active' : 'split-boundary'}
          onClick={() => needsRefinement
            ? void openRefinement(crop.frame, nextCrop.frame)
            : applyBoundary(toggleMixedBoundary(boundaries, crop.frame))}
          disabled={busy || refinementBusy}
        >{hasBoundaryInInterval ? 'Zmień podział' : needsRefinement ? 'Doprecyzuj' : 'Podziel tutaj'}</button>;
        })()}
      </div>)}
      </div>
    {refinement && <section className='mixed-boundary-refinement' aria-label='Doprecyzowanie granicy podziału'>
      <header><strong>Doprecyzuj moment przejścia</strong><button type='button' className='secondary' onClick={() => setRefinement(null)}>Zamknij</button></header>
      <p>Wybierz dokładną granicę między sąsiednimi widokami. Nie jest ograniczona do 12 widoków głównych.</p>
      <MixedRefinementBoundaryEvidence matchId={matchId} refinement={refinement} />
      <div className='mixed-refinement-strip'>
        <button type='button' className='mixed-refinement-leading-action' onClick={() => selectRefinedBoundary(refinement.after_frame)} disabled={busy}>Podziel zaraz po poprzednim widoku</button>
        {sortedMixedEvidenceCrops(refinement.anchor_crops).map((crop, index, refinedCrops) => <div className='mixed-refinement-crop' key={crop.anchor_crop_id}>
          <figure className={`team-${(crop.team_label || 'u').toLowerCase()}`}><img src={artifactUrl(matchId, crop.artifact)} alt='Dokładniejszy widok przejścia między osobami' /><figcaption>{crop.time_sec?.toFixed(1) ?? `Klatka ${crop.frame}`}</figcaption></figure>
          {index < refinedCrops.length - 1 && <button type='button' onClick={() => selectRefinedBoundary(crop.frame)} disabled={busy}>Ustaw tutaj</button>}
        </div>)}
      </div>
      {boundaries.some((frame) => frame >= refinement.after_frame && frame < refinement.before_frame) && <button type='button' className='secondary' onClick={() => {
        applyBoundary(boundaries.filter((frame) => frame < refinement.after_frame || frame >= refinement.before_frame));
        setRefinement(null);
      }} disabled={busy}>Usuń podział z tego przedziału</button>}
    </section>}
    <div className='mixed-segment-list'>
      {segments.map((segment, index) => <button type='button' key={segment.index}
        className={selectedSegment === index ? 'mixed-segment selected' : 'mixed-segment'}
        onClick={() => setSelectedSegment(index)} disabled={busy}>
        <strong>Fragment {index + 1}</strong>
        <span>Klatki {segment.frameStart}–{segment.frameEnd}</span>
        <span>{assignmentLabel(
          assignments[index],
          undefined,
          assignments[index]?.team_label ? operatorTeamName(assignments[index]?.team_label) : undefined,
        )}</span>
      </button>)}
    </div>
      {selected && <section className='reviewed-split-child-decision'>
      <h5>Przypisz fragment {selected.index + 1}</h5>
      <div className='reviewed-action-cards'>
        {CHILD_ACTIONS.filter((card) => context.action_capabilities[card.action]?.allowed === true).map((card) => <button type='button' key={card.action} disabled={busy}
          className={assignments[selectedSegment]?.action === card.action ? 'reviewed-action-card selected' : 'reviewed-action-card'}
          onClick={() => {
            if (card.action === 'assign_roster_player') return setAssignment({ action: 'assign_roster_player' });
            if (card.action === 'assign_team') return setAssignment({ action: 'assign_team', team_label: context.effective_team_label === 'B' ? 'B' : 'A' });
            if (card.action === 'assign_existing_slot') return setAssignment({ action: 'assign_existing_slot' });
            if (card.action === 'create_new_stable_player') return setAssignment({ action: 'create_new_stable_player', team_label: context.effective_team_label === 'B' ? 'B' : 'A' });
            setAssignment({ action: card.action });
          }}>{card.label}</button>)}
      </div>
      {assignments[selectedSegment]?.action === 'assign_roster_player' || !assignments[selectedSegment] ? <label>Zawodnik z kadry
        <select value={assignments[selectedSegment]?.player_id || ''} disabled={busy}
          onChange={(event) => setAssignment({ action: 'assign_roster_player', player_id: event.target.value })}>
          <option value=''>Wybierz zawodnika</option>
          {(['A', 'B'] as const).map((teamLabel) => rosterOptionsByTeam[teamLabel].length > 0 && <optgroup
            key={teamLabel}
            label={operatorTeamName(teamLabel)}
          >
            {rosterOptionsByTeam[teamLabel].map((player) => <option key={player.player_id} value={player.player_id}>
              {player.player_name}{player.roster_number ? ` #${player.roster_number}` : ''}
            </option>)}
          </optgroup>)}
        </select>
      </label> : null}
      {assignments[selectedSegment]?.action === 'assign_team' && <label>Drużyna
        <select value={assignments[selectedSegment]?.team_label || ''} disabled={busy}
          onChange={(event) => setAssignment({ action: 'assign_team', team_label: event.target.value })}>
          <option value='A'>{operatorTeamName('A')} — zawodnik nieznany</option>
          <option value='B'>{operatorTeamName('B')} — zawodnik nieznany</option>
        </select>
      </label>}
      {assignments[selectedSegment]?.action === 'assign_existing_slot' && <label>Istniejący zawodnik
        <select value={assignments[selectedSegment]?.stable_slot_id || ''} disabled={busy}
          onChange={(event) => setAssignment({ action: 'assign_existing_slot', stable_slot_id: event.target.value })}>
          <option value=''>Wybierz zawodnika</option>
          {context.slot_options.map((slot) => <option key={slot.stable_slot_id} value={slot.stable_slot_id}>
            {slot.stable_slot_id} · {operatorTeamName(slot.team_label)}
          </option>)}
        </select>
      </label>}
      {assignments[selectedSegment]?.action === 'create_new_stable_player' && <label>Drużyna nowego zawodnika
        <select value={assignments[selectedSegment]?.team_label || ''} disabled={busy}
          onChange={(event) => setAssignment({ action: 'create_new_stable_player', team_label: event.target.value })}>
          <option value='A'>{operatorTeamName('A')}</option>
          <option value='B'>{operatorTeamName('B')}</option>
        </select>
      </label>}
      </section>}
    </>}
    {error && <p className='status error'>{error}</p>}
    <footer className='row'>
      {simpleSplitAllowed && <button type='button' onClick={() => void save()} disabled={busy || !validMixedResolution(reviewCase, boundaries, assignments)}>Zapisz podział + następny</button>}
      <button type='button' className='secondary' onClick={() => void saveComplex()} disabled={busy}>Nie da się bezpiecznie podzielić czasowo</button>
      <button type='button' className='secondary' onClick={cancel} disabled={busy}>Wróć bez zapisu</button>
    </footer>
  </section>;
}
