export class ApiRequestError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(status: number, detail: string, code?: string | null) {
    super(`${status}: ${detail}`);
    this.name = 'ApiRequestError';
    this.status = status;
    this.code = code || null;
  }
}

const RECOVERABLE_REVIEW_QUEUE_CONFLICT_CODES = new Set([
  'review_state_stale',
  'review_unit_already_decided',
  'review_unit_not_actionable',
  'review_queue_stale',
  'review_target_stale',
]);

export function isRecoverableReviewQueueConflict(error: unknown): boolean {
  return error instanceof ApiRequestError
    && error.status === 409
    && error.code !== null
    && RECOVERABLE_REVIEW_QUEUE_CONFLICT_CODES.has(error.code);
}

export function isTemporalSplitNotSeparable(error: unknown): boolean {
  return error instanceof ApiRequestError
    && error.status === 409
    && error.code === 'temporal_split_not_separable';
}

const RECOVERABLE_CONCURRENT_LANE_CONFLICT_CODES = new Set([
  'mixed_player_case_stale',
  'review_target_stale',
  'material_continuity_target_stale',
  'concurrent_lane_topology_stale',
  'concurrent_lane_set_stale',
  'concurrent_lane_source_stale',
  'concurrent_lane_target_stale',
  'concurrent_lane_resolution_conflict',
]);

export function isRecoverableConcurrentLaneConflict(error: unknown): boolean {
  return error instanceof ApiRequestError
    && error.status === 409
    && error.code !== null
    && RECOVERABLE_CONCURRENT_LANE_CONFLICT_CODES.has(error.code);
}
