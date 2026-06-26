import { useState } from 'react';
import type { Match, Team } from '../types';
import { createMatch } from '../api';
import { defaultTeams, errorMessage } from '../lib/helpers';
import { TeamEditor } from './TeamEditor';

interface NewMatchFormProps {
  onCreated: (match: Match) => void;
  onError: (message: string) => void;
}

export function NewMatchForm({ onCreated, onError }: NewMatchFormProps) {
  const [title, setTitle] = useState('Nowy mecz');
  const [matchDate, setMatchDate] = useState('');
  const [season, setSeason] = useState('2026');
  const [venue, setVenue] = useState('');
  const [format, setFormat] = useState('7v7');
  const [teams, setTeams] = useState<Team[]>(defaultTeams());
  const [video, setVideo] = useState<File | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!video) {
      onError('Wybierz plik video.');
      return;
    }

    try {
      const match = await createMatch({
        title,
        video,
        match_date: matchDate,
        season,
        venue,
        format,
        teams,
      });
      onCreated(match);
    } catch (error) {
      onError(errorMessage(error));
    }
  }

  return (
    <form onSubmit={submit} className='stack'>
      <label>
        Tytuł meczu
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>
      <div className='grid three compact'>
        <label>
          Data
          <input
            type='date'
            value={matchDate}
            onChange={(event) => setMatchDate(event.target.value)}
          />
        </label>
        <label>
          Sezon
          <input
            value={season}
            onChange={(event) => setSeason(event.target.value)}
          />
        </label>
        <label>
          Format
          <input
            value={format}
            onChange={(event) => setFormat(event.target.value)}
          />
        </label>
      </div>
      <label>
        Miejsce
        <input
          value={venue}
          onChange={(event) => setVenue(event.target.value)}
        />
      </label>
      <TeamEditor teams={teams} onChange={setTeams} />
      <label>
        Video
        <input
          type='file'
          accept='video/*'
          onChange={(event) => setVideo(event.target.files?.[0] || null)}
        />
      </label>
      <button type='submit'>Dodaj mecz</button>
    </form>
  );
}
