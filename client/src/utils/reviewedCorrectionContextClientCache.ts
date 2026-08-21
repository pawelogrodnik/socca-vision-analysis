import { getReviewedCorrectionContext } from '../api';
import { createReviewedCorrectionContextCache } from './reviewedCorrectionContextCache';

const cache = createReviewedCorrectionContextCache(getReviewedCorrectionContext);

export const loadReviewedCorrectionContext = cache.load;
export const prefetchReviewedCorrectionContext = cache.prefetch;
export const invalidateReviewedCorrectionContext = cache.invalidate;
export const invalidateReviewedCorrectionContextsBeforeVersion = cache.invalidateOlderState;
