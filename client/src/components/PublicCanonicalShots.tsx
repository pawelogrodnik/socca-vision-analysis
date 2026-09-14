import type { PublicCanonicalShot, PublicMatchReport } from '../types';
import { formatKeyMomentTime } from '../lib/keyMomentTime';
import { publicShotOutcomeLabels } from '../lib/publicShotPresentation';

export function PublicCanonicalShots({ shots, report, onPlayAt, videoAvailable }: { shots: PublicCanonicalShot[]; report: PublicMatchReport; onPlayAt: (time: number) => void; videoAvailable: boolean }) {
  const teamNames = new Map(report.teams.map((team) => [team.team_id, team.team_name || team.team_label || team.team_id]));
  const playerNames = new Map(report.players.map((player) => [player.player_id, player.player_name || player.player_id]));
  return <section className='operator-key-moments-list' aria-label='Opublikowane strzały'>
    <div className='row between'><p className='muted'>Opublikowane strzały ({shots.length})</p></div>
    {shots.map((shot) => <article className='redesign-moment-row' key={shot.shot_id}>
      <time>{formatKeyMomentTime(shot.time_sec)}</time>
      <div><h3>{publicShotOutcomeLabels[shot.outcome]}</h3><p>{teamNames.get(shot.team_id) || shot.team_id}{shot.player_id && playerNames.get(shot.player_id) ? ` · ${playerNames.get(shot.player_id)}` : ''}</p></div>
      {videoAvailable ? <button type='button' className='key-moment-action-button' aria-label='Odtwórz strzał' title='Odtwórz strzał' onClick={() => onPlayAt(shot.time_sec)}><span aria-hidden='true'>▶</span></button> : null}
    </article>)}
    {!shots.length ? <p className='redesign-empty-state'>Brak opublikowanych strzałów.</p> : null}
  </section>;
}
