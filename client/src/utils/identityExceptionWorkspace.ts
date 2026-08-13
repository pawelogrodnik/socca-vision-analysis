export function moveReviewCaseIndex(
  requestedIndex: number,
  caseCount: number,
): number {
  if (caseCount <= 0) return 0;
  return Math.min(Math.max(0, requestedIndex), caseCount - 1);
}

export async function persistReviewDecision<Result>(
  persist: () => Promise<Result>,
  onPersisted: (result: Result) => void,
): Promise<Result> {
  const result = await persist();
  onPersisted(result);
  return result;
}
