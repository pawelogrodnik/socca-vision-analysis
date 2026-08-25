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
