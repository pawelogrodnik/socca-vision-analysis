import { useEffect, useMemo, useRef, useState } from 'react';

import {
  artifactUrl,
  finalizeReviewedIdentityCorrections,
  getIdentityRosterSubjectReview,
  getReviewedIdentityReviewProgress,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type {
  IdentityRosterSubjectReviewCard,
  Match,
  ReviewedCorrectionResponse,
  ReviewedIdentityAtEntity,
  ReviewedIdentityReviewUnit,
  ReviewWorkflow,
} from '../types';
import { hasOperatorReviewableVisualEvidence } from '../utils/identityReviewWorkspace';
import {
  finalizeDeferredReviewBatch,
  removeResolvedReviewCase,
  reviewUnitKey,
  shouldFinalizeDeferredReview,
} from '../utils/identityExceptionQueue';
import { requiredCasesLabel } from '../utils/reviewWorkflowPresentation';
import { ReviewedIdentityCorrectionForm } from './ReviewedIdentityCorrectionForm';

type Props = {
  match: Match;
  workflow: ReviewWorkflow;
  onWorkflowChanged: (workflow: ReviewWorkflow) => void;
  onRetryReview?: () => Promise<void>;
};

type ReviewCase = {
  unit: ReviewedIdentityReviewUnit;
  card: IdentityRosterSubjectReviewCard | null;
};

function toCorrectionEntity(reviewCase: ReviewCase): ReviewedIdentityAtEntity {
  const { card, unit } = reviewCase;
  const evidence = unit.visual_evidence || card?.visual_evidence;
  const crop = evidence?.anchor_crops[0];
  const frameStart = unit.frame_start ?? card?.start_frame ?? crop?.frame ?? 0;
  const frameEnd = unit.frame_end ?? card?.end_frame ?? crop?.frame ?? frameStart;
  return {
    frame: crop?.frame ?? frameStart,
    time_sec: crop?.time_sec ?? 0,
    tracklet_id: crop?.tracklet_id || unit.tracklet_ids[0] || unit.candidate_subject_id,
    candidate_subject_id: unit.candidate_subject_id,
    candidate_subject_ids: [unit.candidate_subject_id],
    team_label: unit.source_team_label || card?.team_label || 'U',
    stable_anonymous_slot_id: null,
    canonical_player_id: null,
    player_name: null,
    display_label: '',
    identity_status: 'unresolved',
    identity_source: null,
    fallback_label: 'Nieznany zawodnik',
    requires_review: true,
    hard_blockers: [],
    conflicts: [],
    detected_evidence_count: unit.detected_observation_count || card?.detected_frames || 0,
    frame_start: frameStart,
    frame_end: frameEnd,
    review_target_id: unit.review_target_id,
    scope_kind: unit.scope_kind,
    source_ownership_digest: unit.source_ownership_digest,
  };
}

export function IdentityExceptionReviewPanel({
  match,
  workflow,
  onWorkflowChanged,
  onRetryReview,
}: Props) {
  const [cases, setCases] = useState<ReviewCase[]>([]);
  const [index, setIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [finalizing, setFinalizing] = useState(false);
  const [finalizeFailed, setFinalizeFailed] = useState(false);
  const [message, setMessage] = useState('');
  const finalizeInFlight = useRef(false);

  async function loadCases(ignore?: () => boolean, preserveMessage = false): Promise<ReviewCase[]> {
    setLoading(true);
    if (!preserveMessage) setMessage('');
    try {
      const [document, progress] = await Promise.all([
        getIdentityRosterSubjectReview(match.id),
        getReviewedIdentityReviewProgress(match.id),
      ]);
      if (ignore?.()) return [];
      const cardsBySubject = new Map(
        document.cards.map((nextCard) => [nextCard.candidate_subject_id, nextCard]),
      );
      const actionable = progress.next_cases
        .filter((item) => item.priority === 'high')
        .map((unit) => ({
          unit,
          card: cardsBySubject.get(unit.candidate_subject_id) || null,
        }));
      if (shouldFinalizeDeferredReview(actionable, progress.recompute_required)) {
        setCases([]);
        setIndex(0);
        void finalizeCorrections();
        return [];
      }
      setCases(actionable);
      setIndex(0);
      return actionable;
    } catch (error) {
      if (!ignore?.()) setMessage(errorMessage(error));
      return [];
    } finally {
      if (!ignore?.()) setLoading(false);
    }
  }

  useEffect(() => {
    let disposed = false;
    void loadCases(() => disposed);
    return () => { disposed = true; };
    // Cards are reloaded after a semantic decision, not for incidental workflow object updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id]);

  const reviewCase = cases[index] || null;
  const card = reviewCase?.card || null;
  const unitEvidence = reviewCase?.unit.visual_evidence;
  const evidence = unitEvidence || card?.visual_evidence || null;
  const entity = useMemo(
    () => (reviewCase ? toCorrectionEntity(reviewCase) : null),
    [reviewCase],
  );
  const hasVisualEvidence = Boolean(
    unitEvidence?.anchor_crops.length
    || (card && hasOperatorReviewableVisualEvidence(card)),
  );

  function saved(result: ReviewedCorrectionResponse) {
    if (!reviewCase || !result.recompute_deferred) {
      if (result.workflow) onWorkflowChanged(result.workflow);
      return;
    }
    const next = removeResolvedReviewCase(
      cases,
      index,
      reviewUnitKey(reviewCase.unit),
    );
    setCases(next.cases);
    setIndex(next.index);
    setFinalizeFailed(false);
    setMessage('Zapisano decyzję.');
    if (shouldFinalizeDeferredReview(next.cases)) void finalizeCorrections();
  }

  async function finalizeCorrections() {
    if (finalizeInFlight.current) return;
    finalizeInFlight.current = true;
    setFinalizing(true);
    setFinalizeFailed(false);
    setMessage('Przeliczam Review po zapisaniu decyzji…');
    try {
      const { result, cases: authoritativeCases } = await finalizeDeferredReviewBatch(
        () => finalizeReviewedIdentityCorrections(match.id),
        () => loadCases(undefined, true),
        onWorkflowChanged,
      );
      if (result.workflow.phase === 'exceptions') {
        setMessage(`Po przeliczeniu pozostały jeszcze ${authoritativeCases.length} przypadki.`);
      } else {
        setCases([]);
        setIndex(0);
        setMessage('Review zostało przeliczone.');
      }
    } catch (error) {
      setFinalizeFailed(true);
      setMessage(`Decyzje są zapisane, ale przeliczenie Review nie powiodło się. ${errorMessage(error)}`);
    } finally {
      finalizeInFlight.current = false;
      setFinalizing(false);
    }
  }

  if (loading && !finalizing) return <p className='loading-line'><span className='spinner' /> Ładuję przypadki do sprawdzenia…</p>;

  return <section className='identity-exception-review'>
    <div className='row between'>
      <div>
        <p className='eyebrow'>Krok 2</p>
        <h2>Pozostałe przypadki</h2>
        <p>System zakończył automatyczne przypisania. Sprawdź tylko przypadki, w których wykryto konflikt wymagający decyzji.</p>
        <strong>{requiredCasesLabel(workflow.issues.blocking)}</strong>
      </div>
      {cases.length > 0 && <span className='reviewed-status-badge'>Przypadek {index + 1} z {cases.length}</span>}
    </div>

    {reviewCase && entity && hasVisualEvidence && evidence ? <>
      {reviewCase.unit.scope_kind === 'canonical_segment' && <div className='status'>
        <strong>System połączył w jednym tracklecie różne osoby.</strong>
        <p>Oceń tylko pokazany fragment. Decyzja nie obejmie sąsiednich ani niejednoznacznych klatek.</p>
      </div>}
      <div className='identity-exception-evidence'>
        {evidence.anchor_crops.map((crop) => <figure
          key={crop.anchor_crop_id}
          className={`team-${reviewCase.unit.source_team_label.toLowerCase()}`}
        >
          <img src={artifactUrl(match.id, crop.artifact)} alt='Widok zawodnika do identyfikacji' />
          <figcaption>Wybrany widok zawodnika</figcaption>
        </figure>)}
        {(unitEvidence?.boundary_crops || []).map((crop) => <figure
          key={`boundary-${crop.anchor_crop_id}`}
          className='outside-target'
        >
          <img src={artifactUrl(match.id, crop.artifact)} alt='Sąsiedni fragment poza zakresem decyzji' />
          <figcaption>Sąsiedni fragment — poza zakresem decyzji</figcaption>
        </figure>)}
      </div>
      <ReviewedIdentityCorrectionForm
        matchId={match.id}
        entity={entity}
        onCancel={() => setMessage('Decyzja nie została zapisana.')}
        onSaved={saved}
        deferRecompute
      />
      {cases.length > 1 && <div className='row'>
        <button type='button' className='secondary' onClick={() => setIndex((current) => Math.max(0, current - 1))} disabled={index === 0}>Poprzedni</button>
        <button type='button' className='secondary' onClick={() => setIndex((current) => Math.min(cases.length - 1, current + 1))} disabled={index >= cases.length - 1}>Następny</button>
      </div>}
    </> : reviewCase && entity ? <div className='status'>
      <strong>Brak materiału pozwalającego wiarygodnie rozstrzygnąć ten przypadek.</strong>
      <p>Odśwież Review — ten przypadek nie powinien wymagać ręcznej decyzji.</p>
      {onRetryReview && <button type='button' className='secondary' onClick={() => void onRetryReview()}>
        Odśwież Review
      </button>}
    </div> : finalizing ? null : <div className='status'>
      <strong>Nie udało się przygotować podglądu przypadku wymagającego decyzji.</strong>
      <p>Workflow nadal wskazuje: {requiredCasesLabel(workflow.issues.blocking)}. Odśwież Review albo otwórz diagnostykę.</p>
      {onRetryReview && <button type='button' className='secondary' onClick={() => void onRetryReview()}>
        Odśwież Review
      </button>}
    </div>}
    {finalizing && <p className='loading-line'><span className='spinner' /> Przeliczam Review po zapisaniu decyzji…</p>}
    {message && <p className={`status${finalizeFailed ? ' error' : ''}`}>{message}</p>}
    {finalizeFailed && <button
      type='button'
      className='secondary'
      onClick={() => void finalizeCorrections()}
      disabled={finalizing}
    >Ponów przeliczenie Review</button>}
  </section>;
}
