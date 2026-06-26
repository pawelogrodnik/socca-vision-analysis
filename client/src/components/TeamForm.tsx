import { useEffect, useState } from 'react';
import type { Team } from '../types';
import { emptyTeam, parseRoster, rosterToText } from '../lib/helpers';

interface TeamFormProps {
  initialTeam?: Team | null;
  submitLabel: string;
  onSubmit: (team: Team) => Promise<void> | void;
  onDelete?: () => Promise<void> | void;
}

export function TeamForm({ initialTeam, submitLabel, onSubmit, onDelete }: TeamFormProps) {
  const [team, setTeam] = useState<Team>(
    initialTeam || emptyTeam('Nowa drużyna', '#64748b'),
  );
  const [rosterText, setRosterText] = useState(rosterToText(initialTeam?.players || []));
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  useEffect(() => {
    const nextTeam = initialTeam || emptyTeam('Nowa drużyna', '#64748b');
    setTeam(nextTeam);
    setRosterText(rosterToText(nextTeam.players || []));
  }, [initialTeam]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (isSubmitting || isDeleting) return;
    setIsSubmitting(true);
    try {
      await onSubmit({
        ...team,
        players: parseRoster(rosterText),
      });
    } finally {
      setIsSubmitting(false);
    }
  }

  async function remove() {
    if (!onDelete || isSubmitting || isDeleting) return;
    setIsDeleting(true);
    try {
      await onDelete();
    } finally {
      setIsDeleting(false);
    }
  }

  return (
    <form className='stack' onSubmit={submit}>
      <div className='grid two compact'>
        <label>
          Nazwa drużyny
          <input
            value={team.name}
            disabled={isSubmitting || isDeleting}
            onChange={(event) => setTeam((current) => ({ ...current, name: event.target.value }))}
          />
        </label>
        <label>
          Kolor drużyny
          <input
            type='color'
            value={team.color || '#64748b'}
            disabled={isSubmitting || isDeleting}
            onChange={(event) => setTeam((current) => ({ ...current, color: event.target.value }))}
          />
        </label>
      </div>
      <label>
        Roster: imię i nazwisko, numer, rola - jeden zawodnik na linię
        <textarea
          rows={10}
          value={rosterText}
          disabled={isSubmitting || isDeleting}
          onChange={(event) => setRosterText(event.target.value)}
        />
      </label>
      {(isSubmitting || isDeleting) && (
        <p className='loading-line'>
          <span className='spinner' />
          {isDeleting ? 'Usuwam drużynę...' : 'Zapisuję drużynę...'}
        </p>
      )}
      <div className='row'>
        <button type='submit' disabled={isSubmitting || isDeleting}>
          {isSubmitting ? 'Zapisuję...' : submitLabel}
        </button>
        {onDelete && (
          <button
            type='button'
            className='danger'
            onClick={remove}
            disabled={isSubmitting || isDeleting}
          >
            {isDeleting ? 'Usuwam...' : 'Usuń drużynę'}
          </button>
        )}
      </div>
    </form>
  );
}
