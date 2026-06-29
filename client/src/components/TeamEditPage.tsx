import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { createTeam, deleteTeam, getTeam, updateTeam } from '../api';
import type { Team } from '../types';
import { emptyTeam, errorMessage } from '../lib/helpers';
import { TeamForm } from './TeamForm';

interface TeamEditPageProps {
  mode: 'create' | 'edit';
}

export function TeamEditPage({ mode }: TeamEditPageProps) {
  const { teamId } = useParams();
  const navigate = useNavigate();
  const [team, setTeam] = useState<Team | null>(
    mode === 'create' ? emptyTeam('Nowa drużyna', '#64748b') : null,
  );
  const [status, setStatus] = useState('');

  useEffect(() => {
    if (mode === 'create') return;
    if (!teamId) {
      setStatus('Brakuje ID drużyny.');
      return;
    }
    getTeam(teamId)
      .then(setTeam)
      .catch((error) => setStatus(errorMessage(error)));
  }, [mode, teamId]);

  async function save(nextTeam: Team) {
    try {
      const saved = mode === 'create' || !teamId
        ? await createTeam(nextTeam)
        : await updateTeam(teamId, nextTeam);
      setStatus(`Zapisano ${saved.name}.`);
      navigate('/teams');
    } catch (error) {
      setStatus(errorMessage(error));
    }
  }

  async function remove() {
    if (!teamId) return;
    try {
      await deleteTeam(teamId);
      navigate('/teams');
    } catch (error) {
      setStatus(errorMessage(error));
    }
  }

  return (
    <main className='app'>
      <section className='hero compact-hero'>
        <p className='eyebrow'>Drużyny</p>
        <h1>{mode === 'create' ? 'Dodaj drużynę' : 'Edytuj drużynę'}</h1>
        <p>Zarządzanie rosterem jest oddzielone od analizy meczu.</p>
        <div className='row'>
          <Link to='/teams'>Drużyny</Link>
          {teamId && <Link to={`/teams/${encodeURIComponent(teamId)}/stats`}>Statystyki</Link>}
          <Link to='/admin-panel'>Panel meczu</Link>
        </div>
      </section>

      {status && <p className='status'>{status}</p>}

      <section className='card'>
        {team ? (
          <TeamForm
            initialTeam={team}
            submitLabel={mode === 'create' ? 'Utwórz drużynę' : 'Zapisz drużynę'}
            onSubmit={save}
            onDelete={mode === 'edit' ? remove : undefined}
          />
        ) : (
          <p className='muted'>Ładowanie drużyny...</p>
        )}
      </section>
    </main>
  );
}
