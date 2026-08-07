import type { ReviewWorkflow } from '../types';

export function initialAuditIdentityWorkIsComplete(
  workflow: ReviewWorkflow | undefined,
): boolean {
  return workflow !== undefined && workflow.phase !== 'initial_audit';
}
