import type { Match, ReviewWorkflow } from '../types';

export type TopLevelWorkflowStepId = 'video' | 'analysis' | 'review' | 'publish';
export type TopLevelWorkflowStepStatus = 'current' | 'done' | 'ready' | 'locked';

function analysisCompleted(match: Match | null): boolean {
  return match?.analysis_report?.status === 'completed';
}

function stepReachable(
  stepId: TopLevelWorkflowStepId,
  match: Match | null,
  workflow: ReviewWorkflow | null,
): boolean {
  if (stepId === 'video') return true;
  if (stepId === 'analysis') return Boolean(match);
  if (stepId === 'review') return analysisCompleted(match);
  return analysisCompleted(match) && workflow?.can_enter_report === true;
}

export function suggestedTopLevelStep(
  match: Match | null,
  workflow: ReviewWorkflow | null,
): TopLevelWorkflowStepId {
  if (!match) return 'video';
  if (!analysisCompleted(match)) return 'analysis';
  return workflow?.can_enter_report === true ? 'publish' : 'review';
}

export function topLevelStepStatus(
  stepId: TopLevelWorkflowStepId,
  activeStep: TopLevelWorkflowStepId,
  match: Match | null,
  workflow: ReviewWorkflow | null,
): TopLevelWorkflowStepStatus {
  if (!stepReachable(stepId, match, workflow)) return 'locked';
  if (stepId === activeStep) return 'current';
  if (stepId === 'video' && match) return 'done';
  if (stepId === 'analysis' && analysisCompleted(match)) return 'done';
  if (stepId === 'review' && workflow?.review_complete) return 'done';
  if (stepId === 'publish' && Boolean(match?.published_match_id || match?.status === 'published')) return 'done';
  return 'ready';
}
