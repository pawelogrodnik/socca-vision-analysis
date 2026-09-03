import type { MixedPlayerFocusedCaseResponse, MixedPlayersReviewQueue } from '../types';

export type MixedEntryMode = 'manual' | 'resolve_now';

export type MixedPostSaveDestination = 'required' | 'mixed' | 'workflow';

export type MixedNavigationDirection = 'previous' | 'next';

export type ExactMixedFocusResult<
  Response extends {
    requested_case_id: string;
    status: string;
    case: { case_id?: string } | null;
  },
> =
  | { kind: 'visible'; response: Response; case: NonNullable<Response['case']> }
  | { kind: 'membership_changed'; response: Response }
  | { kind: 'invalid'; response: Response };

const MIXED_MEMBERSHIP_CHANGED_STATUSES = new Set([
  'missing',
  'no_longer_unresolved',
  'not_in_mandatory_queue',
]);

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

export async function loadExactMixedFocus<
  Response extends {
    requested_case_id: string;
    status: string;
    case: { case_id?: string } | null;
  },
>(
  load: (caseId: string) => Promise<Response>,
  caseId: string,
): Promise<ExactMixedFocusResult<Response>> {
  const response = await load(caseId);
  if (response.requested_case_id !== caseId) return { kind: 'invalid', response };
  const visible = response.status === 'current_blocking'
    || response.status === 'stale_or_unclassifiable_blocking';
  if (visible && response.case?.case_id === caseId) {
    return {
      kind: 'visible',
      response,
      case: response.case as NonNullable<Response['case']>,
    };
  }
  if (MIXED_MEMBERSHIP_CHANGED_STATUSES.has(response.status) && response.case === null) {
    return { kind: 'membership_changed', response };
  }
  return { kind: 'invalid', response };
}

/**
 * Select the next authoritative case without trusting a stale local sibling.
 * The current case is the navigation anchor when it survives reconciliation;
 * otherwise the old ordering supplies a best-effort directional position.
 */
export function reconciledMixedFocusCaseId(
  previousCaseIds: string[],
  currentCaseId: string | null,
  attemptedIndex: number,
  authoritativeCaseIds: string[],
  direction: MixedNavigationDirection,
): string | null {
  if (authoritativeCaseIds.length === 0) return null;

  const step = direction === 'next' ? 1 : -1;
  const currentAuthoritativeIndex = currentCaseId
    ? authoritativeCaseIds.indexOf(currentCaseId)
    : -1;
  if (currentAuthoritativeIndex >= 0) {
    const adjacent = authoritativeCaseIds[currentAuthoritativeIndex + step];
    return adjacent || authoritativeCaseIds[currentAuthoritativeIndex];
  }

  const priorPositions = new Map(previousCaseIds.map((caseId, index) => [caseId, index]));
  const orderedSurvivors = authoritativeCaseIds
    .map((caseId) => ({ caseId, oldIndex: priorPositions.get(caseId) }))
    .filter((item): item is { caseId: string; oldIndex: number } => item.oldIndex !== undefined)
    .filter((item) => direction === 'next'
      ? item.oldIndex >= attemptedIndex
      : item.oldIndex <= attemptedIndex)
    .sort((left, right) => direction === 'next'
      ? left.oldIndex - right.oldIndex
      : right.oldIndex - left.oldIndex);
  if (orderedSurvivors[0]) return orderedSurvivors[0].caseId;

  const fallbackIndex = direction === 'next'
    ? Math.min(Math.max(attemptedIndex, 0), authoritativeCaseIds.length - 1)
    : Math.min(Math.max(attemptedIndex - 1, 0), authoritativeCaseIds.length - 1);
  return authoritativeCaseIds[fallbackIndex] || null;
}

export function mixedQueueForFocusedCase(
  response: MixedPlayerFocusedCaseResponse,
): MixedPlayersReviewQueue | null {
  if (!response.case) return null;
  return {
    schema_version: response.schema_version,
    mode: response.mode,
    match_id: response.match_id,
    summary: {
      total: 1,
      unresolved: 1,
      unresolved_total: 1,
      nonblocking_by_scope: 0,
      resolved: 0,
      complex_unresolved: response.case.resolution_status === 'unresolved_complex_mix' ? 1 : 0,
    },
    assignment_options: response.assignment_options,
    cases: [response.case],
  };
}
