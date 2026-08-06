import type { ReviewedIdentityAt, ReviewedOutputJob, ReviewedStatsResponse } from '../types';

export type ReviewedDerivedOutputState = {
  job: ReviewedOutputJob | null;
  stats: ReviewedStatsResponse | null;
  atTime: ReviewedIdentityAt | null;
};

export function clearReviewedDerivedOutput(): ReviewedDerivedOutputState {
  return { job: null, stats: null, atTime: null };
}
