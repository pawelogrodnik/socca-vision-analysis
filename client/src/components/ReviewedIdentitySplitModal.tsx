import { createPortal } from 'react-dom';

import type { ReviewedCorrectionContext, ReviewedCorrectionResponse, Team } from '../types';
import { ReviewedIdentitySplitEditor } from './ReviewedIdentitySplitEditor';

type Props = {
  matchId: string;
  context: ReviewedCorrectionContext;
  teams?: Team[];
  onCancel: () => void;
  onSaved: (result: ReviewedCorrectionResponse) => void;
};

/** Full-width editor for explicitly reopening an already saved split. */
export function ReviewedIdentitySplitModal({ matchId, context, teams, onCancel, onSaved }: Props) {
  return createPortal(
    <div className='reviewed-identity-split-modal' role='dialog' aria-modal='true' aria-label='Edytuj podział fragmentu'>
      {/* Backdrop is visual only and blocks the page behind the modal. The
          editor owns guarded dirty-state cancellation ("Wróć bez zapisu"). */}
      <div className='reviewed-identity-split-modal-backdrop' />
      <section className='reviewed-identity-split-modal-content'>
        <ReviewedIdentitySplitEditor
          matchId={matchId}
          context={context}
          teams={teams}
          onCancel={onCancel}
          onSaved={(result) => onSaved(result as unknown as ReviewedCorrectionResponse)}
        />
      </section>
    </div>,
    document.body,
  );
}
