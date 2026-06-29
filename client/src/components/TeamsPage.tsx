import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { listTeams } from '../api';
import type { Team } from '../types';
import { errorMessage } from '../lib/helpers';

export function TeamsPage() {
  const [teams, setTeams] = useState<Team[]>([]);
  const [status, setStatus] = useState('');

  useEffect(() => {
    listTeams()
      .then(setTeams)
      .catch((error) => setStatus(errorMessage(error)));
  }, []);

  return (
    <main className='app'>
      <section className='hero compact-hero'>
        <p className='eyebrow'>Drużyny</p>
        <h1>Rejestr drużyn</h1>
        <p>Zarządzaj lokalnymi drużynami i rosterami przed dodaniem meczu.</p>
        <div className='row'>
          <Link to='/admin-panel'>Panel meczu</Link>
          <Link to='/teams/add'>Dodaj drużynę</Link>
        </div>
      </section>

      {status && <p className='status'>{status}</p>}

      <section className='card'>
        <div className='row between'>
          <h2>Drużyny</h2>
          <Link to='/teams/add'>Dodaj drużynę</Link>
        </div>
        {teams.length === 0 ? (
          <p className='muted'>
            Nie ma jeszcze drużyn. Dodaj dwie drużyny przed utworzeniem meczu.
          </p>
        ) : (
          <div className='team-registry-list'>
            {teams.map((team) => (
              <div
                className='team-registry-item'
                key={team.id || team.name}
              >
                <span
                  className='color-dot'
                  style={{ background: team.color || '#64748b' }}
                />
                <div>
                  <strong>{team.name}</strong>
                  <span>{team.players?.length || 0} zawodników</span>
                </div>
                <div className='team-registry-actions'>
                  {team.id && <Link to={`/teams/${encodeURIComponent(team.id)}/stats`}>Statystyki</Link>}
                  {team.id && <Link to={`/teams/${encodeURIComponent(team.id)}`}>Edytuj</Link>}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
