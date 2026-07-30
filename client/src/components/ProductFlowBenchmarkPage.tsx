import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import {
  finishProductFlowBenchmarkH1,
  finishProductFlowBenchmarkH2,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type { Match } from '../types';
import { InitialIdentityAuditPanel } from './InitialIdentityAuditPanel';
import { SecondHalfIdentityReanchorPanel } from './SecondHalfIdentityReanchorPanel';

type BenchmarkWorkspace = {
  match_id: string;
  frames: number;
  match: Match;
};

type Benchmark = {
  state: string;
  status: string;
  benchmark_id: string;
  workspaces: {
    h1: BenchmarkWorkspace | null;
    h2: BenchmarkWorkspace | null;
  };
  operator_budget: {
    h1_maximum_actions: number;
    h2_maximum_confirmations: number;
  };
  audit_log: Array<{
    from_state: string;
    to_state: string;
    action: string;
  }>;
};

const API_BASE = import.meta.env.DEV
  ? ''
  : (import.meta.env.VITE_API_BASE_URL || '');

async function getBenchmark(benchmarkId: string): Promise<Benchmark> {
  const response = await fetch(
    `${API_BASE}/api/product-flow-benchmarks/${encodeURIComponent(benchmarkId)}`,
  );
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<Benchmark>;
}

export function ProductFlowBenchmarkPage() {
  const { benchmarkId } = useParams();
  const [benchmark, setBenchmark] = useState<Benchmark | null>(null);
  const [status, setStatus] = useState('Ładowanie benchmarku…');
  const [transitioning, setTransitioning] = useState(false);

  const reload = useCallback(async () => {
    if (!benchmarkId) return;
    const nextBenchmark = await getBenchmark(benchmarkId);
    setBenchmark(nextBenchmark);
    setStatus('');
  }, [benchmarkId]);

  useEffect(() => {
    void reload().catch((error: unknown) => setStatus(errorMessage(error)));
  }, [reload]);

  async function finishH1() {
    if (!benchmarkId) return;
    setTransitioning(true);
    try {
      await finishProductFlowBenchmarkH1(benchmarkId);
      await reload();
      setStatus('H1 przebudowany. Re-anchor H2 jest teraz gotowy.');
    } catch (error) {
      setStatus(errorMessage(error));
      throw error;
    } finally {
      setTransitioning(false);
    }
  }

  async function finishH2() {
    if (!benchmarkId) return;
    setTransitioning(true);
    try {
      await finishProductFlowBenchmarkH2(benchmarkId);
      await reload();
      setStatus('Benchmark zakonczony. Finalny raport jest gotowy.');
    } catch (error) {
      setStatus(errorMessage(error));
      throw error;
    } finally {
      setTransitioning(false);
    }
  }

  const state = benchmark?.state;
  const h1 = benchmark?.workspaces.h1?.match;
  const h2 = benchmark?.workspaces.h2?.match;

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
        <section>
          <p className='muted'>
            Stan: <strong>{benchmark.state}</strong>. Budżet: do{' '}
            {benchmark.operator_budget.h1_maximum_actions} aktywnych decyzji H1
            oraz do {benchmark.operator_budget.h2_maximum_confirmations}{' '}
            potwierdzeń H2.
          </p>
        </section>
      )}

      {state === 'H1_READY' && h1 && (
        <InitialIdentityAuditPanel
          match={h1}
          onStatus={setStatus}
          maximumActions={benchmark.operator_budget.h1_maximum_actions}
          benchmarkState={state}
          onFinished={finishH1}
        />
      )}

      {(state === 'H1_FINISHED' || state === 'H1_REBUILT' || transitioning) && (
        <p className='status'>
          Trwa bezpieczny rebuild po H1. H2 pojawi się dopiero po jego zakończeniu.
        </p>
      )}

      {state === 'H2_READY' && h2 && (
        <SecondHalfIdentityReanchorPanel
          match={h2}
          onStatus={setStatus}
          maximumConfirmations={benchmark.operator_budget.h2_maximum_confirmations}
          benchmarkState={state}
          onFinished={finishH2}
        />
      )}

      {state === 'REPORT_READY' && (
        <p className='status'>
          Raport benchmarku jest gotowy. Nie oznacza to jeszcze IA7A_READY —
          najpierw trzeba ocenić wynik operatora.
        </p>
      )}

      {state === 'FAILED' && (
        <p className='error'>
          Benchmark zatrzymał się bezpiecznie w stanie FAILED. Szczegóły są w
          ostatnim wpisie audit logu.
        </p>
      )}
    </main>
  );
}
