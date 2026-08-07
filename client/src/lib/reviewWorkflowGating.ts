import type { ReviewWorkflow } from '../types';

export type ReportWorkflowGate = {
  allowed: boolean;
  reasonCode: string | null;
};

/** Backend workflow state wins over legacy local readiness checks. */
export function reportWorkflowGate(workflow: ReviewWorkflow | null): ReportWorkflowGate {
  if (workflow?.can_enter_report && workflow.can_publish) {
    return { allowed: true, reasonCode: null };
  }
  return {
    allowed: false,
    reasonCode: workflow?.blockers[0]?.code || 'review_not_completed',
  };
}
