import type { ReviewedIdentityReviewQueue } from '../types';
import {
  beginRequiredReviewLifecycle,
  beginRequiredReviewNavigation,
} from './identityExceptionQueue';
import type { TeamReviewFilter } from './identityExceptionTeamFilter';

export function moveReviewCaseIndex(
  requestedIndex: number,
  caseCount: number,
): number {
  if (caseCount <= 0) return 0;
  return Math.min(Math.max(0, requestedIndex), caseCount - 1);
}

export function createReviewCommitGuard() {
  const committedKeys = new Set<string>();
  return {
    markIfNew: (reviewKey: string): boolean => {
      if (committedKeys.has(reviewKey)) return false;
      committedKeys.add(reviewKey);
      return true;
    },
    resetForAuthoritativeQueue: () => {
      committedKeys.clear();
    },
  };
}

export function createReviewQueueConflictRecovery(
  teamFilter: TeamReviewFilter,
  queue: ReviewedIdentityReviewQueue,
  totalRemaining: number,
) {
  return {
    localCases: [],
    index: 0,
    totalRemaining,
    lifecycle: beginRequiredReviewLifecycle(0),
    navigation: beginRequiredReviewNavigation(),
    progressRequest: {
      offset: 0,
      preferredIndex: 0,
      teamFilter,
      queue,
    },
  };
}

export async function persistReviewDecision<Result>(
  persist: () => Promise<Result>,
  onPersisted: (result: Result) => void,
): Promise<Result> {
  const result = await persist();
  onPersisted(result);
  return result;
}
