import type { Team } from '../types';
import { emptyTeam } from '../lib/helpers';
import { parseRoster, rosterToText } from '../lib/helpers';

interface TeamEditorProps {
  teams: Team[];
  onChange: (teams: Team[]) => void;
}

export function TeamEditor({ teams, onChange }: TeamEditorProps) {
  function updateTeam(index: number, patch: Partial<Team>) {
    onChange(
      teams.map((team, teamIndex) =>
        teamIndex === index ? { ...team, ...patch } : team,
      ),
    );
  }

  return (
    <div className='stack'>
      <div className='row between'>
        <strong>Drużyny i roster</strong>
        <button
          type='button'
          onClick={() =>
            onChange([
              ...teams,
              emptyTeam(`Team ${teams.length + 1}`, '#64748b'),
            ])
          }
        >
          Dodaj drużynę
        </button>
      </div>
      {teams.map((team, index) => (
        <div className='team-card' key={team.id || index}>
          <div className='grid three compact'>
            <label>
              Nazwa
              <input
                value={team.name}
                onChange={(event) =>
                  updateTeam(index, { name: event.target.value })
                }
              />
            </label>
            <label>
              Kolor
              <input
                type='color'
                value={team.color || '#64748b'}
                onChange={(event) =>
                  updateTeam(index, { color: event.target.value })
                }
              />
            </label>
            <button
              type='button'
              onClick={() =>
                onChange(teams.filter((_, teamIndex) => teamIndex !== index))
              }
            >
              Usuń
            </button>
          </div>
          <label>
            Zawodnicy: imię, numer, rola — jeden na linię
            <textarea
              value={rosterToText(team.players || [])}
              onChange={(event) =>
                updateTeam(index, { players: parseRoster(event.target.value) })
              }
              rows={5}
            />
          </label>
        </div>
      ))}
    </div>
  );
}
