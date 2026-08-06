import { useState } from 'react';

import type {
  ReviewedCorrectionResponse,
  ReviewedIdentityAt,
  ReviewedIdentityAtEntity,
} from '../types';
import { ReviewedIdentityCorrectionForm } from './ReviewedIdentityCorrectionForm';

type Props = {
  matchId: string;
  document: ReviewedIdentityAt;
  onCorrectionSaved: (
    entity: ReviewedIdentityAtEntity,
    result: ReviewedCorrectionResponse,
  ) => void;
};

export function ReviewedIdentityAtTimePanel({
  matchId,
  document,
  onCorrectionSaved,
}: Props) {
  const [editingTrackletId, setEditingTrackletId] = useState<string | null>(null);
  return <div className='reviewed-at-time'>
    <h3>Widoczne przypisania · klatka {document.frame}</h3>
    {document.reference_snapshot_stale && <p className='status'>
      Lista pochodzi ze starego filmu referencyjnego; zapisane poprawki zostaną pokazane po finalizacji.
    </p>}
    {!document.entities.length && <p>Brak realnych wykrytych obserwacji w tej klatce.</p>}
    {document.entities.map((entity) => <article className='reviewed-entity-row' key={`${entity.tracklet_id}:${entity.frame}`}>
      <div>
        <strong>{entity.display_label || entity.fallback_label}</strong>{' · '}
        Team {entity.team_label}{' · '}
        <span>{entity.identity_status}</span>
        <div className='muted'>
          subject: {entity.candidate_subject_id ?? 'brak'} · tracklet: {entity.tracklet_id} · detected observations: {entity.detected_evidence_count}
        </div>
      </div>
      {editingTrackletId !== entity.tracklet_id && <button
        type='button'
        onClick={() => setEditingTrackletId(entity.tracklet_id)}
        disabled={!entity.candidate_subject_id}
      >Popraw przypisanie</button>}
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
  </div>;
}
