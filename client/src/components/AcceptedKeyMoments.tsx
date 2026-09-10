import type { KeyMomentEditorialMoment, PublicMatchReport } from '../types';
import { formatKeyMomentTime } from '../lib/keyMomentTime';

type Props = {
  moments: KeyMomentEditorialMoment[];
  report: PublicMatchReport;
  onPlayAt: (timeSec: number) => void;
  onAdd: () => void;
  onEdit: (moment: KeyMomentEditorialMoment) => void;
  onDelete: (moment: KeyMomentEditorialMoment) => void;
};

export function AcceptedKeyMoments({ moments, report, onPlayAt, onAdd, onEdit, onDelete }: Props) {
  const teamNames = new Map(report.teams.map((team) => [team.team_id, team.team_name || team.team_label || team.team_id]));
  return <section className='operator-key-moments-list' aria-label='Zaakceptowane Key Moments'>
    <div className='row between'>
      <p className='muted'>Opublikowane momenty ({moments.length})</p>
      <button type='button' onClick={onAdd}>+ Dodaj moment</button>
    </div>
    {moments.map((moment) => <article className='redesign-moment-row' key={moment.moment_id}>
      <time>{formatKeyMomentTime(moment.time_sec)}</time>
      <div>
        <h3>{moment.headline}</h3>
        {moment.note ? <p>{moment.note}</p> : moment.team_id ? <p>{teamNames.get(moment.team_id) || moment.team_id}</p> : null}
      </div>
      <div className='row'>
        <button type='button' onClick={() => onPlayAt(moment.time_sec)}>Odtwórz</button>
        <button type='button' className='secondary' onClick={() => onEdit(moment)}>Edytuj</button>
        <button type='button' className='secondary' onClick={() => onDelete(moment)}>Usuń</button>
      </div>
    </article>)}
    {!moments.length ? <p className='redesign-empty-state'>Brak opublikowanych momentów dla tego raportu.</p> : null}
  </section>;
}
