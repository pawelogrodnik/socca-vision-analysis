import type { Match, ReviewedIdentityCoverageDebt } from '../types';
import { matchTeamName } from '../utils/identityExceptionTeamFilter';
import { formatReviewedIdentityPercentagePoints } from '../utils/reviewedIdentityMaxPresentation';

type Props = {
  match: Match;
  debt: ReviewedIdentityCoverageDebt;
  mixedLocked: boolean;
};

const rows = [
  ['committed_pending', 'Zapisane decyzje', false],
  ['required', 'Wymagane', false],
  ['mixed', 'Mixed Players', true],
  ['optional_max', 'Opcjonalny MAX', true],
  ['unavailable', 'Bez bezpiecznej ścieżki', false],
] as const;

export function ReviewedIdentityCoverageDebtSummary({ match, debt, mixedLocked }: Props) {
  const teams = Object.entries(debt.per_team).filter(([team]) => team === 'A' || team === 'B');
  const hasDebt = teams.some(([, team]) => team.unnamed_observations > 0);
  if (!hasDebt) return null;

  return <section className='identity-coverage-debt' aria-label='Wyjaśnienie pozostałego pokrycia'>
    <header>
      <strong>Gdzie jest pozostałe pokrycie</strong>
      <span>Udział bieżących obserwacji; „do” nie oznacza gwarantowanego nazwiska.</span>
    </header>
    {teams.map(([teamLabel, team]) => {
      const teamName = matchTeamName(match.teams || [], teamLabel as 'A' | 'B');
      const visibleRows = rows.filter(([key]) => team.buckets[key].unique_observations > 0);
      const isTeamStatsOnly = team.scope === 'team_stats_only';
      return <div className='identity-coverage-debt-team' key={teamLabel}>
        <div className='identity-coverage-debt-heading'>
          <strong>{teamName}</strong>
          {!isTeamStatsOnly && team.target_named_coverage != null && <span>Cel Required: {(team.target_named_coverage * 100).toFixed(0)}%</span>}
          {isTeamStatsOnly && <span>Statystyki drużyny — imiona informacyjnie</span>}
        </div>
        {!isTeamStatsOnly && team.buckets.required.unique_observations === 0 && team.current_named_coverage != null && team.target_named_coverage != null && team.current_named_coverage < team.target_named_coverage && <p>Brak zwykłych wymaganych przypadków {teamName}.</p>}
        {visibleRows.map(([key, label, potential]) => {
          const bucket = team.buckets[key];
          return <div className='identity-coverage-debt-row' key={key}>
            <span>{label}</span>
            <strong>{potential ? `do ${formatReviewedIdentityPercentagePoints(bucket.coverage_pp)}` : formatReviewedIdentityPercentagePoints(bucket.coverage_pp)}</strong>
            <small>{bucket.unique_observations} obserwacji{bucket.case_count ? ` · ${bucket.case_count} przypadków` : ''}</small>
          </div>;
        })}
        {team.buckets.mixed.unique_observations > 0 && mixedLocked && <p>Mixed Players stanie się dostępne po zakończeniu wymaganych przypadków.</p>}
        {team.unaccounted_unnamed_observations !== 0 && <p className='identity-coverage-debt-diagnostic'>Diagnostyka: {team.unaccounted_unnamed_observations} nieprzypisanych obserwacji.</p>}
      </div>;
    })}
    {debt.ambiguous.mixed_case_count > 0 && <p className='identity-coverage-debt-ambiguous'>Dodatkowo {debt.ambiguous.mixed_case_count} przypadki Mixed nie mają jeszcze pewnej drużyny.</p>}
  </section>;
}
