export function formatReviewedIdentityPercent(value: number | null | undefined): string {
  const numeric = Number(value);
  const bounded = Number.isFinite(numeric) ? Math.min(1, Math.max(0, numeric)) : 0;
  return `${(bounded * 100).toFixed(1)}%`;
}

export function formatReviewedIdentityPercentagePoints(value: number | null | undefined): string {
  const numeric = Number(value);
  const bounded = Number.isFinite(numeric) ? Math.max(0, numeric) : 0;
  return `+${bounded.toFixed(1)} pp`;
}

export function formatOptionalCaseCount(count: number | null | undefined): string {
  const numeric = Math.max(0, Math.floor(Number(count) || 0));
  if (numeric === 1) return '1 opcjonalny przypadek';
  if (numeric >= 2 && numeric <= 4) return `${numeric} opcjonalne przypadki`;
  return `${numeric} opcjonalnych przypadków`;
}
