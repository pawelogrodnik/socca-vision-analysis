import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { listTeams } from '../api';
import { errorMessage } from '../lib/helpers';
import type { Match, Team } from '../types';

interface MatchRosterPanelProps {
  match: Match;
  disabled?: boolean;
  surface?: 'card' | 'panel';
  onSave: (teams: Team[]) => Promise<void> | void;
  onStatus: (message: string) => void;
}

function teamKey(team: Team): string {
  return team.id || team.name;
}

function findRegistryKey(registry: Team[], team: Team | undefined): string {
  if (!team) return '';
  const match = registry.find(
    (item) => item.id === team.id || item.name === team.name,
  );
  return match ? teamKey(match) : '';
}

function teamByKey(registry: Team[], key: string): Team | undefined {
  return registry.find((team) => teamKey(team) === key);
}

export function MatchRosterPanel({
  match,
  disabled = false,
  surface = 'card',
  onSave,
  onStatus,
}: MatchRosterPanelProps) {
  const [teamRegistry, setTeamRegistry] = useState<Team[]>([]);
  const [teamAKey, setTeamAKey] = useState('');
  const [teamBKey, setTeamBKey] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  async function loadRegistry() {
    setIsLoading(true);
    try {
      const teams = await listTeams();
      setTeamRegistry(teams);
      setTeamAKey(findRegistryKey(teams, match.teams?.[0]));
      setTeamBKey(findRegistryKey(teams, match.teams?.[1]));
    } catch (error) {
      onStatus(errorMessage(error));
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void loadRegistry();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id]);

  useEffect(() => {
    if (teamRegistry.length === 0) return;
    setTeamAKey(findRegistryKey(teamRegistry, match.teams?.[0]));
    setTeamBKey(findRegistryKey(teamRegistry, match.teams?.[1]));
  }, [match.teams, teamRegistry]);

  async function saveRoster() {
    if (isSaving || disabled) return;
    if (!teamAKey && teamBKey) {
      onStatus('Najpierw wybierz Team A. Team B bez Team A zmienilby label w analizie.');
      return;
    }
    if (teamAKey && teamBKey && teamAKey === teamBKey) {
      onStatus('Team A i Team B musza byc roznymi druzynami.');
      return;
    }

    const teamA = teamByKey(teamRegistry, teamAKey);
    const teamB = teamByKey(teamRegistry, teamBKey);
    const nextTeams = [teamA, teamB].filter((team): team is Team => Boolean(team));

    setIsSaving(true);
    try {
      await onSave(nextTeams);
      onStatus(
        nextTeams.length > 0
          ? `Zapisano roster meczu: ${nextTeams.map((team) => team.name).join(' / ')}.`
          : 'Wyczyszczono roster meczu.',
      );
    } catch (error) {
      onStatus(errorMessage(error));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className={surface === 'card' ? 'card workflow-card' : 'team-picker'}>
      <div className='row between'>
        <div>
          <h2>Roster tego meczu</h2>
          <p className='muted'>
            Wybierz istniejaca druzyne z rejestru i zapisz ja do snapshotu tego
            meczu. Po zapisie pojawi sie w Team/Zawodnik z rosteru.
          </p>
        </div>
        <div className='row'>
          <Link to='/teams'>Rejestr druzyn</Link>
          <button type='button' className='secondary' onClick={loadRegistry} disabled={disabled || isLoading}>
            {isLoading ? 'Laduje...' : 'Odswiez roster'}
          </button>
        </div>
      </div>

      {(match.teams || []).length > 0 && (
        <div className='team-snapshot'>
          <strong>Aktualny snapshot meczu</strong>
          {(match.teams || []).map((team, index) => (
            <div className='team-row' key={team.id || team.name}>
              <span
                className='color-dot'
                style={{ background: team.color || '#64748b' }}
              />
              <strong>Team {index === 0 ? 'A' : index === 1 ? 'B' : 'U'}</strong>
              <span>{team.name}</span>
              <span className='muted'>{team.players?.length || 0} zawodnikow</span>
            </div>
          ))}
        </div>
      )}

      <div className='grid two compact'>
        <label>
          Twoja druzyna / Team A
          <select
            value={teamAKey}
            disabled={disabled || isLoading || isSaving}
            onChange={(event) => setTeamAKey(event.target.value)}
          >
            <option value=''>-- bez rosteru --</option>
            {teamRegistry.map((team) => (
              <option value={teamKey(team)} key={teamKey(team)}>
                {team.name} ({team.players?.length || 0} zawodnikow)
              </option>
            ))}
          </select>
        </label>
        <label>
          Przeciwnik / Team B
          <select
            value={teamBKey}
            disabled={disabled || isLoading || isSaving}
            onChange={(event) => setTeamBKey(event.target.value)}
          >
            <option value=''>-- anonimowy przeciwnik --</option>
            {teamRegistry.map((team) => (
              <option value={teamKey(team)} key={teamKey(team)}>
                {team.name} ({team.players?.length || 0} zawodnikow)
              </option>
            ))}
          </select>
        </label>
      </div>

      {teamRegistry.length === 0 && !isLoading && (
        <p className='muted'>
          Brak druzyn w rejestrze. Dodaj druzyne w `/teams`, potem wroc tutaj i
          kliknij Odswiez roster.
        </p>
      )}

      <div className='row'>
        <button type='button' onClick={saveRoster} disabled={disabled || isSaving || isLoading}>
          {isSaving ? 'Zapisuje roster...' : 'Zapisz roster w tym meczu'}
        </button>
        <button
          type='button'
          className='secondary'
          onClick={() => {
            setTeamAKey('');
            setTeamBKey('');
          }}
          disabled={disabled || isSaving}
        >
          Wyczysc wybor
        </button>
      </div>
    </div>
  );
}
