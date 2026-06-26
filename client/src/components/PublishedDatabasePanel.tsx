import { useEffect, useState } from 'react';
import type { PublishedMatch, PublishedMatchDetail } from '../types';
import {
  deletePublishedMatch,
  getPublishedMatch,
  listPublishedMatches,
} from '../api';
import { errorMessage } from '../lib/helpers';
import { PublishedMatchList } from './MatchList';
import { PublishedMatchSummary } from './MatchSummary';

interface PublishedDatabasePanelProps {
  onStatus: (message: string) => void;
}

export function PublishedDatabasePanel({
  onStatus,
}: PublishedDatabasePanelProps) {
  const [matches, setMatches] = useState<PublishedMatch[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [selected, setSelected] = useState<PublishedMatchDetail | null>(null);

  async function refresh(nextSelectedId = selectedId) {
    const items = await listPublishedMatches();
    setMatches(items);
    const id = nextSelectedId || items[0]?.id || '';
    setSelectedId(id);
    setSelected(id ? await getPublishedMatch(id) : null);
  }

  useEffect(() => {
    refresh().catch((error) => onStatus(errorMessage(error)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function select(id: string) {
    setSelectedId(id);
    setSelected(await getPublishedMatch(id));
  }

  async function remove(id: string) {
    const confirmed = window.confirm(
      'Usunąć opublikowany mecz z bazy? Tego używamy przy duplikatach albo błędnych statystykach.',
    );
    if (!confirmed) return;
    await deletePublishedMatch(id);
    onStatus(`Usunięto ${id} z bazy.`);
    await refresh('');
  }

  return (
    <section className='card'>
      <div className='row between'>
        <h2>7. Zarządzanie opublikowanymi statystykami</h2>
        <button type='button' onClick={() => refresh()}>
          Odśwież bazę
        </button>
      </div>
      <p className='muted'>
        To są mecze zaimportowane do SQLite. Tu można usunąć duplikaty albo
        błędne snapshoty statystyk.
      </p>
      <div className='grid two'>
        <PublishedMatchList
          matches={matches}
          selectedId={selectedId}
          onSelect={select}
        />
        <div>
          {selected ? (
            <>
              <PublishedMatchSummary match={selected} />
              <div className='row'>
                <button
                  type='button'
                  className='danger'
                  onClick={() => remove(selected.id)}
                >
                  Usuń z bazy
                </button>
              </div>
            </>
          ) : (
            <p className='muted'>Brak rekordów w bazie.</p>
          )}
        </div>
      </div>
    </section>
  );
}
