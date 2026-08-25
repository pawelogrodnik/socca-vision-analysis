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

export function isReviewQueueConflict(error: unknown): boolean {
  return error instanceof ApiRequestError
    && error.status === 409
    && (error.code === 'review_unit_already_decided' || error.code === 'review_state_stale');
}
