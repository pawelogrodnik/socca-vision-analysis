import { useEffect, useState } from 'react';
import type { Match, MatchMetadataPayload, Team } from '../types';
import { defaultTeams } from '../lib/helpers';
import { TeamEditor } from './TeamEditor';

interface MetadataEditorProps {
  match: Match;
  onSave: (payload: MatchMetadataPayload) => void;
}

export function MetadataEditor({ match, onSave }: MetadataEditorProps) {
  const [title, setTitle] = useState(match.title);
  const [matchDate, setMatchDate] = useState(match.match_date || '');
  const [season, setSeason] = useState(match.season || '');
  const [venue, setVenue] = useState(match.venue || '');
  const [format, setFormat] = useState(match.format || '7v7');
  const [status, setStatus] = useState(match.status || 'uploaded');
  const [teams, setTeams] = useState<Team[]>(match.teams || defaultTeams());

  useEffect(() => {
    setTitle(match.title);
    setMatchDate(match.match_date || '');
    setSeason(match.season || '');
    setVenue(match.venue || '');
    setFormat(match.format || '7v7');
    setStatus(match.status || 'uploaded');
    setTeams(match.teams || defaultTeams());
  }, [match]);

  return (
    <div className='stack'>
      <label>
        Tytuł
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
          Status
          <input
            value={status}
            onChange={(event) => setStatus(event.target.value)}
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
      <label>
        Format
        <input
          value={format}
          onChange={(event) => setFormat(event.target.value)}
        />
      </label>
      <TeamEditor teams={teams} onChange={setTeams} />
      <button
        type='button'
        onClick={() =>
          onSave({
            title,
            match_date: matchDate || null,
            season: season || null,
            venue: venue || null,
            format,
            status,
            teams,
          })
        }
      >
        Zapisz metadane
      </button>
    </div>
  );
}
