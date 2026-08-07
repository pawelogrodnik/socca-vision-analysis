import type { ReviewedIdentityDocument, ReviewedOutputJob } from '../types';

export function formatReviewTime(value: number | null | undefined): string {
  const seconds = Math.max(0, Number(value) || 0);
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds - minutes * 60;
  return `${String(minutes).padStart(2, '0')}:${remainingSeconds.toFixed(1).padStart(4, '0')}`;
}

export function formatElapsedTime(startedAt: string | undefined, now = Date.now()): string | null {
  if (!startedAt) return null;
  const started = Date.parse(startedAt);
  if (Number.isNaN(started)) return null;
  const seconds = Math.max(0, Math.floor((now - started) / 1000));
  const minutes = Math.floor(seconds / 60);
  return minutes > 0 ? `${minutes} min ${seconds % 60} s` : `${seconds} s`;
}

export function reviewedIdentityStatusLabel(status: ReviewedIdentityDocument['status'] | undefined): string {
  const labels: Record<ReviewedIdentityDocument['status'], string> = {
    missing: 'Review nieprzygotowane',
    partial_reviewed: 'Review rozpoczęte',
    complete_reviewed: 'Review zakończone',
    stale: 'Review wymaga odświeżenia',
    blocked: 'Review wymaga decyzji',
  };
  return status ? labels[status] : 'Ładowanie review';
}

export function reviewedRenderStatusLabel(status: ReviewedOutputJob['status'] | undefined): string {
  const labels: Record<ReviewedOutputJob['status'], string> = {
    missing: 'Wideo jeszcze niewygenerowane',
    queued: 'Wideo oczekuje na wygenerowanie',
    running: 'Trwa przygotowywanie wideo',
    completed: 'Wideo gotowe',
    stale: 'Wideo nieaktualne po poprawkach',
    failed: 'Generowanie nie powiodło się',
  };
  return status ? labels[status] : 'Ładowanie wideo';
}

export function shouldShowInitialReviewCta(
  identityStatus: ReviewedIdentityDocument['status'] | undefined,
  renderStatus: ReviewedOutputJob['status'] | undefined,
): boolean {
  return identityStatus === 'missing' || renderStatus === 'missing';
}

export function teamLabelForOperator(teamLabel: string | null | undefined): string {
  if (teamLabel === 'A') return 'Team A';
  if (teamLabel === 'B') return 'Team B';
  return 'Nieznana drużyna';
}
