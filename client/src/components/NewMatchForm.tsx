import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { Match, Team } from '../types';
import { createMatch, listTeams } from '../api';
import { errorMessage } from '../lib/helpers';

interface NewMatchFormProps {
  onCreated: (match: Match) => Promise<void> | void;
  onError: (message: string) => void;
}

export function NewMatchForm({ onCreated, onError }: NewMatchFormProps) {
  const [title, setTitle] = useState('Nowy mecz');
  const [matchDate, setMatchDate] = useState('');
  const [season, setSeason] = useState('2026');
  const [venue, setVenue] = useState('');
  const [format, setFormat] = useState('7v7');
  const [teamRegistry, setTeamRegistry] = useState<Team[]>([]);
  const [teamAId, setTeamAId] = useState('');
  const [teamBId, setTeamBId] = useState('');
  const [video, setVideo] = useState<File | null>(null);
  const [isLoadingTeams, setIsLoadingTeams] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setIsLoadingTeams(true);
    listTeams()
      .then((items) => {
        if (cancelled) return;
        setTeamRegistry(items);
        setTeamAId((current) => current || items[0]?.id || '');
        setTeamBId((current) => current || items[1]?.id || '');
      })
      .catch((error) => {
        if (!cancelled) onError(errorMessage(error));
      })
      .finally(() => {
        if (!cancelled) setIsLoadingTeams(false);
      });
    return () => {
      cancelled = true;
    };
  }, [onError]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (isSubmitting) return;
    if (!video) {
      onError('Wybierz plik video.');
      return;
    }
    const teamA = teamRegistry.find((team) => team.id === teamAId);
    const teamB = teamRegistry.find((team) => team.id === teamBId);
    if (!teamA || !teamB || teamA.id === teamB.id) {
      onError('Wybierz dwie rozne druzyny z rejestru.');
      return;
    }

    setIsSubmitting(true);
    try {
      const match = await createMatch({
        title,
        video,
        match_date: matchDate,
        season,
        venue,
        format,
        teams: [teamA, teamB],
      });
      await onCreated(match);
    } catch (error) {
      onError(errorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  const canSubmit = !isSubmitting && !isLoadingTeams && teamRegistry.length >= 2;

  return (
    <form onSubmit={submit} className='stack'>
      <label>
        Tytuł meczu
        <input
          value={title}
          disabled={isSubmitting}
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>
      <div className='grid three compact'>
        <label>
          Data
          <input
            type='date'
            value={matchDate}
            disabled={isSubmitting}
            onChange={(event) => setMatchDate(event.target.value)}
          />
        </label>
        <label>
          Sezon
          <input
            value={season}
            disabled={isSubmitting}
            onChange={(event) => setSeason(event.target.value)}
          />
        </label>
        <label>
          Format
          <input
            value={format}
            disabled={isSubmitting}
            onChange={(event) => setFormat(event.target.value)}
          />
        </label>
      </div>
      <label>
        Miejsce
        <input
          value={venue}
          disabled={isSubmitting}
          onChange={(event) => setVenue(event.target.value)}
        />
      </label>
      <div className='team-picker'>
        <div className='row between'>
          <strong>Drużyny w meczu</strong>
          <Link to='/teams/add'>Dodaj drużynę</Link>
        </div>
        {isLoadingTeams && (
          <p className='loading-line'>
            <span className='spinner' />
            Ładuję rejestr drużyn...
          </p>
        )}
        {teamRegistry.length < 2 && (
          <p className='muted'>
            Dodaj co najmniej dwie drużyny w rejestrze przed utworzeniem
            meczu.
          </p>
        )}
        <div className='grid two compact'>
          <label>
            Drużyna A
            <select
              value={teamAId}
              disabled={isSubmitting || isLoadingTeams}
              onChange={(event) => setTeamAId(event.target.value)}
            >
              <option value=''>-- wybierz drużynę --</option>
              {teamRegistry.map((team) => (
                <option value={team.id || team.name} key={team.id || team.name}>
                  {team.name} ({team.players?.length || 0} zawodników)
                </option>
              ))}
            </select>
          </label>
          <label>
            Drużyna B
            <select
              value={teamBId}
              disabled={isSubmitting || isLoadingTeams}
              onChange={(event) => setTeamBId(event.target.value)}
            >
              <option value=''>-- wybierz drużynę --</option>
              {teamRegistry.map((team) => (
                <option value={team.id || team.name} key={team.id || team.name}>
                  {team.name} ({team.players?.length || 0} zawodników)
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      <label>
        Video
        <input
          type='file'
          accept='video/*'
          disabled={isSubmitting}
          onChange={(event) => setVideo(event.target.files?.[0] || null)}
        />
      </label>
      {isSubmitting && (
        <p className='loading-line'>
          <span className='spinner' />
          Wysyłam video i tworzę mecz. Przy większym pliku to może chwilę
          potrwać.
        </p>
      )}
      <button type='submit' disabled={!canSubmit}>
        {isSubmitting
          ? 'Dodaję mecz...'
          : isLoadingTeams
            ? 'Ładuję drużyny...'
            : teamRegistry.length < 2
              ? 'Dodaj najpierw dwie drużyny'
            : 'Dodaj mecz'}
      </button>
    </form>
  );
}
