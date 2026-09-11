import { useEffect, useState } from 'react';
import {
  getMergedSourceDataRebuildStatus,
  getPublishedMatch,
  previewMergedSourceDataRebuild,
  rebuildMergedSourceData,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type { PublishedMatchDetail, SourceDataRebuildJob, SourceDataRebuildPreflight, SourceDataRebuildSource } from '../types';

type Props = {
  mergedId: string;
  devAllowed: boolean;
  onReportUpdated: (match: PublishedMatchDetail) => void;
};

function sourceSummary(source: SourceDataRebuildSource): string {
  if (source.classification === 'blocked') return `zablokowany: ${source.blocking_reason || 'brak bezpiecznej ścieżki'}`;
  const data = source.classification === 'already_current' ? 'dane aktualne' : 'statystyki zostaną przeliczone';
  const visual = source.video_disposition === 'historical_preserved'
    ? 'Review video pozostanie historyczne'
    : source.video_disposition === 'current_preserved'
      ? 'Review video pozostanie aktualne'
      : 'Review video niedostępne';
  return `${data} · ${visual}`;
}

function phaseLabel(phase?: string): string {
  const labels: Record<string, string> = {
    preflight: 'Sprawdzanie źródeł',
    building_stats: 'Przeliczanie statystyk',
    building_report: 'Budowanie raportu źródłowego',
    publishing_source: 'Aktualizacja publikacji źródłowej',
    refreshing_merged: 'Odświeżanie meczu zbiorczego',
    already_current: 'Źródło jest aktualne',
    completed: 'Mecz zbiorczy zaktualizowany',
    failed: 'Przebudowa nie powiodła się',
  };
  return labels[phase || ''] || 'Przebudowa danych źródłowych';
}

function isActive(job: SourceDataRebuildJob | null): boolean {
  return job?.status === 'queued' || job?.status === 'running';
}

export function MergedSourceDataRebuildPanel({ mergedId, devAllowed, onReportUpdated }: Props) {
  const [preflight, setPreflight] = useState<SourceDataRebuildPreflight | null>(null);
  const [job, setJob] = useState<SourceDataRebuildJob | null>(null);
  const [status, setStatus] = useState('');

  useEffect(() => {
    if (!devAllowed) return;
    let cancelled = false;
    void Promise.all([
      previewMergedSourceDataRebuild(mergedId),
      getMergedSourceDataRebuildStatus(mergedId),
    ]).then(([nextPreview, nextJob]) => {
      if (cancelled) return;
      setPreflight(nextPreview);
      setJob(nextJob.status === 'idle' ? null : nextJob);
    }).catch((error: unknown) => {
      if (!cancelled) setStatus(errorMessage(error));
    });
    return () => { cancelled = true; };
  }, [devAllowed, mergedId]);

  useEffect(() => {
    if (!devAllowed || !isActive(job)) return undefined;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void getMergedSourceDataRebuildStatus(mergedId).then(async (next) => {
        if (cancelled) return;
        setJob(next);
        if (next.status === 'completed') {
          const updated = await getPublishedMatch(mergedId);
          if (!cancelled) {
            onReportUpdated(updated);
            setPreflight(await previewMergedSourceDataRebuild(mergedId));
          }
        }
      }).catch((error: unknown) => {
        if (!cancelled) setStatus(errorMessage(error));
      });
    }, 1_500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [devAllowed, job?.status, mergedId, onReportUpdated]);

  if (!devAllowed) return null;
  const sources = job?.sources || preflight?.sources || [];
  const progress = job?.progress;
  const canStart = preflight?.status === 'ready' && !isActive(job);

  async function start() {
    setStatus('');
    try {
      const next = await rebuildMergedSourceData(mergedId);
      setJob(next);
      if (next.status === 'blocked') setPreflight(next.preflight || null);
      if (next.status === 'completed') {
        const updated = await getPublishedMatch(mergedId);
        onReportUpdated(updated);
      }
    } catch (error) {
      setStatus(errorMessage(error));
    }
  }

  return <section className='panel report-actions'>
    <h2>Przebudowa danych źródłowych</h2>
    <p className='muted'>Dev-only: aktualizuje bezpieczne dane pochodne wszystkich źródeł, bez renderowania Review MP4.</p>
    <button type='button' onClick={() => void start()} disabled={!canStart}>
      Przebuduj mecze źródłowe
    </button>
    {preflight?.status === 'blocked' && <p className='status'>Przebudowa jest zablokowana — popraw wskazane źródło przed uruchomieniem.</p>}
    {sources.length > 0 && <ul className='compact-list'>
      {sources.map((source) => <li key={source.published_id}>
        <strong>{source.source_match_id}</strong> · {sourceSummary(source)}
      </li>)}
    </ul>}
    {progress && <div className='report-action-status'>
      <strong>{phaseLabel(progress.phase)}</strong>
      {progress.source_total > 0 && <div>Mecz {progress.source_index}/{progress.source_total}{progress.source_match_id ? ` — ${progress.source_match_id}` : ''}</div>}
      {progress.processed_units != null && progress.total_units != null && <div>{progress.processed_units} / {progress.total_units}</div>}
    </div>}
    {job?.status === 'failed' && <p className='status'>
      {job.failure?.published_id ? `${job.failure.published_id}: ` : ''}
      {job.failure?.detail || 'Przebudowa danych źródłowych nie powiodła się.'}
    </p>}
    {job?.status === 'completed' && <p className='status success'>Mecz zbiorczy korzysta z aktualnych danych źródłowych.</p>}
    {status && <p className='status'>{status}</p>}
  </section>;
}
