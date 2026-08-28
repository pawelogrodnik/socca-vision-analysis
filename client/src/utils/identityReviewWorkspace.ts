import type {
  IdentityRosterSubjectReviewCard,
  ReviewWorkflow,
  ReviewWorkflowAction,
  ReviewWorkflowStepId,
} from '../types';

export type IdentityReviewStage =
  | 'identify_players'
  | 'remaining_issues'
  | 'mixed_players'
  | 'prepare_result'
  | 'rendering'
  | 'video_qa'
  | 'complete'
  | 'error'
  | 'unavailable';

export type IdentityReviewProgressItem = {
  id: ReviewWorkflowStepId;
  label: string;
  status: 'locked' | 'current' | 'processing' | 'completed' | 'error';
};

const stageByPhase: Record<string, IdentityReviewStage> = {
  initial_audit: 'identify_players',
  exceptions: 'remaining_issues',
  mixed_players: 'mixed_players',
  ready_to_finalize: 'prepare_result',
  rendering_review_video: 'rendering',
  video_qa: 'video_qa',
  complete: 'complete',
};

const progressLabels: Record<ReviewWorkflowStepId, string> = {
  initial_audit: 'Rozpoznaj zawodników',
  exceptions: 'Pozostałe przypadki',
  mixed_players: 'Zmieszani gracze',
  finalize: 'Przygotuj wynik',
  video_qa: 'Sprawdź wideo',
};

export function identityReviewStage(workflow: ReviewWorkflow | null): IdentityReviewStage {
  if (!workflow?.available) return 'unavailable';
  if (workflow.status === 'error') return 'error';
  return stageByPhase[workflow.phase] || 'unavailable';
}

/** Pick the first mandatory queue before either review panel can mount. */
export function initialMandatoryQueue(
  workflow: ReviewWorkflow | null,
): 'required' | 'mixed' {
  return identityReviewStage(workflow) === 'mixed_players' ? 'mixed' : 'required';
}

export function identityReviewProgress(workflow: ReviewWorkflow | null): IdentityReviewProgressItem[] {
  const byId = new Map((workflow?.steps || []).map((step) => [step.id, step]));
  return (Object.keys(progressLabels) as ReviewWorkflowStepId[]).map((id) => ({
    id,
    label: progressLabels[id],
    status: byId.get(id)?.status || 'locked',
  }));
}

/** Workflow permissions are the sole authority for normal operator CTAs. */
export function workflowAllows(
  workflow: ReviewWorkflow | null,
  action: ReviewWorkflowAction,
): boolean {
  return Boolean(workflow?.allowed_actions.includes(action));
}

export function hasOperatorReviewableVisualEvidence(
  card: Pick<IdentityRosterSubjectReviewCard, 'visual_evidence'>,
): boolean {
  return card.visual_evidence.anchor_crops.length > 0;
}

export function reviewWorkflowErrorMessage(workflow: ReviewWorkflow): string {
  const code = workflow.blockers[0]?.code || workflow.required_action?.type;
  if (code === 'render_failed') return 'Nie udało się przygotować wideo do sprawdzenia.';
  if (code === 'review_recompute_failed') return 'Nie udało się odświeżyć review.';
  if (code === 'review_progress_missing' || code === 'review_progress_stale') {
    return 'Review wymaga odświeżenia przed kolejną decyzją.';
  }
  return 'Nie udało się przygotować kolejnego kroku review.';
}
