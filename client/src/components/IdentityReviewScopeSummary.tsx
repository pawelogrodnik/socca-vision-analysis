import type { IdentityReviewScope, Team } from '../types';


type Props = {
  teams: Team[];
  scope?: IdentityReviewScope | null;
};


export function IdentityReviewScopeSummary({ teams, scope }: Props) {
  return <section className='identity-review-scope-summary' aria-label='Zakres Reviewed Identity'>
    <strong>Zakres Review</strong>
    {(['A', 'B'] as const).map((teamLabel, index) => {
      const teamName = teams[index]?.name?.trim() || `Team ${teamLabel}`;
      return <span key={teamLabel}>
        {teamName} — {scope?.teams[teamLabel] === 'team_stats_only'
          ? 'tylko statystyki drużynowe'
          : 'statystyki zawodników'}
      </span>;
    })}
  </section>;
}
