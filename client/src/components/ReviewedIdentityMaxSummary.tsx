import type { ReviewedIdentityOptionalAudit } from '../types';
import {
  formatOptionalCaseCount,
  formatReviewedIdentityPercent,
} from '../utils/reviewedIdentityMaxPresentation';

type Props = {
  teamName: string;
  summary: ReviewedIdentityOptionalAudit;
  compact?: boolean;
};

export function ReviewedIdentityMaxSummary({ teamName, summary, compact = false }: Props) {
  const unavailable = formatReviewedIdentityPercent(summary.unavailable_residual_ratio);
  const metrics = [
    ['Aktualne pokrycie', formatReviewedIdentityPercent(summary.current_named_coverage)],
    ['Po zapisanych decyzjach', formatReviewedIdentityPercent(summary.projected_named_coverage)],
    ['Wymagane minimum', `${formatReviewedIdentityPercent(summary.minimum_target_ratio)} ${summary.current_minimum_target_met ? '✓' : ''}`],
    ['Bezpieczne maksimum', formatReviewedIdentityPercent(summary.safe_max_named_coverage)],
  ];
  return <div className={`reviewed-identity-max-metrics${compact ? ' compact' : ''}`} aria-live='polite' aria-label={`Pokrycie tożsamości ${teamName}`}>
    {metrics.map(([label, value]) => <div key={label}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>)}
    <div>
      <span>Pozostało</span>
      <strong>{formatOptionalCaseCount(summary.remaining_cases)}</strong>
    </div>
    <div>
      <span>Obserwacje do bezpiecznego przypisania</span>
      <strong>{summary.actionable_unique_observations_remaining}</strong>
    </div>
    {summary.status === 'safe_max_reached' && <div>
      <span>Niedostępna pozostałość</span>
      <strong>{summary.unavailable_residual_observations} ({unavailable})</strong>
    </div>}
  </div>;
}
