import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import type { IdentityReviewScopeChoice, Match, Team } from '../types';
import { createMatch, listTeams } from '../api';
import { errorMessage } from '../lib/helpers';
import { selectedMatchRosterReadiness } from '../utils/matchRoster';
import { scopeForPlayerStatsChoice } from '../utils/identityReviewScope';
import { IdentityReviewScopeSelector } from './IdentityReviewScopeSelector';

interface NewMatchFormProps {
  onCreated: (match: Match) => Promise<void> | void;
  onError: (message: string) => void;
}

function localDateInputValue(date = new Date()): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function defaultMatchTitle(dateValue: string): string {
  return `Mecz ${dateValue}`;
}

function teamKey(team: Team): string {
  return team.id || team.name;
}

export function NewMatchForm({ onCreated, onError }: NewMatchFormProps) {
  const defaultDate = useMemo(() => localDateInputValue(), []);
  const [title, setTitle] = useState(defaultMatchTitle(defaultDate));
  const [matchDate, setMatchDate] = useState(defaultDate);
  const [season, setSeason] = useState(defaultDate.slice(0, 4));
  const [venue, setVenue] = useState('');
  const [format, setFormat] = useState('7v7');
  const [teamRegistry, setTeamRegistry] = useState<Team[]>([]);
  const [teamAId, setTeamAId] = useState('');
  const [teamBId, setTeamBId] = useState('');
  const [playerStatsChoice, setPlayerStatsChoice] = useState<IdentityReviewScopeChoice>('A');
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
    const teamA = teamRegistry.find((team) => teamKey(team) === teamAId);
    const teamB = teamRegistry.find((team) => teamKey(team) === teamBId);
    const reviewScope = scopeForPlayerStatsChoice(playerStatsChoice);
    const rosterStatus = selectedMatchRosterReadiness(teamA, teamB, reviewScope);
    if (!rosterStatus.ready) {
      onError(rosterStatus.message || 'Uzupełnij roster meczu.');
      return;
    }
    const selectedTeams = [teamA, teamB].filter((team): team is Team => Boolean(team));

    setIsSubmitting(true);
    try {
      const match = await createMatch({
        title: title.trim() || defaultMatchTitle(matchDate || defaultDate),
        video,
        match_date: matchDate,
        season,
        venue,
        format,
        teams: selectedTeams,
        identity_review_scope: reviewScope,
      });
      await onCreated(match);
    } catch (error) {
      onError(errorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  const teamA = teamRegistry.find((team) => teamKey(team) === teamAId);
  const teamB = teamRegistry.find((team) => teamKey(team) === teamBId);
  const reviewScope = scopeForPlayerStatsChoice(playerStatsChoice);
  const rosterStatus = selectedMatchRosterReadiness(teamA, teamB, reviewScope);
  const canSubmit = !isSubmitting && Boolean(video) && rosterStatus.ready;

  return (
    <form onSubmit={submit} className='stack'>
      <label>
        Video
        <input
          type='file'
          accept='video/*'
          disabled={isSubmitting}
          onChange={(event) => setVideo(event.target.files?.[0] || null)}
        />
      </label>

      <section className='team-picker'>
          <div className='row between'>
            <div>
              <strong>Drużyny i rostery</strong>
              <p className='muted'>Wybierz obie drużyny biorące udział w meczu. Roster jest używany później podczas Review zawodników.</p>
            </div>
            <Link to='/teams/add'>Dodaj druzyne</Link>
          </div>
          {isLoadingTeams && (
            <p className='loading-line'>
              <span className='spinner' />
              Laduje rejestr druzyn...
            </p>
          )}
          {teamRegistry.length === 0 && !isLoadingTeams && (
            <p className='muted'>
              Brak drużyn w rejestrze. Dodaj obie drużyny wraz z zawodnikami przed utworzeniem meczu.
            </p>
          )}
          <div className='grid two compact'>
            <label>
              Twoja druzyna / Team A
              <select
                value={teamAId}
                disabled={isSubmitting || isLoadingTeams}
                onChange={(event) => setTeamAId(event.target.value)}
              >
                <option value=''>Wybierz Team A</option>
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
                value={teamBId}
                disabled={isSubmitting || isLoadingTeams}
                onChange={(event) => setTeamBId(event.target.value)}
              >
                <option value=''>Wybierz Team B</option>
                {teamRegistry.map((team) => (
                  <option value={teamKey(team)} key={teamKey(team)}>
                    {team.name} ({team.players?.length || 0} zawodnikow)
                  </option>
                ))}
              </select>
            </label>
          </div>
          <IdentityReviewScopeSelector
            teams={[teamA, teamB]}
            value={playerStatsChoice}
            onChange={setPlayerStatsChoice}
            disabled={isSubmitting || isLoadingTeams}
          />
          {!isLoadingTeams && !rosterStatus.ready && <p className='error'>{rosterStatus.message}</p>}
      </section>

      <details className='debug-details'>
        <summary>Opcjonalnie: metadane meczu</summary>
        <div className='stack'>
          <label>
            Tytul meczu
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
        </div>
      </details>

      {isSubmitting && (
        <p className='loading-line'>
          <span className='spinner' />
          Wysylam video i tworze mecz. Przy wiekszym pliku to moze chwile
          potrwac.
        </p>
      )}
      <button type='submit' disabled={!canSubmit}>
        {isSubmitting ? 'Dodaje mecz...' : 'Dodaj video i utworz mecz'}
      </button>
    </form>
  );
}
