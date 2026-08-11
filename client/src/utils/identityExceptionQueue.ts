import type {
  ReviewedCorrectionFinalizeResponse,
  ReviewedIdentityReviewUnit,
  ReviewWorkflow,
} from '../types';


export type ReviewCaseWithUnit = {
  unit: ReviewedIdentityReviewUnit;
};


export function reviewUnitKey(unit: ReviewedIdentityReviewUnit): string {
  return unit.review_target_id
    ? `segment:${unit.review_target_id}`
    : `subject:${unit.candidate_subject_id}`;
}


export function removeResolvedReviewCase<T extends ReviewCaseWithUnit>(
  cases: T[],
  currentIndex: number,
  resolvedKey: string,
): { cases: T[]; index: number } {
  const remaining = cases.filter((item) => reviewUnitKey(item.unit) !== resolvedKey);
  return {
    cases: remaining,
    index: remaining.length === 0
      ? 0
      : Math.min(Math.max(0, currentIndex), remaining.length - 1),
  };
}


export function shouldFinalizeDeferredReview(
  cases: ReviewCaseWithUnit[],
  recomputeRequired = false,
): boolean {
  return recomputeRequired || cases.length === 0;
}


export async function finalizeDeferredReviewBatch<T extends ReviewCaseWithUnit>(
  finalize: () => Promise<ReviewedCorrectionFinalizeResponse>,
  reloadCases: () => Promise<T[]>,
  onWorkflowChanged: (workflow: ReviewWorkflow) => void,
): Promise<{ result: ReviewedCorrectionFinalizeResponse; cases: T[] }> {
  const result = await finalize();
  onWorkflowChanged(result.workflow);
  return {
    result,
    cases: result.workflow.phase === 'exceptions' ? await reloadCases() : [],
  };
}
