import type { IdentityReviewScopeChoice, Team } from '../types';


type Props = {
  teams: Array<Team | undefined>;
  value: IdentityReviewScopeChoice;
  onChange: (value: IdentityReviewScopeChoice) => void;
  disabled?: boolean;
};


export function IdentityReviewScopeSelector({ teams, value, onChange, disabled }: Props) {
  const teamA = teams[0]?.name?.trim() || 'Team A';
  const teamB = teams[1]?.name?.trim() || 'Team B';
  const options: Array<{ value: IdentityReviewScopeChoice; label: string; detail: string }> = [
    { value: 'A', label: teamA, detail: `${teamA}: statystyki zawodników · ${teamB}: tylko drużynowe` },
    { value: 'B', label: teamB, detail: `${teamB}: statystyki zawodników · ${teamA}: tylko drużynowe` },
    { value: 'both', label: 'Obie drużyny', detail: 'Pełne statystyki zawodników obu drużyn' },
  ];
  return <fieldset className='identity-review-scope-selector' disabled={disabled}>
    <legend>Statystyki zawodników</legend>
    <p className='muted'>Wybierz drużynę, dla której będziesz rozpoznawać zawodników imiennie.</p>
    <div className='identity-review-scope-options'>
      {options.map((option) => <label key={option.value}>
        <input
          type='radio'
          name='identity-review-scope'
          value={option.value}
          checked={value === option.value}
          onChange={() => onChange(option.value)}
        />
        <span><strong>{option.label}</strong><small>{option.detail}</small></span>
      </label>)}
    </div>
  </fieldset>;
}
