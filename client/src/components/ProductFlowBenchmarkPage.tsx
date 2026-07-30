import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import type { Match } from '../types';
import { InitialIdentityAuditPanel } from './InitialIdentityAuditPanel';
import { SecondHalfIdentityReanchorPanel } from './SecondHalfIdentityReanchorPanel';

type Benchmark = {
  status: string;
  benchmark_id: string;
  workspaces: {
    h1: { match_id: string; frames: number; match: Match };
    h2: { match_id: string; frames: number; match: Match };
  };
  operator_budget: {
    h1_maximum_actions: number;
    h2_maximum_confirmations: number;
  };
};

export function ProductFlowBenchmarkPage() {
  const { benchmarkId } = useParams();
  const [benchmark, setBenchmark] = useState<Benchmark | null>(null);
  const [h1, setH1] = useState<Match | null>(null);
  const [h2, setH2] = useState<Match | null>(null);
  const [status, setStatus] = useState('Ładowanie benchmarku…');

  useEffect(() => {
    if (!benchmarkId) return;
    const apiPath = (path: string) => (
      import.meta.env.DEV
        ? path
        : `${import.meta.env.VITE_API_BASE_URL || ''}${path}`
    );
    const getJson = async <T,>(path: string): Promise<T> => {
      const response = await fetch(apiPath(path));
      if (!response.ok) throw new Error(await response.text());
      return response.json() as Promise<T>;
    };
    getJson<Benchmark>(`/api/product-flow-benchmarks/${encodeURIComponent(benchmarkId)}`)
      .then(async (nextBenchmark) => {
        setBenchmark(nextBenchmark);
        setH1(nextBenchmark.workspaces.h1.match);
        setH2(nextBenchmark.workspaces.h2.match);
        setStatus('');
      })
      .catch((error: unknown) => setStatus(error instanceof Error ? error.message : String(error)));
  }, [benchmarkId]);

  return (
    <main className='app'>
      <section className='hero compact-hero'>
        <p className='eyebrow'>Product-flow benchmark</p>
        <h1>Krótki audyt H1 → re-anchor H2</h1>
        <p>
          Wybierz tylko pewne przypadki. Nie musisz wypełniać całego audytu —
          „Pomiń / nie wiem” oraz wcześniejsze zakończenie są zawsze poprawne.
        </p>
        <div className='row'><Link to='/'>Wróć do aplikacji</Link></div>
      </section>
      {status && <p className='status'>{status}</p>}
      {benchmark && (
        <p className='muted'>
          Budżet: do {benchmark.operator_budget.h1_maximum_actions} pewnych decyzji H1
          oraz do {benchmark.operator_budget.h2_maximum_confirmations} potwierdzeń H2.
        </p>
      )}
      {h1 && <InitialIdentityAuditPanel match={h1} onStatus={setStatus} />}
      {h2 && <SecondHalfIdentityReanchorPanel match={h2} onStatus={setStatus} />}
    </main>
  );
}
