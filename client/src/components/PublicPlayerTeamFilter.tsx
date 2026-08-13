import type { PublicReportPlayer, PublicReportTeam } from '../types';
import {
  publicReportPlayersForTeam,
  publicReportTeamKey,
} from '../lib/publicReportPresentation';

type PublicPlayerTeamFilterProps = {
  activeTeamKey: string | undefined;
  onSelect: (teamKey: string) => void;
  players: PublicReportPlayer[];
  teams: PublicReportTeam[];
};

const FALLBACK_TEAM_COLORS = ['#f97316', '#38bdf8'];

export function PublicPlayerTeamFilter({
  activeTeamKey,
  onSelect,
  players,
  teams,
}: PublicPlayerTeamFilterProps) {
  return (
    <section className='card player-team-filter-card'>
      <div>
        <h2>Przeglądana drużyna</h2>
        <p className='muted'>
          Wybór dotyczy porównania zawodników, ich statystyk oraz heatmap.
        </p>
      </div>
      <div className='player-team-filter' role='group' aria-label='Przeglądana drużyna'>
        {teams.map((team, index) => {
          const key = publicReportTeamKey(team, index);
          const isActive = key === activeTeamKey;
          const playerCount = publicReportPlayersForTeam(players, team).length;
          return (
            <button
              aria-pressed={isActive}
              className={`player-team-filter-button${isActive ? ' active' : ''}`}
              key={key}
              type='button'
              onClick={() => onSelect(key)}
            >
              <span
                className='player-team-filter-swatch'
                style={{
                  background:
                    team.display_color ||
                    FALLBACK_TEAM_COLORS[index % FALLBACK_TEAM_COLORS.length],
                }}
              />
              <span>{team.team_name || team.team_label || `Drużyna ${index + 1}`}</span>
              <small>{playerCount}</small>
            </button>
          );
        })}
      </div>
    </section>
  );
}
