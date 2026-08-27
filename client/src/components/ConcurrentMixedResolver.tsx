import { useEffect, useMemo, useRef, useState } from 'react';

import { artifactUrl } from '../api';
import type {
  ConcurrentLaneRefinement,
  ConcurrentLaneResolution,
  ConcurrentMixedLane,
  Match,
  MixedPlayerCase,
  MixedPlayersReviewQueue,
  MixedSegmentAssignment,
} from '../types';
import {
  assignmentLabel,
  mixedSegments,
  mixedTimeForFrame,
  remapMixedAssignments,
  replaceMixedBoundaryInInterval,
  sortedMixedEvidenceCrops,
  toggleMixedBoundary,
} from '../utils/mixedPlayersReview';
import { matchTeamName } from '../utils/identityExceptionTeamFilter';
import { formatReviewTime } from '../utils/reviewedOutputPresentation';
import { MixedAssignmentControls } from './MixedAssignmentControls';

type Props = {
  match: Pick<Match, 'id' | 'teams'>;
  reviewCase: MixedPlayerCase;
  assignmentOptions: MixedPlayersReviewQueue['assignment_options'];
  caseNumber?: number;
  caseTotal?: number;
  busy: boolean;
  historicalRepair?: boolean;
  statusMessage?: string;
  onDirtyChange: (dirty: boolean) => void;
  onSave: (resolutions: ConcurrentLaneResolution[]) => Promise<void>;
  onDefer: () => Promise<void>;
  onCancel?: () => void;
  onPrevious?: () => void;
  onNext?: () => void;
  previousDisabled?: boolean;
  nextDisabled?: boolean;
  loadRefinement: (
    lane: ConcurrentMixedLane,
    afterFrame: number,
    beforeFrame: number,
  ) => Promise<ConcurrentLaneRefinement>;
};

type ConcurrentLaneDraft =
  | {
    lane_id: string;
    lane_source_digest: string;
    resolution: 'direct';
    assignment: MixedSegmentAssignment;
  }
  | {
    lane_id: string;
    lane_source_digest: string;
    resolution: 'temporal_split';
    split_after_frames: number[];
    segment_assignments: Array<MixedSegmentAssignment | null>;
  };

type DraftMap = Record<string, ConcurrentLaneDraft | undefined>;

export function ConcurrentMixedResolver({
  match,
  reviewCase,
  assignmentOptions,
  caseNumber,
  caseTotal,
  busy,
  historicalRepair = false,
  statusMessage = '',
  onDirtyChange,
  onSave,
  onDefer,
  onCancel,
  onPrevious,
  onNext,
  previousDisabled,
  nextDisabled,
  loadRefinement,
}: Props) {
  const lanes = reviewCase.concurrent_resolution?.lanes || [];
  const initialDrafts = useMemo(() => Object.fromEntries(
    lanes.flatMap((lane) => lane.current_resolution
      ? [[lane.lane_id, lane.current_resolution] as const]
      : []),
  ), [reviewCase.case_id, reviewCase.source_subject_digest]);
  const [drafts, setDrafts] = useState<DraftMap>(initialDrafts);
  const [selectedLaneId, setSelectedLaneId] = useState(
    lanes.find((lane) => !lane.current_resolution)?.lane_id || lanes[0]?.lane_id || '',
  );
  const [splitLaneId, setSplitLaneId] = useState<string | null>(null);
  const [selectedSegment, setSelectedSegment] = useState(0);
  const [refinement, setRefinement] = useState<ConcurrentLaneRefinement | null>(null);
  const [refinementBusy, setRefinementBusy] = useState(false);
  const [message, setMessage] = useState('');
  const saveInFlightRef = useRef(false);
  const dirty = JSON.stringify(drafts) !== JSON.stringify(initialDrafts);
  const selectedLane = lanes.find((lane) => lane.lane_id === selectedLaneId) || lanes[0];
  const splitLane = lanes.find((lane) => lane.lane_id === splitLaneId) || null;
  const completed = lanes.filter((lane) => completeLaneResolution(lane, drafts[lane.lane_id])).length;

  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);

  useEffect(() => {
    setDrafts(initialDrafts);
    setSelectedLaneId(
      lanes.find((lane) => !lane.current_resolution)?.lane_id || lanes[0]?.lane_id || '',
    );
    setSplitLaneId(null);
    setSelectedSegment(0);
    setRefinement(null);
    setMessage('');
  }, [initialDrafts, reviewCase.case_id, reviewCase.source_subject_digest]);

  function chooseLane(laneId: string) {
    setSelectedLaneId(laneId);
    setSplitLaneId(null);
    setRefinement(null);
    setSelectedSegment(0);
  }

  function assignLane(assignment: MixedSegmentAssignment) {
    if (!selectedLane) return;
    setDrafts((current) => ({
      ...current,
      [selectedLane.lane_id]: {
        lane_id: selectedLane.lane_id,
        lane_source_digest: selectedLane.source_ownership_digest,
        resolution: 'direct',
        assignment,
      },
    }));
    const next = lanes.find((lane) => lane.lane_id !== selectedLane.lane_id && !completeLaneResolution(lane, drafts[lane.lane_id]));
    if (next) setSelectedLaneId(next.lane_id);
  }

  function openLaneSplit() {
    if (!selectedLane) return;
    setDrafts((current) => ({
      ...current,
      [selectedLane.lane_id]: current[selectedLane.lane_id]?.resolution === 'temporal_split'
        ? current[selectedLane.lane_id]
        : {
          lane_id: selectedLane.lane_id,
          lane_source_digest: selectedLane.source_ownership_digest,
          resolution: 'temporal_split',
          split_after_frames: [],
          segment_assignments: [],
        },
    }));
    setSplitLaneId(selectedLane.lane_id);
    setSelectedSegment(0);
    setRefinement(null);
  }

  function updateLaneBoundaries(lane: ConcurrentMixedLane, next: number[]): boolean {
    const current = drafts[lane.lane_id];
    const previousBoundaries = current?.resolution === 'temporal_split'
      ? current.split_after_frames
      : [];
    const previousAssignments = current?.resolution === 'temporal_split'
      ? current.segment_assignments
      : [];
    const remapped = remapMixedAssignments(
      laneAsMixedCase(reviewCase, lane),
      previousBoundaries,
      next,
      previousAssignments,
    );
    if (remapped.requiresConfirmation && !window.confirm(
      'Zmiana podziału zmienia zakres fragmentów tej ścieżki. Niejednoznaczne przypisania zostaną wyczyszczone.',
    )) return false;
    setDrafts((values) => ({
      ...values,
      [lane.lane_id]: {
        lane_id: lane.lane_id,
        lane_source_digest: lane.source_ownership_digest,
        resolution: 'temporal_split',
        split_after_frames: next,
        segment_assignments: remapped.assignments,
      },
    }));
    setSelectedSegment(Math.min(selectedSegment, next.length));
    setMessage(remapped.requiresConfirmation
      ? 'Zmieniono granicę. Przypisz ponownie zmienione fragmenty tej ścieżki.'
      : '');
    return true;
  }

  function assignLaneSegment(assignment: MixedSegmentAssignment) {
    if (!splitLane) return;
    const current = drafts[splitLane.lane_id];
    if (!current || current.resolution !== 'temporal_split') return;
    const expected = current.split_after_frames.length + 1;
    const assignments = Array.from({ length: expected }, (_, index) => current.segment_assignments[index] || null);
    assignments[selectedSegment] = assignment;
    setDrafts((values) => ({
      ...values,
      [splitLane.lane_id]: {
        ...current,
        segment_assignments: assignments,
      },
    }));
    const next = assignments.findIndex((value, index) => index > selectedSegment && value === null);
    if (next >= 0) setSelectedSegment(next);
  }

  async function refineBoundary(lane: ConcurrentMixedLane, afterFrame: number, beforeFrame: number) {
    setRefinementBusy(true);
    setMessage('');
    try {
      setRefinement(await loadRefinement(lane, afterFrame, beforeFrame));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setRefinementBusy(false);
    }
  }

  async function submitAll() {
    if (saveInFlightRef.current || completed !== lanes.length) return;
    saveInFlightRef.current = true;
    try {
      await onSave(lanes.map((lane) => persistedResolution(drafts[lane.lane_id])).filter(
        (value): value is ConcurrentLaneResolution => Boolean(value),
      ));
    } finally {
      saveInFlightRef.current = false;
    }
  }

  async function deferCurrentCase() {
    if (saveInFlightRef.current) return;
    saveInFlightRef.current = true;
    try {
      await onDefer();
    } finally {
      saveInFlightRef.current = false;
    }
  }

  if (!selectedLane || lanes.length === 0) {
    return <div className='status error'>Nie udało się odtworzyć dokładnych ścieżek tego przypadku.</div>;
  }
  if (splitLane) {
    return <ConcurrentLaneSplitEditor
      match={match}
      reviewCase={reviewCase}
      lane={splitLane}
      draft={drafts[splitLane.lane_id]}
      assignmentOptions={assignmentOptions}
      selectedSegment={selectedSegment}
      refinement={refinement}
      busy={busy || refinementBusy}
      onBack={() => { setSplitLaneId(null); setRefinement(null); }}
      onSelectSegment={setSelectedSegment}
      onAssign={assignLaneSegment}
      onBoundaries={(next) => { updateLaneBoundaries(splitLane, next); }}
      onRefine={(after, before) => void refineBoundary(splitLane, after, before)}
      onSelectRefined={(frame) => {
        const current = drafts[splitLane.lane_id];
        if (current?.resolution === 'temporal_split' && refinement) {
          const applied = updateLaneBoundaries(splitLane, replaceMixedBoundaryInInterval(
            current.split_after_frames,
            refinement.after_frame,
            refinement.before_frame,
            frame,
          ));
          if (applied) setRefinement(null);
        }
      }}
      onRemoveRefined={() => {
        const current = drafts[splitLane.lane_id];
        if (current?.resolution !== 'temporal_split' || !refinement) return;
        const applied = updateLaneBoundaries(
          splitLane,
          current.split_after_frames.filter(
            (frame) => frame < refinement.after_frame || frame >= refinement.before_frame,
          ),
        );
        if (applied) setRefinement(null);
      }}
      message={message}
    />;
  }

  return <section className='concurrent-mixed-resolver' aria-label='Przypisywanie równoległych zawodników'>
    <header className='concurrent-resolver-header'>
      <div>
        <p className='eyebrow'>Zmieszani gracze</p>
        <h2>Przypisz równoległych zawodników</h2>
        <p>W tym fragmencie system śledzi kilka osób jednocześnie. Przejrzyj każdą ścieżkę i przypisz ją do właściwego zawodnika.</p>
      </div>
      <div className='identity-exception-case-context'>
        {caseNumber && caseTotal && <span className='reviewed-status-badge'>Przypadek {caseNumber} z {caseTotal}</span>}
        <span>{lanes.length} ścieżki • {timeRange(reviewCase, reviewCase.frame_start, reviewCase.frame_end)}</span>
      </div>
    </header>
    {reviewCase.reviewed_complex && <div className='mixed-complex-reviewed' role='status'>
      <strong>Ten przypadek był wcześniej oznaczony jako złożony.</strong>
      <span>Możesz teraz przypisać równoległe ścieżki osobno.</span>
    </div>}
    {historicalRepair && <div className='mixed-topology-warning' role='status'>
      <strong>Historyczny podział czasowy nie jest już uznawany za bezpieczny.</strong>
      <span>Poprzednia decyzja pozostanie bez zmian, dopóki jawnie nie zapiszesz naprawionych przypisań równoległych.</span>
    </div>}
    {statusMessage && <p className='status' role='status'>{statusMessage}</p>}
    <div className='concurrent-resolver-workspace'>
      <div className='concurrent-lane-list' aria-label='Ścieżki zawodników'>
        {lanes.map((lane, laneIndex) => <ConcurrentLaneCard
          key={lane.lane_id}
          matchId={match.id}
          reviewCase={reviewCase}
          lane={lane}
          laneIndex={laneIndex}
          lanes={lanes}
          selected={lane.lane_id === selectedLane.lane_id}
          resolution={drafts[lane.lane_id]}
          roster={assignmentOptions.roster}
          teams={match.teams}
          onSelect={() => chooseLane(lane.lane_id)}
        />)}
      </div>
      <aside className='concurrent-lane-decision' aria-label={`Przypisz Ścieżkę ${lanes.indexOf(selectedLane) + 1}`}>
        <header>
          <span className='eyebrow'>Wybrana ścieżka</span>
          <h3>Przypisz Ścieżkę {lanes.indexOf(selectedLane) + 1}</h3>
          <p>{timeRange(reviewCase, selectedLane.frame_start, selectedLane.frame_end)}</p>
          <strong>{laneStatus(drafts[selectedLane.lane_id], assignmentOptions.roster, match)}</strong>
        </header>
        <MixedAssignmentControls
          assignment={directAssignment(drafts[selectedLane.lane_id])}
          options={assignmentOptions}
          teams={match.teams}
          onAssign={assignLane}
        />
        <button type='button' className='secondary lane-split-action' onClick={openLaneSplit}>Ta ścieżka zawiera więcej niż jednego zawodnika</button>
      </aside>
    </div>
    <footer className='concurrent-resolver-footer'>
      {onPrevious && <button type='button' className='secondary' onClick={onPrevious} disabled={busy || previousDisabled}>Poprzedni</button>}
      <div className='concurrent-save-progress'><strong>{completed} z {lanes.length} ścieżek przypisane</strong><span>Wszystkie decyzje zostaną zapisane razem.</span></div>
      <button type='button' onClick={() => void submitAll()} disabled={busy || completed !== lanes.length}>Zapisz przypisania + następny</button>
      <button type='button' className='secondary quiet' onClick={() => void deferCurrentCase()} disabled={busy}>Nie da się bezpiecznie rozwiązać tego przypadku</button>
      {onCancel && <button type='button' className='secondary' onClick={onCancel} disabled={busy}>Wróć bez zapisu</button>}
      {onNext && <button type='button' className='secondary' onClick={onNext} disabled={busy || nextDisabled}>Następny</button>}
    </footer>
    {message && <p className='status' role='status'>{message}</p>}
  </section>;
}

function ConcurrentLaneCard({ matchId, reviewCase, lane, laneIndex, lanes, selected, resolution, roster, teams, onSelect }: {
  matchId: string;
  reviewCase: MixedPlayerCase;
  lane: ConcurrentMixedLane;
  laneIndex: number;
  lanes: ConcurrentMixedLane[];
  selected: boolean;
  resolution?: ConcurrentLaneDraft;
  roster: MixedPlayersReviewQueue['assignment_options']['roster'];
  teams: Match['teams'];
  onSelect: () => void;
}) {
  const overlaps = lane.overlap_lane_ids.map((id) => lanes.findIndex((value) => value.lane_id === id) + 1).filter((value) => value > 0);
  return <article className={`concurrent-lane-card${selected ? ' selected' : ''}`}>
    <button type='button' className='concurrent-lane-card-main' aria-pressed={selected} onClick={onSelect}>
    <span className='concurrent-lane-card-title'><strong>{completeLaneResolution(lane, resolution) ? '✓' : '!'} Ścieżka {laneIndex + 1}</strong><span>{laneStatus(resolution, roster, { teams })}</span></span>
    <span className='concurrent-lane-crops'>
      {sortedMixedEvidenceCrops(lane.evidence.anchor_crops).map((crop) => <img key={crop.anchor_crop_id} src={artifactUrl(matchId, crop.artifact)} alt={`Ścieżka ${laneIndex + 1}, ${formatReviewTime(crop.time_sec || 0)}`} />)}
      {lane.evidence.anchor_crops.length === 0 && <em>Brak podglądu tej ścieżki</em>}
    </span>
    <span className='concurrent-lane-card-meta'><span>{timeRange(reviewCase, lane.frame_start, lane.frame_end)}</span>{overlaps.length > 0 && <span>Nakłada się ze {overlaps.map((value) => `Ścieżką ${value}`).join(', ')}</span>}</span>
    </button>
    <details className='concurrent-lane-technical'>
      <summary>Szczegóły techniczne</summary>
      <small>Tracklet {lane.tracklet_id} · {lane.observation_count} obserwacji</small>
    </details>
  </article>;
}

function ConcurrentLaneSplitEditor({ match, reviewCase, lane, draft, assignmentOptions, selectedSegment, refinement, busy, onBack, onSelectSegment, onAssign, onBoundaries, onRefine, onSelectRefined, onRemoveRefined, message }: {
  match: Pick<Match, 'id' | 'teams'>;
  reviewCase: MixedPlayerCase;
  lane: ConcurrentMixedLane;
  draft?: ConcurrentLaneDraft;
  assignmentOptions: MixedPlayersReviewQueue['assignment_options'];
  selectedSegment: number;
  refinement: ConcurrentLaneRefinement | null;
  busy: boolean;
  onBack: () => void;
  onSelectSegment: (index: number) => void;
  onAssign: (assignment: MixedSegmentAssignment) => void;
  onBoundaries: (frames: number[]) => void;
  onRefine: (after: number, before: number) => void;
  onSelectRefined: (frame: number) => void;
  onRemoveRefined: () => void;
  message: string;
}) {
  const current = draft?.resolution === 'temporal_split' ? draft : null;
  const boundaries = current?.split_after_frames || [];
  const assignments = current?.segment_assignments || [];
  const laneCase = laneAsMixedCase(reviewCase, lane);
  const segments = mixedSegments(laneCase, boundaries);
  const crops = sortedMixedEvidenceCrops(lane.evidence.anchor_crops);
  return <section className='concurrent-lane-split-editor' aria-label='Podział wybranej ścieżki'>
    <header><div><span className='eyebrow'>Ścieżka {(reviewCase.concurrent_resolution?.lanes.findIndex((value) => value.lane_id === lane.lane_id) ?? -1) + 1} › Podział ścieżki</span><h2>Podziel tylko tę ścieżkę</h2><p>Podglądy i granice poniżej dotyczą wyłącznie wybranej ścieżki.</p></div><button type='button' className='secondary' onClick={onBack}>Wróć do ścieżek</button></header>
    <div className='mixed-temporal-strip lane-only-strip'>
      {crops.map((crop, index) => <div className='mixed-crop-group' key={crop.anchor_crop_id}>
        <figure><img src={artifactUrl(match.id, crop.artifact)} alt='Podgląd wyłącznie wybranej ścieżki' /><figcaption>{formatReviewTime(crop.time_sec || 0)}</figcaption></figure>
        {index < crops.length - 1 && <button type='button' className='split-boundary' disabled={busy} onClick={() => lane.observation_count > crops.length ? onRefine(crop.frame, crops[index + 1].frame) : onBoundaries(toggleMixedBoundary(boundaries, crop.frame))}>{lane.observation_count > crops.length ? 'Doprecyzuj' : 'Podziel tutaj'}</button>}
      </div>)}
    </div>
    {refinement && <div className='mixed-boundary-refinement'><strong>Doprecyzuj przejście w tej ścieżce</strong><div className='mixed-refinement-strip'>{sortedMixedEvidenceCrops(refinement.anchor_crops).map((crop) => <button type='button' key={crop.anchor_crop_id} onClick={() => onSelectRefined(crop.frame)}><img src={artifactUrl(match.id, crop.artifact)} alt='Dokładniejszy podgląd ścieżki' /><span>{formatReviewTime(crop.time_sec || 0)}</span></button>)}</div>{boundaries.some((frame) => frame >= refinement.after_frame && frame < refinement.before_frame) && <button type='button' className='secondary' onClick={onRemoveRefined}>Usuń podział z tego przedziału</button>}</div>}
    <div className='concurrent-lane-split-layout'>
      <div className='mixed-segment-list'>{segments.map((segment) => <button type='button' key={segment.index} className={selectedSegment === segment.index ? 'selected' : ''} onClick={() => onSelectSegment(segment.index)}><strong>Fragment {segment.index + 1}</strong><span>{timeRange(reviewCase, segment.frameStart, segment.frameEnd)}</span><small>{assignmentLabel(assignments[segment.index] || null)}</small></button>)}</div>
      <aside className='concurrent-lane-decision'><h3>Przypisz fragment {selectedSegment + 1}</h3><MixedAssignmentControls assignment={assignments[selectedSegment] || null} options={assignmentOptions} teams={match.teams} onAssign={onAssign} /></aside>
    </div>
    {message && <p className='status'>{message}</p>}
  </section>;
}

function laneAsMixedCase(parent: MixedPlayerCase, lane: ConcurrentMixedLane): MixedPlayerCase {
  return {
    ...parent,
    frame_start: lane.frame_start,
    frame_end: lane.frame_end,
    observation_count: lane.observation_count,
    temporal_evidence: lane.evidence,
    temporal_topology: {
      kind: 'serial',
      simple_split_allowed: true,
      tracklet_count: 1,
      max_concurrent_tracklets: 1,
      overlap_ranges: [],
      tracklets: [{ tracklet_id: lane.tracklet_id, frame_start: lane.frame_start, frame_end: lane.frame_end, observation_count: lane.observation_count }],
    },
    concurrent_resolution: null,
  };
}

function completeLaneResolution(lane: ConcurrentMixedLane, resolution?: ConcurrentLaneDraft): boolean {
  if (!resolution || resolution.lane_id !== lane.lane_id || resolution.lane_source_digest !== lane.source_ownership_digest) return false;
  if (resolution.resolution === 'direct') return Boolean(resolution.assignment);
  return resolution.split_after_frames.length > 0
    && resolution.split_after_frames.every((frame) => frame >= lane.frame_start && frame < lane.frame_end)
    && resolution.segment_assignments.length === resolution.split_after_frames.length + 1
    && resolution.segment_assignments.every(Boolean);
}

function laneStatus(resolution: ConcurrentLaneDraft | undefined, roster: MixedPlayersReviewQueue['assignment_options']['roster'], match: Pick<Match, 'teams'>): string {
  if (!resolution) return 'Nie przypisano';
  if (resolution.resolution === 'temporal_split') return resolution.split_after_frames.length > 0 && resolution.segment_assignments.length === resolution.split_after_frames.length + 1 && resolution.segment_assignments.every(Boolean) ? `Podzielono na ${resolution.segment_assignments.length} fragmenty` : 'Podział wymaga dokończenia';
  const player = roster.find((value) => value.player_id === resolution.assignment.player_id);
  const team = resolution.assignment.team_label === 'A' || resolution.assignment.team_label === 'B' ? matchTeamName(match.teams || [], resolution.assignment.team_label) : undefined;
  return assignmentLabel(resolution.assignment, player ? `${player.player_name}${player.roster_number ? ` #${player.roster_number}` : ''}` : undefined, team);
}

function persistedResolution(draft: ConcurrentLaneDraft | undefined): ConcurrentLaneResolution | null {
  if (!draft) return null;
  if (draft.resolution === 'direct') return draft;
  if (!draft.segment_assignments.every(Boolean)) return null;
  return {
    ...draft,
    segment_assignments: draft.segment_assignments.filter((value): value is MixedSegmentAssignment => value !== null),
  };
}

function directAssignment(draft: ConcurrentLaneDraft | undefined): MixedSegmentAssignment | null {
  return draft?.resolution === 'direct' ? draft.assignment : null;
}

function timeRange(reviewCase: MixedPlayerCase, start: number, end: number): string {
  const first = mixedTimeForFrame(reviewCase, start);
  const last = mixedTimeForFrame(reviewCase, end);
  return first !== null && last !== null ? `${formatReviewTime(first)}–${formatReviewTime(last)}` : `Klatki ${start}–${end}`;
}
