import { useEffect, useMemo, useState } from 'react';

import {
  artifactUrl,
  getIdentityRosterSubjectReview,
  getReviewedIdentityReviewProgress,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type {
  IdentityRosterSubjectReviewCard,
  Match,
  ReviewedCorrectionResponse,
  ReviewedIdentityAtEntity,
  ReviewWorkflow,
} from '../types';
import { hasOperatorReviewableVisualEvidence } from '../utils/identityReviewWorkspace';
import { requiredCasesLabel } from '../utils/reviewWorkflowPresentation';
import { ReviewedIdentityCorrectionForm } from './ReviewedIdentityCorrectionForm';

type Props = {
  match: Match;
  workflow: ReviewWorkflow;
  onWorkflowChanged: (workflow: ReviewedCorrectionResponse['workflow']) => void;
  onRetryReview?: () => Promise<void>;
};

function toCorrectionEntity(card: IdentityRosterSubjectReviewCard): ReviewedIdentityAtEntity {
  const crop = card.visual_evidence.anchor_crops[0];
  const frameStart = card.start_frame ?? crop?.frame ?? 0;
  const frameEnd = card.end_frame ?? crop?.frame ?? frameStart;
  return {
    frame: crop?.frame ?? frameStart,
    time_sec: crop?.time_sec ?? 0,
    tracklet_id: crop?.tracklet_id || card.candidate_subject_id,
    candidate_subject_id: card.candidate_subject_id,
    candidate_subject_ids: [card.candidate_subject_id],
    team_label: card.team_label || 'U',
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
    detected_evidence_count: card.detected_frames || 0,
    frame_start: frameStart,
    frame_end: frameEnd,
  };
}

export function IdentityExceptionReviewPanel({
  match,
  workflow,
  onWorkflowChanged,
  onRetryReview,
}: Props) {
  const [cards, setCards] = useState<IdentityRosterSubjectReviewCard[]>([]);
  const [index, setIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');

  async function loadCases(ignore?: () => boolean) {
    setLoading(true);
    setMessage('');
    try {
      const [document, progress] = await Promise.all([
        getIdentityRosterSubjectReview(match.id),
        getReviewedIdentityReviewProgress(match.id),
      ]);
      if (ignore?.()) return;
      const requiredSubjectIds = new Set(
        progress.next_cases
          .filter((item) => item.priority === 'high')
          .map((item) => item.candidate_subject_id),
      );
      const actionable = document.cards.filter((nextCard) => (
        requiredSubjectIds.has(nextCard.candidate_subject_id)
      ));
      setCards(actionable);
      setIndex(0);
    } catch (error) {
      if (!ignore?.()) setMessage(errorMessage(error));
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

  const card = cards[index] || null;
  const entity = useMemo(() => (card ? toCorrectionEntity(card) : null), [card]);
  const hasVisualEvidence = card ? hasOperatorReviewableVisualEvidence(card) : false;

  function saved(result: ReviewedCorrectionResponse) {
    setMessage('Zapisano decyzję. Sprawdzam, czy pozostały jeszcze ważne przypadki.');
    onWorkflowChanged(result.workflow);
    if (result.workflow?.phase === 'exceptions') void loadCases();
  }

  if (loading) return <p className='loading-line'><span className='spinner' /> Ładuję przypadki do sprawdzenia…</p>;

  return <section className='identity-exception-review'>
    <div className='row between'>
      <div>
        <p className='eyebrow'>Krok 2</p>
        <h2>Pozostałe przypadki</h2>
        <p>System zakończył automatyczne przypisania. Sprawdź tylko przypadki, w których wykryto konflikt wymagający decyzji.</p>
        <strong>{requiredCasesLabel(workflow.issues.blocking)}</strong>
      </div>
      {cards.length > 0 && <span className='reviewed-status-badge'>Przypadek {index + 1} z {cards.length}</span>}
    </div>

    {card && entity && hasVisualEvidence ? <>
      <div className='identity-exception-evidence'>
        {card.visual_evidence.anchor_crops.map((crop) => <figure key={crop.anchor_crop_id}>
          <img src={artifactUrl(match.id, crop.artifact)} alt='Widok zawodnika do identyfikacji' />
          <figcaption>Wybrany widok zawodnika</figcaption>
        </figure>)}
      </div>
      <ReviewedIdentityCorrectionForm
        matchId={match.id}
        entity={entity}
        onCancel={() => setMessage('Decyzja nie została zapisana.')}
        onSaved={saved}
      />
      {cards.length > 1 && <div className='row'>
        <button type='button' className='secondary' onClick={() => setIndex((current) => Math.max(0, current - 1))} disabled={index === 0}>Poprzedni</button>
        <button type='button' className='secondary' onClick={() => setIndex((current) => Math.min(cards.length - 1, current + 1))} disabled={index >= cards.length - 1}>Następny</button>
      </div>}
    </> : card && entity ? <div className='status'>
      <strong>Brak materiału pozwalającego wiarygodnie rozstrzygnąć ten przypadek.</strong>
      <p>Odśwież Review — ten przypadek nie powinien wymagać ręcznej decyzji.</p>
      {onRetryReview && <button type='button' className='secondary' onClick={() => void onRetryReview()}>
        Odśwież Review
      </button>}
    </div> : <div className='status'>
      <strong>Nie udało się przygotować podglądu przypadku wymagającego decyzji.</strong>
      <p>Workflow nadal wskazuje: {requiredCasesLabel(workflow.issues.blocking)}. Odśwież Review albo otwórz diagnostykę.</p>
      {onRetryReview && <button type='button' className='secondary' onClick={() => void onRetryReview()}>
        Odśwież Review
      </button>}
    </div>}
    {message && <p className='status'>{message}</p>}
  </section>;
}
