import type { CanonicalShot, PublicMatchReport } from '../types';
import { formatKeyMomentTime } from '../lib/keyMomentTime';

type Props = {
  shots: CanonicalShot[];
  report: PublicMatchReport;
  onPlayAt: (timeSec: number) => void;
  onAdd: () => void;
  onEdit: (shot: CanonicalShot) => void;
  onDelete: (shot: CanonicalShot) => void;
};

const outcomeLabels = {
  goal: 'Gol',
  on_target: 'Celny',
  off_target: 'Niecelny',
  blocked: 'Zablokowany',
} as const;

const locationLabels = {
  ball: 'Pozycja z piłki',
  player: 'Pozycja zawodnika',
  manual: 'Pozycja ręczna',
  unavailable: 'Brak pozycji',
} as const;

export function AcceptedShots({ shots, report, onPlayAt, onAdd, onEdit, onDelete }: Props) {
  const teamNames = new Map(report.teams.map((team) => [team.team_id, team.team_name || team.team_label || team.team_id]));
  const playerNames = new Map(report.players.map((player) => [player.player_id, player.player_name || player.player_id]));
  return <section className='operator-key-moments-list' aria-label='Zaakceptowane strzały'>
    <div className='row between'>
      <p className='muted'>Zaakceptowane strzały ({shots.length})</p>
      <button type='button' onClick={onAdd}>+ Dodaj strzał</button>
    </div>
    {shots.map((shot) => <article className='redesign-moment-row' key={shot.shot_id}>
      <time>{formatKeyMomentTime(shot.time_sec)}</time>
      <div>
        <h3>{outcomeLabels[shot.outcome]}</h3>
        <p>{teamNames.get(shot.team_id) || shot.team_id}{shot.player_id ? ` · ${playerNames.get(shot.player_id) || shot.player_id}` : ''}</p>
        <p>{locationLabels[shot.location_source]}</p>
      </div>
      <div className='key-moment-action-list'>
        <button type='button' className='key-moment-action-button' aria-label='Odtwórz' title='Odtwórz' onClick={() => onPlayAt(shot.time_sec)}><span aria-hidden='true'>▶</span></button>
        <button type='button' className='key-moment-action-button' aria-label='Edytuj' title='Edytuj' onClick={() => onEdit(shot)}><span aria-hidden='true'>✎</span></button>
        <button type='button' className='key-moment-action-button danger' aria-label='Usuń' title='Usuń' onClick={() => onDelete(shot)}><span aria-hidden='true'>×</span></button>
      </div>
    </article>)}
    {!shots.length ? <p className='redesign-empty-state'>Brak zaakceptowanych strzałów.</p> : null}
  </section>;
}
