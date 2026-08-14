import type {
  ReviewedCorrectionFinalizeResponse,
  ReviewedIdentityReviewUnit,
  ReviewWorkflow,
} from '../types';


export type ReviewCaseWithUnit = {
  unit: ReviewedIdentityReviewUnit;
};


export type ReviewPageNavigation =
  | { kind: 'local'; index: number }
  | { kind: 'page'; offset: number; index: number }
  | { kind: 'none' };


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
  globalRemaining = cases.length,
  coverageAllowsFinalize = true,
): boolean {
  return recomputeRequired || (
    cases.length === 0
    && globalRemaining === 0
    && coverageAllowsFinalize
  );
}


export function resolveReviewPageNavigation({
  direction,
  currentIndex,
  pageLength,
  pageOffset,
  pageSize,
  hasMore,
}: {
  direction: 'previous' | 'next';
  currentIndex: number;
  pageLength: number;
  pageOffset: number;
  pageSize: number;
  hasMore: boolean;
}): ReviewPageNavigation {
  if (direction === 'previous') {
    if (currentIndex > 0) return { kind: 'local', index: currentIndex - 1 };
    if (pageOffset <= 0) return { kind: 'none' };
    return {
      kind: 'page',
      offset: Math.max(0, pageOffset - pageSize),
      index: pageSize - 1,
    };
  }
  if (currentIndex + 1 < pageLength) {
    return { kind: 'local', index: currentIndex + 1 };
  }
  if (!hasMore) return { kind: 'none' };
  return { kind: 'page', offset: pageOffset + pageSize, index: 0 };
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
