import type { ReviewedCorrectionContext } from '../types';

type Entry = {
  value?: ReviewedCorrectionContext;
  pending?: Promise<ReviewedCorrectionContext>;
};

type ContextLoader = (
  matchId: string,
  subjectId: string,
  reviewTargetId?: string | null,
) => Promise<ReviewedCorrectionContext>;

export function createReviewedCorrectionContextCache(loader: ContextLoader) {
  const entries = new Map<string, Entry>();
  const sourceGenerations = new Map<string, number>();
  const key = (matchId: string, subjectId: string, reviewTargetId?: string | null) => (
    `${matchId}:${subjectId}:${reviewTargetId || ''}`
  );
  const generation = (cacheKey: string) => sourceGenerations.get(cacheKey) || 0;
  const invalidateKey = (cacheKey: string) => {
    sourceGenerations.set(cacheKey, generation(cacheKey) + 1);
    entries.delete(cacheKey);
  };
  const load = (matchId: string, subjectId: string, reviewTargetId?: string | null) => {
    const cacheKey = key(matchId, subjectId, reviewTargetId);
    const entry = entries.get(cacheKey) || {};
    if (entry.value) return Promise.resolve(entry.value);
    if (entry.pending) return entry.pending;
    const entryGeneration = generation(cacheKey);
    const pending = loader(matchId, subjectId, reviewTargetId)
      .then((value) => {
        if (generation(cacheKey) === entryGeneration) entries.set(cacheKey, { value });
        return value;
      })
      .catch((error: unknown) => {
        if (generation(cacheKey) === entryGeneration) entries.delete(cacheKey);
        throw error;
      });
    entries.set(cacheKey, { pending });
    return pending;
  };
  return {
    load,
    prefetch: (matchId: string, subjectId?: string, reviewTargetId?: string | null) => {
      if (subjectId) void load(matchId, subjectId, reviewTargetId);
    },
    invalidate: (matchId: string, subjectId?: string, reviewTargetId?: string | null) => {
      if (!subjectId) {
        for (const cacheKey of entries.keys()) {
          if (cacheKey.startsWith(`${matchId}:`)) invalidateKey(cacheKey);
        }
        return;
      }
      invalidateKey(key(matchId, subjectId, reviewTargetId));
    },
    invalidateOlderState: (matchId: string, stateVersion?: number) => {
      if (stateVersion == null) return;
      for (const [cacheKey, entry] of entries) {
        if (!cacheKey.startsWith(`${matchId}:`)) continue;
        if (!entry.value || (entry.value.review_state_version ?? -1) < stateVersion) {
          invalidateKey(cacheKey);
        }
      }
    },
    resetForTests: () => {
      entries.clear();
      sourceGenerations.clear();
    },
  };
}
