import type {
  ReviewedCorrectionFinalizeResponse,
  ReviewedIdentityReviewQueue,
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


export const REQUIRED_REVIEW_WORKING_WINDOW_SIZE = 40;


export type RequiredReviewLifecycle = {
  knownRemaining: number;
  durableSavesInWindow: number;
};


export type RequiredReviewSaveTransition = {
  lifecycle: RequiredReviewLifecycle;
  synchronization: 'none' | 'boundary' | 'completion';
};


export function beginRequiredReviewLifecycle(knownRemaining: number): RequiredReviewLifecycle {
  return {
    knownRemaining: Math.max(0, knownRemaining),
    durableSavesInWindow: 0,
  };
}


export function recordDurableRequiredReviewSave(
  lifecycle: RequiredReviewLifecycle,
): RequiredReviewSaveTransition {
  const knownRemaining = Math.max(0, lifecycle.knownRemaining - 1);
  const durableSavesInWindow = lifecycle.durableSavesInWindow + 1;
  if (durableSavesInWindow >= REQUIRED_REVIEW_WORKING_WINDOW_SIZE) {
    return {
      lifecycle: { knownRemaining, durableSavesInWindow: 0 },
      synchronization: 'boundary',
    };
  }
  return {
    lifecycle: { knownRemaining, durableSavesInWindow },
    synchronization: knownRemaining === 0 ? 'completion' : 'none',
  };
}


export function shouldRecoverRequiredReviewCompletion(
  recomputeRequired: boolean | undefined,
  knownRemaining: number,
  coverageAllowsFinalize: boolean,
): boolean {
  return recomputeRequired === true
    && knownRemaining === 0
    && coverageAllowsFinalize;
}


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
  _recomputeRequired = false,
  globalRemaining = cases.length,
  coverageAllowsFinalize = true,
): boolean {
  // `recompute_required` means canonical propagation is pending; it is not a
  // claim that a versioned hot queue is unsafe. Callers finalize only at a
  // deliberate working-window or completion boundary.
  return cases.length === 0
    && globalRemaining === 0
    && coverageAllowsFinalize;
}


export function shouldAutoFinalizeDeferredQueue(
  queue: ReviewedIdentityReviewQueue,
  cases: ReviewCaseWithUnit[],
  recomputeRequired = false,
  globalRemaining = cases.length,
  coverageAllowsFinalize = true,
): boolean {
  return queue === 'required' && shouldFinalizeDeferredReview(
    cases,
    recomputeRequired,
    globalRemaining,
    coverageAllowsFinalize,
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
