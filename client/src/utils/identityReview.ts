import type { IdentityReviewCrop, IdentityReviewGalleryStint } from '../types';

export type CropFrameRange = {
  anchorFrame: number;
  focusFrame: number;
};

export function selectedCropFrames(crops: IdentityReviewCrop[], range: CropFrameRange | null): number[] {
  if (!range) return [];
  const start = Math.min(range.anchorFrame, range.focusFrame);
  const end = Math.max(range.anchorFrame, range.focusFrame);
  return crops.map((crop) => crop.frame).filter((frame) => frame >= start && frame <= end);
}

export function splitFramesForSelection(
  stint: IdentityReviewGalleryStint,
  crops: IdentityReviewCrop[],
  range: CropFrameRange | null,
): number[] {
  const selected = selectedCropFrames(crops, range);
  if (selected.length === 0) return [];
  const ordered = [...crops].sort((left, right) => left.frame - right.frame);
  const first = Math.min(...selected);
  const last = Math.max(...selected);
  const frames: number[] = [];
  if (typeof stint.start_frame === 'number' && first > stint.start_frame) {
    frames.push(first);
  }
  const nextCrop = ordered.find((crop) => crop.frame > last);
  if (nextCrop && (typeof stint.end_frame !== 'number' || nextCrop.frame <= stint.end_frame)) {
    frames.push(nextCrop.frame);
  }
  return [...new Set(frames)];
}

