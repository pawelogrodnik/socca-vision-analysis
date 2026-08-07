import { useState } from 'react';

import type {
  ReviewedCorrectionResponse,
  ReviewedIdentityAt,
  ReviewedIdentityAtEntity,
} from '../types';
import { formatReviewTime, teamLabelForOperator } from '../utils/reviewedOutputPresentation';
import { ReviewedIdentityCorrectionForm } from './ReviewedIdentityCorrectionForm';

type Props = {
  matchId: string;
  document: ReviewedIdentityAt;
  onCorrectionSaved: (
    entity: ReviewedIdentityAtEntity,
    result: ReviewedCorrectionResponse,
  ) => void;
};

function identityDescription(entity: ReviewedIdentityAtEntity): string {
  if (entity.identity_status === 'confirmed') return 'Rozpoznany zawodnik';
  if (entity.identity_status === 'referee') return 'Sędzia';
  if (entity.identity_status === 'false_detection') return 'Fałszywa detekcja';
  return 'Nierozpoznany zawodnik';
}

export function ReviewedIdentityAtTimePanel({
  matchId,
  document,
  onCorrectionSaved,
}: Props) {
  const [editingTrackletId, setEditingTrackletId] = useState<string | null>(null);
  return <section className='reviewed-at-time'>
    <h3>Osoby widoczne w tej klatce · {formatReviewTime(document.time_sec)}</h3>
    {document.reference_snapshot_stale && <p className='status'>
      Lista pochodzi ze starego filmu referencyjnego; zapisane poprawki zostaną pokazane po odświeżeniu wyniku.
    </p>}
    {!document.entities.length && <p>Brak realnych wykrytych obserwacji w tej klatce.</p>}
    {document.entities.map((entity) => <article className='reviewed-entity-row' key={`${entity.tracklet_id}:${entity.frame}`}>
      <div className='reviewed-entity-main'>
        <strong>{entity.display_label || entity.fallback_label}</strong>
        <span>{identityDescription(entity)} · {teamLabelForOperator(entity.team_label)}</span>
        <p>Fragment obejmuje {entity.detected_evidence_count} wykryte obserwacje.</p>
      </div>
      {editingTrackletId !== entity.tracklet_id && <button
        type='button'
        onClick={() => setEditingTrackletId(entity.tracklet_id)}
        disabled={!entity.candidate_subject_id}
      >{entity.identity_status === 'confirmed' ? 'Zmień przypisanie' : 'Zidentyfikuj'}</button>}
      <details className='reviewed-entity-technical-details'>
        <summary>Szczegóły techniczne</summary>
        <p>candidate_subject_id: {entity.candidate_subject_id ?? 'brak'}</p>
        <p>candidate_subject_ids: {entity.candidate_subject_ids.join(', ') || 'brak'}</p>
        <p>tracklet_id: {entity.tracklet_id} · frame_start: {entity.frame_start} · frame_end: {entity.frame_end}</p>
        <p>identity_source: {entity.identity_source ?? 'brak'} · status: {entity.identity_status}</p>
        {entity.hard_blockers.length > 0 && <p>hard blockers: {entity.hard_blockers.join(', ')}</p>}
        {entity.conflicts.length > 0 && <p>conflicts: {entity.conflicts.length}</p>}
      </details>
      {editingTrackletId === entity.tracklet_id && <ReviewedIdentityCorrectionForm
        matchId={matchId}
        entity={entity}
        onCancel={() => setEditingTrackletId(null)}
        onSaved={(result) => {
          setEditingTrackletId(null);
          onCorrectionSaved(entity, result);
        }}
      />}
    </article>)}
  </section>;
}
