import type { PossessionTimelinePoint } from '../types';

export type PossessionTimelineShares = {
  teamA: number;
  teamB: number;
  other: number;
};

export function possessionTimelineShares(point: PossessionTimelinePoint): PossessionTimelineShares {
  const teamA = Math.max(0, point.team_controlled_frames?.A || 0);
  const teamB = Math.max(0, point.team_controlled_frames?.B || 0);
  const total = Math.max(1, point.frames || teamA + teamB);
  const aShare = clamp01(teamA / total);
  const bShare = clamp01(teamB / total);
  return {
    teamA: aShare,
    teamB: bShare,
    other: clamp01(1 - aShare - bShare),
  };
}

export function formatTimelineTime(seconds: number): string {
  const safeSeconds = Math.max(0, Math.round(seconds || 0));
  const minutes = Math.floor(safeSeconds / 60);
  const rest = safeSeconds % 60;
  return `${minutes}:${String(rest).padStart(2, '0')}`;
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(1, value));
}
