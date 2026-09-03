import type { ReviewWorkflow } from '../types';
import { workflowAllows } from '../utils/identityReviewWorkspace';

export type ReviewedIdentityMandatoryQueue = 'required' | 'mixed';

type Props = {
  workflow: ReviewWorkflow;
  activeQueue: ReviewedIdentityMandatoryQueue;
  disabled?: boolean;
  onSelect: (queue: ReviewedIdentityMandatoryQueue) => void;
};

export function ReviewedIdentityQueueTabs({ workflow, activeQueue, disabled = false, onSelect }: Props) {
  const required = workflow.issues.normal_blocking ?? 0;
  const mixed = workflow.issues.mixed_blocking ?? 0;
  const canOpenRequired = workflowAllows(workflow, 'review_identity_issue');
  const canOpenMixed = workflowAllows(workflow, 'review_mixed_players');

  return <nav className='identity-review-mandatory-queues' aria-label='Kolejki wymaganej części Review'>
    <button
      type='button'
      className={activeQueue === 'required' ? 'active' : ''}
      aria-pressed={activeQueue === 'required'}
      disabled={disabled || !canOpenRequired}
      onClick={() => onSelect('required')}
    >Wymagane przypadki <span>{required}</span></button>
    <button
      type='button'
      className={activeQueue === 'mixed' ? 'active' : ''}
      aria-pressed={activeQueue === 'mixed'}
      disabled={disabled || !canOpenMixed}
      onClick={() => onSelect('mixed')}
    >Zmieszani gracze <span>{mixed}</span></button>
  </nav>;
}
