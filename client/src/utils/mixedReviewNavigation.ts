import type { MixedPlayerFocusedCaseResponse, MixedPlayersReviewQueue } from '../types';

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

export async function loadExactMixedFocus<
  Response extends {
    requested_case_id: string;
    status: string;
    case: { case_id?: string } | null;
  },
>(
  load: (caseId: string) => Promise<Response>,
  caseId: string,
): Promise<{ response: Response; case: NonNullable<Response['case']> } | null> {
  const response = await load(caseId);
  const visible = response.status === 'current_blocking'
    || response.status === 'stale_or_unclassifiable_blocking';
  if (
    !visible
    || response.requested_case_id !== caseId
    || response.case?.case_id !== caseId
  ) return null;
  return { response, case: response.case as NonNullable<Response['case']> };
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
