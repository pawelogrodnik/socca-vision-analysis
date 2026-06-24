import { useEffect, useState } from 'react';
import { getMatch, listMatches } from './api';
import { IdentityCandidatePanel } from './IdentityCandidatePanel';
import type { Match } from './types';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function IdentityCandidateAdminSection() {
  const isAdmin = window.location.pathname.startsWith('/admin-panel');
  const [matches, setMatches] = useState<Match[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [selected, setSelected] = useState<Match | null>(null);
  const [status, setStatus] = useState('');

  async function refresh(nextId = selectedId) {
    const items = await listMatches();
    setMatches(items);
    const id = nextId || items[0]?.id || '';
    setSelectedId(id);
    setSelected(id ? await getMatch(id) : null);
  }

  useEffect(() => {
    if (!isAdmin) return;
    refresh().catch((error) => setStatus(errorMessage(error)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin]);

  useEffect(() => {
    if (!isAdmin || !selectedId) return;
    getMatch(selectedId)
      .then(setSelected)
      .catch((error) => setStatus(errorMessage(error)));
  }, [isAdmin, selectedId]);

  if (!isAdmin) return null;

  return (
    <section className="app">
      <section className="card">
        <div className="row between">
          <div>
            <h2>Identity candidate resolver</h2>
            <p className="muted">Nowy panel przypisuje grupy trackletów do zawodników. Używaj go zamiast ręcznego klikania pojedynczych raw tracker IDs.</p>
          </div>
          <button type="button" onClick={() => refresh()}>Odśwież mecze</button>
        </div>
        {status && <p className="status">{status}</p>}
        <label>
          Mecz do review
          <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
            <option value="">-- wybierz mecz --</option>
            {matches.map((match) => (
              <option key={match.id} value={match.id}>
                {match.title} · {match.match_date || 'brak daty'} · {match.status || 'uploaded'}
              </option>
            ))}
          </select>
        </label>
      </section>

      {selected ? (
        <IdentityCandidatePanel match={selected} onStatus={setStatus} onSaved={() => refresh(selected.id)} />
      ) : (
        <section className="card">
          <p className="muted">Wybierz mecz, żeby zobaczyć identity candidates.</p>
        </section>
      )}
    </section>
  );
}
