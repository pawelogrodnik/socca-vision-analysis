import { useState } from 'react';
import type { PublicCanonicalShot, PublicMatchReport, PublicReportTeam } from '../types';
import { formatReportClock } from '../lib/redesignedPublicReportPresentation';
import { hasPublicShotMapLocation, matchesPublicShotOutcomeFilter, publicShotOutcomeLabels, publicShotSummary, publicShotsForTeam, type PublicShotOutcomeFilter } from '../lib/publicShotPresentation';

const shotMapFilters: Array<{ value: PublicShotOutcomeFilter; label: string; icon: string }> = [
  { value: 'all', label: 'Wszystkie', icon: '◉' },
  { value: 'on_target', label: 'Celne', icon: '●' },
  { value: 'off_target', label: 'Niecelne', icon: '○' },
  { value: 'blocked', label: 'Zablokowane', icon: '×' },
  { value: 'goal', label: 'Gole', icon: '⚽' },
];

function teamName(team: PublicReportTeam): string {
  return team.team_name || team.team_label || team.team_id || 'Drużyna';
}

function markerIcon(outcome: PublicCanonicalShot['outcome']): string {
  if (outcome === 'blocked') return '×';
  if (outcome === 'off_target') return '○';
  return outcome === 'goal' ? '⚽' : '●';
}

function Marker({ shot, teamName, playerName, onPlayAt, onSelect }: { shot: PublicCanonicalShot; teamName: string; playerName?: string; onPlayAt: (time: number) => void; onSelect: (shot: PublicCanonicalShot) => void }) {
  if (!hasPublicShotMapLocation(shot)) return null;
  const detail = [formatReportClock(shot.time_sec), publicShotOutcomeLabels[shot.outcome], teamName, playerName].filter(Boolean).join(' · ');
  return <button
    type='button'
    className={`public-shot-map-marker outcome-${shot.outcome}`}
    style={{ left: `${shot.map_location.x * 100}%`, top: `${shot.map_location.y * 100}%` }}
    aria-label={detail}
    title={detail}
    onClick={() => { onSelect(shot); onPlayAt(shot.time_sec); }}
  ><span aria-hidden='true'>{markerIcon(shot.outcome)}</span></button>;
}

export function PublicShotMaps({ report, shots, onPlayAt }: { report: PublicMatchReport; shots: PublicCanonicalShot[]; onPlayAt: (time: number) => void }) {
  const [selected, setSelected] = useState<PublicCanonicalShot | null>(null);
  const [filter, setFilter] = useState<PublicShotOutcomeFilter>('all');
  const teams = report.teams.slice(0, 2);
  const playerNames = new Map(report.players.map((player) => [player.player_id, player.player_name || player.player_id]));
  function selectFilter(next: PublicShotOutcomeFilter) {
    setFilter(next);
    if (selected && !matchesPublicShotOutcomeFilter(selected, next)) setSelected(null);
  }
  return <section className='redesign-section public-shot-maps' aria-labelledby='public-shot-map-title'>
    <div className='redesign-section-heading'><div><p className='redesign-kicker'>Strzały</p><h2 id='public-shot-map-title'>Mapa strzałów</h2><p className='redesign-note'>Każda mapa pokazuje wyłącznie strzały jednej drużyny. Kierunek boiska wynika z kanonicznej kalibracji raportu.</p></div></div>
    <div className='key-moment-operator-tabs public-shot-map-filters' role='group' aria-label='Filtr wyniku strzału'>
      {shotMapFilters.map((option) => <button key={option.value} type='button' aria-pressed={filter === option.value} onClick={() => selectFilter(option.value)}><span aria-hidden='true'>{option.icon}</span> {option.label}</button>)}
    </div>
    <div className='public-shot-map-grid'>
      {teams.map((team) => {
        const teamId = team.team_id || team.team_label || '';
        const teamShots = publicShotsForTeam(shots, teamId);
        const summary = publicShotSummary(teamShots);
        const mapped = teamShots.filter(hasPublicShotMapLocation);
        const visibleMapped = mapped.filter((shot) => matchesPublicShotOutcomeFilter(shot, filter));
        return <article className='public-shot-map-card' key={teamId}>
          <h3>{teamName(team)}</h3>
          <dl className='public-shot-summary'><div><dt>Strzały</dt><dd>{summary.total}</dd></div><div><dt>Celne</dt><dd>{summary.onTarget}</dd></div><div><dt>Niecelne</dt><dd>{summary.offTarget}</dd></div><div><dt>% celnych</dt><dd>{summary.accuracyPercent == null ? '—' : `${summary.accuracyPercent}%`}</dd></div></dl>
          <div className='public-shot-pitch' aria-label={`Mapa strzałów: ${teamName(team)}`}>
            <svg viewBox='0 0 100 160' aria-hidden='true'><rect x='1' y='1' width='98' height='158' rx='3' /><line x1='1' x2='99' y1='80' y2='80' /><circle cx='50' cy='80' r='13' /><rect x='28' y='1' width='44' height='22' /><rect x='28' y='137' width='44' height='22' /></svg>
            {visibleMapped.map((shot) => <Marker key={shot.shot_id} shot={shot} teamName={teamName(team)} playerName={shot.player_id ? playerNames.get(shot.player_id) : undefined} onPlayAt={onPlayAt} onSelect={setSelected} />)}
          </div>
          <p className='redesign-note'>{mapped.length} z {summary.total} strzałów na mapie</p>
        </article>;
      })}
    </div>
    {selected ? <div className='public-shot-detail' role='status'><strong>{formatReportClock(selected.time_sec)}</strong><span>{publicShotOutcomeLabels[selected.outcome]}</span><span>{report.teams.find((team) => team.team_id === selected.team_id) ? teamName(report.teams.find((team) => team.team_id === selected.team_id)!) : selected.team_id}</span>{selected.player_id && playerNames.get(selected.player_id) ? <span>{playerNames.get(selected.player_id)}</span> : null}</div> : null}
  </section>;
}
