import type { Match, ReviewedIdentityCoverageDebt } from '../types';
import { matchTeamName } from '../utils/identityExceptionTeamFilter';
import { formatReviewedIdentityPercentagePoints } from '../utils/reviewedIdentityMaxPresentation';
import {
  coverageDebtPresentationTeams,
  requiredBreakdownLabel,
} from '../utils/reviewedIdentityCoverageDebtPresentation';

type Props = {
  match: Match;
  debt: ReviewedIdentityCoverageDebt;
};

const rows = [
  ['committed_pending', 'Zapisane decyzje', false],
  ['required', 'Wymagane', false],
  ['mixed', 'Mixed Players', true],
  ['optional_max', 'Opcjonalny MAX', true],
  ['unavailable', 'Bez bezpiecznej ścieżki', false],
] as const;

export function ReviewedIdentityCoverageDebtSummary({ match, debt }: Props) {
  const teams = coverageDebtPresentationTeams(debt).filter((item) => item.show);
  if (teams.length === 0) return null;

  return <section className='identity-coverage-debt' aria-label='Wyjaśnienie pozostałego pokrycia'>
    <header>
      <strong>Gdzie jest pozostałe pokrycie</strong>
      <span>Udział bieżących obserwacji; „do” nie oznacza gwarantowanego nazwiska.</span>
    </header>
    {teams.map(({ teamLabel, team, isTeamStatsOnly, actualRequired }) => {
      const teamName = matchTeamName(match.teams || [], teamLabel);
      const visibleRows = rows.filter(([key]) => (key === 'required' || !isTeamStatsOnly)
        && (team.buckets[key].unique_observations > 0 || (key === 'required' && team.buckets.required.case_count > 0)));
      return <div className='identity-coverage-debt-team' key={teamLabel}>
        <div className='identity-coverage-debt-heading'>
          <strong>{teamName}</strong>
          {!isTeamStatsOnly && team.target_named_coverage != null && <span>Cel Required: {(team.target_named_coverage * 100).toFixed(0)}%</span>}
          {isTeamStatsOnly && <span>Statystyki drużyny</span>}
        </div>
        {isTeamStatsOnly && <p>Rozpoznanie zawodników tej drużyny nie jest wymagane.</p>}
        <p>Dług tożsamości operatora: {team.operator_identity_debt_observations} obserwacji.</p>
        {isTeamStatsOnly && actualRequired && actualRequired.total_cases > 0 && <div className='identity-coverage-debt-actual-required'>
          <strong>Bieżąca kolejka Required: {actualRequired.total_cases}</strong>
          {Object.entries(actualRequired.breakdown).filter(([, item]) => item.case_count > 0).map(([kind, item]) => <small key={kind}>
            {requiredBreakdownLabel(kind as 'semantic' | 'continuity' | 'coverage')}: {item.case_count}
          </small>)}
          {actualRequired.unexpected_by_scope > 0 && <p>{actualRequired.unexpected_by_scope} przypadków w bieżącej kolejce wykracza poza zakres rozpoznawania zawodników tej drużyny.</p>}
        </div>}
        {!isTeamStatsOnly && team.buckets.required.unique_observations === 0 && team.current_named_coverage != null && team.target_named_coverage != null && team.current_named_coverage < team.target_named_coverage && <p>Brak zwykłych wymaganych przypadków {teamName}.</p>}
        {visibleRows.map(([key, label, potential]) => {
          const bucket = team.buckets[key];
          return <div className='identity-coverage-debt-row' key={key}>
            <span>{label}</span>
            <strong>{potential ? `do ${formatReviewedIdentityPercentagePoints(bucket.coverage_pp)}` : formatReviewedIdentityPercentagePoints(bucket.coverage_pp)}</strong>
            <small>{bucket.unique_observations} obserwacji{bucket.case_count ? ` · ${bucket.case_count} przypadków` : ''}</small>
          </div>;
        })}
        {team.buckets.required.case_count > 0 && <div className='identity-coverage-debt-breakdown'>
          {Object.entries(team.buckets.required.breakdown || {}).filter(([, item]) => item.case_count > 0).map(([kind, item]) => <small key={kind}>
            {requiredBreakdownLabel(kind as 'semantic' | 'continuity' | 'coverage')}: {item.case_count} przypadków · {item.unique_observations} obserwacji
          </small>)}
        </div>}
        {team.buckets.mixed.unique_observations > 0 && <p>Zmieszanych graczy można rozwiązywać równolegle z pozostałymi przypadkami.</p>}
        {team.unaccounted_unnamed_observations !== 0 && <p className='identity-coverage-debt-diagnostic'>Diagnostyka: {team.unaccounted_unnamed_observations} nieprzypisanych obserwacji.</p>}
      </div>;
    })}
    {debt.ambiguous.mixed_case_count > 0 && <div className='identity-coverage-debt-ambiguous'>
      <p>Dodatkowo {debt.ambiguous.mixed_case_count} przypadki Mixed nie mają jeszcze pewnej drużyny.</p>
      <small>Bieżące etykiety diagnostyczne: A {debt.ambiguous.currently_labeled.A || 0}, B {debt.ambiguous.currently_labeled.B || 0}; {debt.ambiguous.unique_current_reliable_observations} unikalnych bieżących obserwacji.</small>
      <small>Telemetria markerów Mixed: {debt.ambiguous.raw_marker_observations} obserwacji źródłowych.</small>
    </div>}
  </section>;
}
