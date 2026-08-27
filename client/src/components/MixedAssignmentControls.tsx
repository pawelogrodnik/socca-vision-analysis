import type { Match, MixedPlayersReviewQueue, MixedSegmentAssignment } from '../types';
import { matchTeamName } from '../utils/identityExceptionTeamFilter';

type Props = {
  assignment: MixedSegmentAssignment | null;
  options: MixedPlayersReviewQueue['assignment_options'];
  teams: Match['teams'];
  onAssign: (assignment: MixedSegmentAssignment) => void;
};

export function MixedAssignmentControls({ assignment, options, teams, onAssign }: Props) {
  return <div className='mixed-assignment-controls'>
    <label className='mixed-primary-assignment'>Zawodnik z kadry
      <select
        aria-label='Zawodnik z kadry'
        value={assignment?.action === 'assign_roster_player' ? assignment.player_id : ''}
        onChange={(event) => event.target.value && onAssign({ action: 'assign_roster_player', player_id: event.target.value })}
      >
        <option value=''>Wybierz zawodnika</option>
        {(['A', 'B'] as const).map((team) => <optgroup key={team} label={matchTeamName(teams || [], team)}>
          {options.roster.filter((player) => player.team_label === team).map((player) => <option key={player.player_id} value={player.player_id}>
            {player.player_name}{player.roster_number ? ` #${player.roster_number}` : ''}
          </option>)}
        </optgroup>)}
      </select>
    </label>
    <label>Ten sam co wcześniej
      <select
        aria-label='Ten sam co wcześniej'
        value={assignment?.action === 'assign_existing_slot' ? assignment.stable_slot_id : ''}
        onChange={(event) => event.target.value && onAssign({ action: 'assign_existing_slot', stable_slot_id: event.target.value })}
      >
        <option value=''>Wybierz wcześniej rozpoznanego gracza</option>
        {options.slots.map((slot) => <option key={slot.stable_slot_id} value={slot.stable_slot_id}>{slot.stable_slot_id}</option>)}
      </select>
    </label>
    <details className='mixed-other-assignments'>
      <summary>Inne przypisanie</summary>
      <div className='mixed-other-assignment-actions'>
        <button type='button' onClick={() => onAssign({ action: 'assign_team', team_label: 'A' })}>{matchTeamName(teams || [], 'A')} — zawodnik nieznany</button>
        <button type='button' onClick={() => onAssign({ action: 'assign_team', team_label: 'B' })}>{matchTeamName(teams || [], 'B')} — zawodnik nieznany</button>
        <button type='button' onClick={() => onAssign({ action: 'create_new_stable_player', team_label: 'A' })}>Nowy zawodnik ({matchTeamName(teams || [], 'A')})</button>
        <button type='button' onClick={() => onAssign({ action: 'create_new_stable_player', team_label: 'B' })}>Nowy zawodnik ({matchTeamName(teams || [], 'B')})</button>
        <button type='button' onClick={() => onAssign({ action: 'referee' })}>Sędzia</button>
        <button type='button' onClick={() => onAssign({ action: 'false_detection' })}>Fałszywa detekcja</button>
        <button type='button' onClick={() => onAssign({ action: 'team_unknown' })}>Nieznana drużyna</button>
        <button type='button' onClick={() => onAssign({ action: 'unresolved' })}>Nie wiem</button>
      </div>
    </details>
  </div>;
}
