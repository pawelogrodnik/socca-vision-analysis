import type { PublicCanonicalShot, ShotOutcome } from '../types';

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

export function hasPublicShotLocation(shot: PublicCanonicalShot): shot is PublicCanonicalShot & { location_m: { x: number; y: number } } {
  return Number.isFinite(shot.location_m?.x) && Number.isFinite(shot.location_m?.y);
}
