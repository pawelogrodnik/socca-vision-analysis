import type {
  Match,
  MixedPlayersReviewQueue,
  MixedSegmentAssignment,
  ReviewedCorrectionActionCapability,
  ReviewedCorrectionPrimaryAction,
} from '../types';
import { matchTeamName } from '../utils/identityExceptionTeamFilter';

type Props = {
  assignment: MixedSegmentAssignment | null;
  options: MixedPlayersReviewQueue['assignment_options'];
  teams: Match['teams'];
  capabilities: Partial<Record<ReviewedCorrectionPrimaryAction, ReviewedCorrectionActionCapability>> | undefined;
  onAssign: (assignment: MixedSegmentAssignment) => void;
};

export function MixedAssignmentControls({ assignment, options, teams, capabilities, onAssign }: Props) {
  const allowed = (action: MixedSegmentAssignment['action']) => capabilities?.[action]?.allowed === true;
  const hasOtherAssignments = [
    'assign_team',
    'create_new_stable_player',
    'referee',
    'false_detection',
    'team_unknown',
    'unresolved',
  ].some((action) => allowed(action as MixedSegmentAssignment['action']));
  return <div className='mixed-assignment-controls'>
    {allowed('assign_roster_player') && <label className='mixed-primary-assignment'>Zawodnik z kadry
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
    </label>}
    {allowed('assign_existing_slot') && <label>Ten sam co wcześniej
      <select
        aria-label='Ten sam co wcześniej'
        value={assignment?.action === 'assign_existing_slot' ? assignment.stable_slot_id : ''}
        onChange={(event) => event.target.value && onAssign({ action: 'assign_existing_slot', stable_slot_id: event.target.value })}
      >
        <option value=''>Wybierz wcześniej rozpoznanego gracza</option>
        {options.slots.map((slot) => <option key={slot.stable_slot_id} value={slot.stable_slot_id}>{slot.stable_slot_id}</option>)}
      </select>
    </label>}
    {hasOtherAssignments && <details className='mixed-other-assignments'>
      <summary>Inne przypisanie</summary>
      <div className='mixed-other-assignment-actions'>
        {allowed('assign_team') && <><button type='button' onClick={() => onAssign({ action: 'assign_team', team_label: 'A' })}>{matchTeamName(teams || [], 'A')} — zawodnik nieznany</button><button type='button' onClick={() => onAssign({ action: 'assign_team', team_label: 'B' })}>{matchTeamName(teams || [], 'B')} — zawodnik nieznany</button></>}
        {allowed('create_new_stable_player') && <><button type='button' onClick={() => onAssign({ action: 'create_new_stable_player', team_label: 'A' })}>Nowy zawodnik ({matchTeamName(teams || [], 'A')})</button><button type='button' onClick={() => onAssign({ action: 'create_new_stable_player', team_label: 'B' })}>Nowy zawodnik ({matchTeamName(teams || [], 'B')})</button></>}
        {allowed('referee') && <button type='button' onClick={() => onAssign({ action: 'referee' })}>Sędzia</button>}
        {allowed('false_detection') && <button type='button' onClick={() => onAssign({ action: 'false_detection' })}>Fałszywa detekcja</button>}
        {allowed('team_unknown') && <button type='button' onClick={() => onAssign({ action: 'team_unknown' })}>Nieznana drużyna</button>}
        {allowed('unresolved') && <button type='button' onClick={() => onAssign({ action: 'unresolved' })}>Nie wiem</button>}
      </div>
    </details>}
  </div>;
}
