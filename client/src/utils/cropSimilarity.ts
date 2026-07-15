import type { IdentityCropReviewCrop } from '../types';

function rootMeanSquareDistance(left: number[], right: number[]): number | null {
  if (left.length === 0 || left.length !== right.length) return null;
  const squared = left.reduce((sum, value, index) => sum + (value - right[index]) ** 2, 0);
  return Math.sqrt(squared / left.length);
}

function histogramDistance(left: number[], right: number[]): number | null {
  if (left.length === 0 || left.length !== right.length) return null;
  const coefficient = left.reduce(
    (sum, value, index) => sum + Math.sqrt(Math.max(0, value) * Math.max(0, right[index])),
    0,
  );
  return Math.sqrt(Math.max(0, 1 - Math.min(1, coefficient)));
}

export function cropSimilarityDistance(
  seed: IdentityCropReviewCrop,
  candidate: IdentityCropReviewCrop,
): number | null {
  if (seed.artifact === candidate.artifact) return 0;
  const visual = rootMeanSquareDistance(
    seed.similarity_descriptor || [],
    candidate.similarity_descriptor || [],
  );
  const appearance = histogramDistance(
    seed.appearance_signature || [],
    candidate.appearance_signature || [],
  );
  if (visual === null) return appearance;
  if (appearance === null) return visual;
  return visual * 0.78 + appearance * 0.22;
}

export function sortCropsBySimilarity(
  crops: IdentityCropReviewCrop[],
  seedArtifact: string | null,
): IdentityCropReviewCrop[] {
  if (!seedArtifact) return crops;
  const seed = crops.find((crop) => crop.artifact === seedArtifact);
  if (!seed) return crops;
  return [...crops].sort((left, right) => {
    const leftDistance = cropSimilarityDistance(seed, left) ?? Number.POSITIVE_INFINITY;
    const rightDistance = cropSimilarityDistance(seed, right) ?? Number.POSITIVE_INFINITY;
    return leftDistance - rightDistance || left.frame - right.frame;
  });
}
