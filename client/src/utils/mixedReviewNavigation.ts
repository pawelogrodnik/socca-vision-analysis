export type MixedEntryMode = 'manual' | 'resolve_now';

export type MixedPostSaveDestination = 'required' | 'mixed' | 'workflow';

/**
 * Route only after the backend's structural reproject. Counts are
 * authoritative workflow facts, while entryMode is deliberately UI intent.
 */
export function mixedPostSaveDestination(
  entryMode: MixedEntryMode,
  normalBlocking: number,
  mixedBlocking: number,
): MixedPostSaveDestination {
  if (entryMode === 'resolve_now' && normalBlocking > 0) return 'required';
  if (mixedBlocking > 0) return 'mixed';
  if (normalBlocking > 0) return 'required';
  return 'workflow';
}

export function exactMixedFocusIndex(
  caseIds: string[],
  focusCaseId: string | null | undefined,
): number | null {
  if (!focusCaseId) return 0;
  const index = caseIds.indexOf(focusCaseId);
  return index >= 0 ? index : null;
}
