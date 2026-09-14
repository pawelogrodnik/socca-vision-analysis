import { useState } from 'react';
import type { PublicCanonicalShot, PublicMatchReport, PublicReportTeam } from '../types';
import { formatReportClock } from '../lib/redesignedPublicReportPresentation';
import { hasPublicShotLocation, publicShotOutcomeLabels, publicShotSummary, publicShotsForTeam } from '../lib/publicShotPresentation';

const PITCH_WIDTH_M = 30;
const PITCH_LENGTH_M = 47.4;

function teamName(team: PublicReportTeam): string {
  return team.team_name || team.team_label || team.team_id || 'Drużyna';
}

function Marker({ shot, onPlayAt, onSelect }: { shot: PublicCanonicalShot; onPlayAt: (time: number) => void; onSelect: (shot: PublicCanonicalShot) => void }) {
  if (!hasPublicShotLocation(shot)) return null;
  const mapLocation = shot.map_location;
  const x = Math.max(2, Math.min(98, mapLocation ? mapLocation.x * 100 : shot.location_m.x / PITCH_WIDTH_M * 100));
  const y = Math.max(2, Math.min(158, mapLocation ? mapLocation.y * 160 : shot.location_m.y / PITCH_LENGTH_M * 160));
  return <button
    type='button'
    className={`public-shot-map-marker outcome-${shot.outcome}`}
    style={{ left: `${x}%`, top: `${y / 1.6}%` }}
    aria-label={`${publicShotOutcomeLabels[shot.outcome]}, ${formatReportClock(shot.time_sec)}`}
    title={`${publicShotOutcomeLabels[shot.outcome]} · ${formatReportClock(shot.time_sec)}`}
    onClick={() => { onSelect(shot); onPlayAt(shot.time_sec); }}
  ><span aria-hidden='true'>{shot.outcome === 'blocked' ? '×' : shot.outcome === 'goal' ? '◎' : '●'}</span></button>;
}

export function PublicShotMaps({ report, shots, onPlayAt }: { report: PublicMatchReport; shots: PublicCanonicalShot[]; onPlayAt: (time: number) => void }) {
  const [selected, setSelected] = useState<PublicCanonicalShot | null>(null);
  const teams = report.teams.slice(0, 2);
  const playerNames = new Map(report.players.map((player) => [player.player_id, player.player_name || player.player_id]));
  return <section className='redesign-section public-shot-maps' aria-labelledby='public-shot-map-title'>
    <div className='redesign-section-heading'><div><p className='redesign-kicker'>Strzały</p><h2 id='public-shot-map-title'>Mapa strzałów</h2><p className='redesign-note'>Każda mapa pokazuje wyłącznie strzały jednej drużyny. Kierunek boiska wynika z kanonicznej kalibracji raportu.</p></div></div>
    <div className='public-shot-map-grid'>
      {teams.map((team) => {
        const teamId = team.team_id || team.team_label || '';
        const teamShots = publicShotsForTeam(shots, teamId);
        const summary = publicShotSummary(teamShots);
        const mapped = teamShots.filter(hasPublicShotLocation);
        return <article className='public-shot-map-card' key={teamId}>
          <h3>{teamName(team)}</h3>
          <dl className='public-shot-summary'><div><dt>Strzały</dt><dd>{summary.total}</dd></div><div><dt>Celne</dt><dd>{summary.onTarget}</dd></div><div><dt>Niecelne</dt><dd>{summary.offTarget}</dd></div><div><dt>% celnych</dt><dd>{summary.accuracyPercent == null ? '—' : `${summary.accuracyPercent}%`}</dd></div></dl>
          <div className='public-shot-pitch' aria-label={`Mapa strzałów: ${teamName(team)}`}>
            <svg viewBox='0 0 100 160' aria-hidden='true'><rect x='1' y='1' width='98' height='158' rx='3' /><line x1='1' x2='99' y1='80' y2='80' /><circle cx='50' cy='80' r='13' /><rect x='28' y='1' width='44' height='22' /><rect x='28' y='137' width='44' height='22' /></svg>
            {mapped.map((shot) => <Marker key={shot.shot_id} shot={shot} onPlayAt={onPlayAt} onSelect={setSelected} />)}
          </div>
          <p className='redesign-note'>{mapped.length} z {summary.total} strzałów na mapie</p>
        </article>;
      })}
    </div>
    {selected ? <div className='public-shot-detail' role='status'><strong>{formatReportClock(selected.time_sec)}</strong><span>{publicShotOutcomeLabels[selected.outcome]}</span><span>{report.teams.find((team) => team.team_id === selected.team_id) ? teamName(report.teams.find((team) => team.team_id === selected.team_id)!) : selected.team_id}</span>{selected.player_id && playerNames.get(selected.player_id) ? <span>{playerNames.get(selected.player_id)}</span> : null}</div> : null}
  </section>;
}
