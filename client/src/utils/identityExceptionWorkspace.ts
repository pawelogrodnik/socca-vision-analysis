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

export async function persistReviewDecision<Result>(
  persist: () => Promise<Result>,
  onPersisted: (result: Result) => void,
): Promise<Result> {
  const result = await persist();
  onPersisted(result);
  return result;
}
