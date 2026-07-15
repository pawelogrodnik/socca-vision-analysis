import type { Match, PublishedMatch } from '../types';

interface MatchListProps {
  matches: Match[];
  selectedId: string;
  onSelect: (id: string) => void;
}

export function MatchList({ matches, selectedId, onSelect }: MatchListProps) {
  if (!matches.length) return <p className='muted'>Brak meczów.</p>;
  return (
    <div className='match-list'>
      {matches.sort((a, b) => {
        if (!a.match_date) return 1;
        if (!b.match_date) return -1;

        return (
          new Date(b.match_date).getTime() -
          new Date(a.match_date).getTime()
        );
      }).map((match) => (
        <button
          type='button'
          className={
            match.id === selectedId ? 'match-item active' : 'match-item'
          }
          key={match.id}
          onClick={() => onSelect(match.id)}
        >
          <strong>{match.title}</strong>
          <span>
            {match.match_date || 'brak daty'} · {match.status || 'uploaded'}
          </span>
        </button>
      ))}
    </div>
  );
}

interface PublishedMatchListProps {
  matches: PublishedMatch[];
  selectedId: string;
  onSelect: (id: string) => void;
}

export function PublishedMatchList({
  matches,
  selectedId,
  onSelect,
}: PublishedMatchListProps) {
  if (!matches.length)
    return <p className='muted'>Brak opublikowanych meczów.</p>;
  return (
    <div className='match-list'>
      {matches.map((match) => (
        <button
          type='button'
          className={
            match.id === selectedId ? 'match-item active' : 'match-item'
          }
          key={match.id}
          onClick={() => onSelect(match.id)}
        >
          <strong>{match.title}</strong>
          <span>
            {match.match_date || 'brak daty'} · {match.player_count} zawodników
            · {match.tracks_count ?? 0} tracków
          </span>
        </button>
      ))}
    </div>
  );
}
