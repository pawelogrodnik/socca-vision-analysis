import type {
  ReviewedCorrectionResponse,
  ReviewedOutputJob,
  ReviewWorkflow,
} from '../types';

export type ReviewedCorrectionWorkflowMode =
  | 'automatic_rerender'
  | 'exceptions'
  | 'manual_finalize_available'
  | 'updated';

export function canFinalizeReviewedVideo(
  workflow: ReviewWorkflow | null | undefined,
): boolean {
  return workflow?.allowed_actions.includes('finalize_identity') === true;
}

export function reviewedCorrectionWorkflowPresentation(
  result: ReviewedCorrectionResponse,
): {
  mode: ReviewedCorrectionWorkflowMode;
  queuedRenderJob: ReviewedOutputJob | null;
} {
  const workflow = result.workflow;
  if (
    workflow?.phase === 'rendering_review_video'
    && workflow.status === 'processing'
  ) {
    return {
      mode: 'automatic_rerender',
      queuedRenderJob: result.render_job ?? workflow.processing ?? null,
    };
  }
  if (workflow?.phase === 'exceptions') {
    return { mode: 'exceptions', queuedRenderJob: null };
  }
  return {
    mode: canFinalizeReviewedVideo(workflow)
      ? 'manual_finalize_available'
      : 'updated',
    queuedRenderJob: null,
  };
}
