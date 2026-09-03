import type { ReviewedIdentityCoverageReadiness } from '../types';

type TeamAttributionBlocker = {
  code?: unknown;
  units?: unknown;
  observations?: unknown;
};

function positiveInteger(value: unknown): number | null {
  const number = typeof value === 'number' ? value : Number(value);
  return Number.isInteger(number) && number > 0 ? number : null;
}

export function teamAttributionBlockerMessage(
  readiness: ReviewedIdentityCoverageReadiness | null,
): string | null {
  const blocker = readiness?.blockers.find((item) => (
    ['team_attribution_evidence_unavailable', 'team_attribution_residual_exceeds_tolerance', 'team_attribution_evidence_technical_failure']
      .includes(String((item as TeamAttributionBlocker).code || ''))
  )) as TeamAttributionBlocker | undefined;
  if (String(blocker?.code || '') === 'team_attribution_evidence_technical_failure') {
    return 'Nie udało się przygotować bezpiecznych widoków dla nierozstrzygniętych obserwacji. Sprawdź dostępność pliku wideo i artefaktów analizy; Review pozostaje zablokowany, aby nie ukryć problemu jakości danych.';
  }
  const units = positiveInteger(blocker?.units);
  const observations = positiveInteger(blocker?.observations);
  if (!units || !observations) return null;
  const unitWord = units === 1 ? 'jednostce' : 'jednostkach';
  return `Pozostało ${observations} obserwacji bez przypisanej drużyny w ${units} ${unitWord} Review. System nie ma dla nich bezpiecznych widoków do decyzji.`;
}

export function reviewRecomputeMessage(
  remainingCases: number,
  coverageBlocked: boolean,
): string {
  if (remainingCases > 0) {
    return `Po przeliczeniu pozostały ${remainingCases} przypadki do sprawdzenia.`;
  }
  if (coverageBlocked) {
    return 'Review zostało przeliczone, ale nadal nie można go zakończyć: brakuje bezpiecznych widoków dla nierozstrzygniętych obserwacji.';
  }
  return 'Review zostało przeliczone.';
}
