import type { IdentityRosterSubjectAnchorCrop, MixedPlayerCase, MixedSegmentAssignment } from '../types';

export type MixedSegment = {
  index: number;
  frameStart: number;
  frameEnd: number;
  cropFrames: number[];
};

export type MixedAssignmentRemap = {
  assignments: Array<MixedSegmentAssignment | null>;
  clearedAssignments: MixedSegmentAssignment[];
  requiresConfirmation: boolean;
};

export function toggleMixedBoundary(boundaries: number[], frame: number): number[] {
  return boundaries.includes(frame)
    ? boundaries.filter((value) => value !== frame)
    : [...boundaries, frame].sort((left, right) => left - right);
}

export function mixedSegments(reviewCase: MixedPlayerCase, boundaries: number[]): MixedSegment[] {
  const sorted = [...new Set(boundaries)].sort((left, right) => left - right);
  const edges = [reviewCase.frame_start, ...sorted.map((frame) => frame + 1), reviewCase.frame_end + 1];
  const cropFrames = reviewCase.temporal_evidence.anchor_crops.map((crop) => crop.frame);
  return edges.slice(0, -1).map((start, index) => ({
    index,
    frameStart: start,
    frameEnd: edges[index + 1] - 1,
    cropFrames: cropFrames.filter((frame) => frame >= start && frame < edges[index + 1]),
  }));
}

export function remapMixedAssignments(
  reviewCase: MixedPlayerCase,
  previousBoundaries: number[],
  nextBoundaries: number[],
  previousAssignments: Array<MixedSegmentAssignment | null>,
): MixedAssignmentRemap {
  const previousSegments = mixedSegments(reviewCase, previousBoundaries);
  const nextSegments = mixedSegments(reviewCase, nextBoundaries);

  if (previousSegments.length === nextSegments.length) {
    return {
      assignments: nextSegments.map((_, index) => previousAssignments[index] || null),
      clearedAssignments: [],
      requiresConfirmation: false,
    };
  }

  const clearedAssignments: MixedSegmentAssignment[] = [];
  const previousOverlapCounts = previousSegments.map((previousSegment) => nextSegments.filter(
    (nextSegment) => previousSegment.frameStart <= nextSegment.frameEnd && previousSegment.frameEnd >= nextSegment.frameStart,
  ).length);
  const assignments = nextSegments.map((nextSegment) => {
    const overlapping = previousSegments
      .map((segment, index) => ({ segment, assignment: previousAssignments[index] || null }))
      .filter(({ segment }) => segment.frameStart <= nextSegment.frameEnd && segment.frameEnd >= nextSegment.frameStart)
      .map(({ segment, assignment }) => ({
        assignment,
        previousIndex: previousSegments.indexOf(segment),
      }));
    const assigned = overlapping
      .map(({ assignment }) => assignment)
      .filter((assignment): assignment is MixedSegmentAssignment => assignment !== null);
    const distinct = uniqueAssignments(assigned);
    const mapsOneToOne = overlapping.length === 1
      && previousOverlapCounts[overlapping[0].previousIndex] === 1;
    if (mapsOneToOne) return overlapping[0].assignment;
    const allSameAssigned = overlapping.length > 1
      && assigned.length === overlapping.length
      && distinct.length === 1;
    if (allSameAssigned) return distinct[0];
    if (assigned.length > 0) clearedAssignments.push(...distinct);
    return null;
  });

  return {
    assignments,
    clearedAssignments: uniqueAssignments(clearedAssignments),
    requiresConfirmation: clearedAssignments.length > 0,
  };
}

export function replaceMixedBoundaryInInterval(
  boundaries: number[],
  intervalStart: number,
  intervalEnd: number,
  nextFrame: number,
): number[] {
  return [
    ...boundaries.filter((frame) => frame < intervalStart || frame >= intervalEnd),
    nextFrame,
  ].sort((left, right) => left - right);
}

export function sortedMixedEvidenceCrops<T extends IdentityRosterSubjectAnchorCrop>(crops: T[]): T[] {
  return [...crops].sort((left, right) => left.frame - right.frame || left.anchor_crop_id.localeCompare(right.anchor_crop_id));
}

function uniqueAssignments(assignments: MixedSegmentAssignment[]): MixedSegmentAssignment[] {
  const seen = new Set<string>();
  return assignments.filter((assignment) => {
    const key = JSON.stringify(assignment, Object.keys(assignment).sort());
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function mixedFramesPerSecond(reviewCase: MixedPlayerCase): number | null {
  const samples = reviewCase.temporal_evidence.anchor_crops
    .filter((crop): crop is typeof crop & { time_sec: number } => Number.isFinite(crop.frame) && Number.isFinite(crop.time_sec))
    .sort((left, right) => left.frame - right.frame);
  const first = samples[0];
  const last = samples[samples.length - 1];
  if (first && last && last.frame > first.frame && last.time_sec > first.time_sec) {
    return (last.frame - first.frame) / (last.time_sec - first.time_sec);
  }
  const anchored = samples.find((crop) => crop.frame > 0 && crop.time_sec > 0);
  return anchored ? anchored.frame / anchored.time_sec : null;
}

export function mixedTimeForFrame(reviewCase: MixedPlayerCase, frame: number): number | null {
  const framesPerSecond = mixedFramesPerSecond(reviewCase);
  return framesPerSecond && framesPerSecond > 0 ? frame / framesPerSecond : null;
}

export function validMixedResolution(
  reviewCase: MixedPlayerCase,
  boundaries: number[],
  assignments: Array<MixedSegmentAssignment | null>,
): boolean {
  const segments = mixedSegments(reviewCase, boundaries);
  return boundaries.length > 0
    && boundaries.every((frame) => frame >= reviewCase.frame_start && frame < reviewCase.frame_end)
    && segments.every((segment) => segment.frameStart <= segment.frameEnd)
    && assignments.length === segments.length
    && assignments.every(Boolean);
}

export function assignmentLabel(assignment: MixedSegmentAssignment | null, rosterName?: string): string {
  if (!assignment) return 'Nie przypisano';
  if (assignment.action === 'assign_roster_player') return rosterName || 'Zawodnik z kadry';
  if (assignment.action === 'assign_existing_slot') return assignment.stable_slot_id || 'Istniejący zawodnik';
  if (assignment.action === 'create_new_stable_player') return `Nowy zawodnik Team ${assignment.team_label}`;
  if (assignment.action === 'assign_team') return `Team ${assignment.team_label} — zawodnik nieznany`;
  const labels = { referee: 'Sędzia', false_detection: 'Fałszywa detekcja', team_unknown: 'Nieznana drużyna', unresolved: 'Nie wiem' };
  return labels[assignment.action as keyof typeof labels] || assignment.action;
}

export function mixedQueueAfterSuccessfulSave(
  cases: MixedPlayerCase[],
  savedSubjectId: string,
  currentIndex: number,
): { cases: MixedPlayerCase[]; index: number } {
  const remaining = cases.filter((item) => item.candidate_subject_id !== savedSubjectId);
  return {
    cases: remaining,
    index: Math.min(currentIndex, Math.max(0, remaining.length - 1)),
  };
}
