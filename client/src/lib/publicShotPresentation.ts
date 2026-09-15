import type { PublicCanonicalShot, ShotOutcome } from '../types';

export type PublicShotOutcomeFilter = 'all' | ShotOutcome;

export type PublicShotSummary = {
  total: number;
  onTarget: number;
  offTarget: number;
  accuracyPercent: number | null;
};

export const publicShotOutcomeLabels: Record<ShotOutcome, string> = {
  goal: 'Gol',
  on_target: 'Strzał celny',
  off_target: 'Strzał niecelny',
  blocked: 'Strzał zablokowany',
};

export function publicShotsForTeam(shots: PublicCanonicalShot[], teamId: string): PublicCanonicalShot[] {
  return shots.filter((shot) => shot.team_id === teamId);
}

export function matchesPublicShotOutcomeFilter(shot: PublicCanonicalShot, filter: PublicShotOutcomeFilter): boolean {
  if (filter === 'all') return true;
  return filter === 'on_target'
    ? shot.outcome === 'on_target' || shot.outcome === 'goal'
    : shot.outcome === filter;
}

export function publicShotSummary(shots: PublicCanonicalShot[]): PublicShotSummary {
  const onTarget = shots.filter((shot) => shot.outcome === 'goal' || shot.outcome === 'on_target').length;
  const offTarget = shots.filter((shot) => shot.outcome === 'off_target').length;
  return {
    total: shots.length,
    onTarget,
    offTarget,
    accuracyPercent: shots.length ? Math.round((onTarget / shots.length) * 1000) / 10 : null,
  };
}

export function hasPublicShotMapLocation(shot: PublicCanonicalShot): shot is PublicCanonicalShot & { map_location: { x: number; y: number } } {
  const location = shot.map_location;
  return Number.isFinite(location?.x)
    && Number.isFinite(location?.y)
    && location != null
    && location.x >= 0
    && location.x <= 1
    && location.y >= 0
    && location.y <= 1;
}
