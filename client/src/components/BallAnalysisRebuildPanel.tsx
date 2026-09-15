import { useEffect, useState } from 'react';
import { getBallAnalysisStatus, rebuildBallAnalysis } from '../api';
import { errorMessage } from '../lib/helpers';
import type { BallAnalysisStatus } from '../types';

type Props = {
  publishedMatchId: string;
  devAllowed: boolean;
  refreshKey?: number;
  onRebuilt?: () => void;
  fetchStatus?: (publishedMatchId: string) => Promise<BallAnalysisStatus>;
  requestRebuild?: (publishedMatchId: string) => Promise<BallAnalysisStatus>;
};

export function BallAnalysisRebuildPanel({
  publishedMatchId,
  devAllowed,
  refreshKey = 0,
  onRebuilt,
  fetchStatus = getBallAnalysisStatus,
  requestRebuild = rebuildBallAnalysis,
}: Props) {
  const [state, setState] = useState<BallAnalysisStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!devAllowed) return;
    let cancelled = false;
    setState(null);
    setError('');
    void fetchStatus(publishedMatchId)
      .then((next) => { if (!cancelled) setState(next); })
      .catch((reason: unknown) => { if (!cancelled) setError(errorMessage(reason)); });
    return () => { cancelled = true; };
  }, [devAllowed, fetchStatus, publishedMatchId, refreshKey]);

  if (!devAllowed) return null;
  const current = state?.status === 'current';

  async function rebuild() {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const next = await requestRebuild(publishedMatchId);
      setState(next);
      onRebuilt?.();
    } catch (reason) {
      setError(errorMessage(reason));
      // Re-read after a failed request: stale must remain visible instead of
      // leaving a misleading optimistic current state.
      void fetchStatus(publishedMatchId).then(setState).catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }

  return <section className='panel report-actions ball-analysis-rebuild-panel' aria-live='polite'>
    <h2>Analiza piłki</h2>
    {current ? <p className='status success'>Analiza piłki aktualna.</p> : <p className='status'>Analiza piłki wymaga przeliczenia.</p>}
    {!current && <button type='button' onClick={() => void rebuild()} disabled={busy || !state}>
      {busy ? <><span className='spinner' aria-hidden='true' /> Przeliczanie…</> : 'Przelicz analizę piłki'}
    </button>}
    {state?.sources?.length && <p className='muted'>
      {state.sources.length === 1 ? 'Przeliczy źródłowy mecz' : `Przeliczy ${state.sources.length} mecze źródłowe`} bez YOLO i bez generowania wideo.
    </p>}
    {error ? <p className='status'>{error}</p> : null}
  </section>;
}
