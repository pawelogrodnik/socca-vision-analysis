import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { getPublishedMatch, listPublishedMatches } from '../api';
import type { PublishedMatch, PublishedMatchDetail } from '../types';
import { errorMessage } from '../lib/helpers';
import { PublishedMatchList } from './MatchList';
import { PublishedMatchSummary } from './MatchSummary';

export function Viewer() {
  const [matches, setMatches] = useState<PublishedMatch[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [selected, setSelected] = useState<PublishedMatchDetail | null>(null);
  const [status, setStatus] = useState('');

  useEffect(() => {
    listPublishedMatches()
      .then((items) => {
        setMatches(items);
        if (items[0]) setSelectedId(items[0].id);
      })
      .catch((error) => setStatus(errorMessage(error)));
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    getPublishedMatch(selectedId)
      .then(setSelected)
      .catch((error) => setStatus(errorMessage(error)));
  }, [selectedId]);

  return (
    <main className='app'>
      <section className='hero'>
        <p className='eyebrow'>Public viewer</p>
        <h1>Socca Vision Analysis</h1>
        <p>Read-only widok opublikowanych meczów z lekkiej bazy SQLite.</p>
        <Link to='/admin-panel'>Przejdź do lokalnego admin panelu</Link>
      </section>
      {status && <p className='status'>{status}</p>}
      <div className='grid two'>
        <section className='card'>
          <h2>Opublikowane mecze</h2>
          <PublishedMatchList
            matches={matches}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        </section>
        <section className='card'>
          <h2>Publiczny raport</h2>
          {selected ? (
            <PublishedMatchSummary match={selected} />
          ) : (
            <p className='muted'>Brak opublikowanych meczów w bazie.</p>
          )}
        </section>
      </div>
    </main>
  );
}
