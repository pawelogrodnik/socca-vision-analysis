import { useEffect, useRef, useState } from 'react';

import {
  finalizeReviewedIdentity,
  generateReviewedOutput,
  getReviewedIdentity,
  getReviewedIdentityAt,
  getReviewedOutputStatus,
  getReviewedStats,
  reviewedVideoUrl,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type {
  ReviewedCorrectionResponse,
  ReviewedIdentityAt,
  ReviewedIdentityAtEntity,
  ReviewedIdentityDocument,
  ReviewedOutputJob,
  ReviewedStatsResponse,
} from '../types';
import {
  formatElapsedTime,
  formatReviewTime,
  reviewedIdentityStatusLabel,
  reviewedRenderStatusLabel,
  shouldShowInitialReviewCta,
} from '../utils/reviewedOutputPresentation';
import { clearReviewedDerivedOutput } from '../utils/reviewedOutputState';
import { ReviewedIdentityAtTimePanel } from './ReviewedIdentityAtTimePanel';
import { ReviewedPlayerStatsTable } from './ReviewedPlayerStatsTable';

export function ReviewedMatchOutputPanel({ matchId }: { matchId: string }) {
  const [identity, setIdentity] = useState<ReviewedIdentityDocument | null>(null);
  const [job, setJob] = useState<ReviewedOutputJob | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [includeMinimap, setIncludeMinimap] = useState(true);
  const [includeBall, setIncludeBall] = useState(true);
  const [showNumber, setShowNumber] = useState(false);
  const [atTime, setAtTime] = useState<ReviewedIdentityAt | null>(null);
  const [stats, setStats] = useState<ReviewedStatsResponse | null>(null);
  const [currentVideoTime, setCurrentVideoTime] = useState(0);
  const [now, setNow] = useState(Date.now());
  const videoRef = useRef<HTMLVideoElement | null>(null);

  async function refresh() {
    try {
      const [nextIdentity, nextJob] = await Promise.all([
        getReviewedIdentity(matchId),
        getReviewedOutputStatus(matchId),
      ]);
      setIdentity(nextIdentity);
      setJob(nextJob);
      const outputIsCurrent = nextJob.status === 'completed'
        && nextIdentity.status !== 'stale'
        && nextJob.source_snapshot_digest === nextIdentity.semantic_digest;
      if (outputIsCurrent) {
        setStats(await getReviewedStats(matchId));
        setDirty(false);
      } else if (nextJob.status === 'stale' || nextIdentity.status === 'stale') {
        setStats(null);
        setDirty(true);
      }
    } catch (error) {
      setMessage(errorMessage(error));
    }
  }

  useEffect(() => { void refresh(); }, [matchId]);
  useEffect(() => {
    if (job?.status !== 'queued' && job?.status !== 'running') return undefined;
    const timer = window.setInterval(() => { void refresh(); }, 1500);
    return () => window.clearInterval(timer);
  }, [job?.status]);
  useEffect(() => {
    if (job?.status !== 'queued' && job?.status !== 'running') return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [job?.status]);

  async function finalizeOnly() {
    setBusy(true);
    setMessage('Finalizuję review zawodników...');
    try {
      const cleared = clearReviewedDerivedOutput();
      setJob(cleared.job);
      setStats(cleared.stats);
      setAtTime(cleared.atTime);
      const nextIdentity = await finalizeReviewedIdentity(matchId);
      setIdentity(nextIdentity);
      const nextJob = await getReviewedOutputStatus(matchId);
      setJob(nextJob);
      setMessage(nextJob.status === 'completed'
        ? 'Review nie zmienił się; istniejące wideo jest aktualne.'
        : 'Zapisano review. Możesz teraz wygenerować aktualne wideo.');
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function generate() {
    setBusy(true);
    setMessage('Dodano przygotowanie wideo do kolejki...');
    try {
      setJob(await generateReviewedOutput(matchId, {
        include_minimap: includeMinimap,
        include_ball: includeBall,
        show_roster_number: showNumber,
      }));
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function finalizeAndRegenerate() {
    setBusy(true);
    setMessage('Przygotowuję aktualny wynik review...');
    try {
      const nextIdentity = await finalizeReviewedIdentity(matchId);
      setIdentity(nextIdentity);
      if (nextIdentity.status === 'blocked') {
        setMessage('Review wymaga decyzji. Wideo nie zostało uruchomione.');
        return;
      }
      setAtTime(null);
      setStats(null);
      setMessage('Generuję wideo do review...');
      setJob(await generateReviewedOutput(matchId, {
        include_minimap: includeMinimap,
        include_ball: includeBall,
        show_roster_number: showNumber,
      }));
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function inspectCurrentTime() {
    if (!videoRef.current) return;
    const time = videoRef.current.currentTime;
    setCurrentVideoTime(time);
    try {
      setAtTime(await getReviewedIdentityAt(matchId, time));
    } catch (error) {
      setMessage(errorMessage(error));
    }
  }

  function correctionSaved(_entity: ReviewedIdentityAtEntity, result: ReviewedCorrectionResponse) {
    setDirty(true);
    setStats(null);
    if (identity) setIdentity({ ...identity, status: 'stale' });
    const allocation = result.allocated_stable_slot_id
      ? ` Utworzono nowego zawodnika jako ${result.allocated_stable_slot_id}.`
      : '';
    setMessage(`Zapisano poprawkę.${allocation} Wideo i statystyki wymagają odświeżenia.`);
  }

  const summary = identity?.summary;
  const renderInProgress = job?.status === 'queued' || job?.status === 'running';
  const canGenerate = identity?.status === 'partial_reviewed' || identity?.status === 'complete_reviewed';
  const showInitialCta = shouldShowInitialReviewCta(identity?.status, job?.status);
  const elapsed = formatElapsedTime(job?.started_at ?? job?.created_at, now);
  const hasVideo = job?.status === 'completed' && Boolean(job.video_digest);

  return <section id='reviewed-match-output' className='panel reviewed-output-panel'>
    <div className='reviewed-output-heading'>
      <div>
        <p className='eyebrow'>Review</p>
        <h2>Review zawodników</h2>
        <p>Sprawdź oznaczenia zawodników, popraw widoczne błędy i wygeneruj końcowe wideo oraz statystyki.</p>
      </div>
      <div className='reviewed-status-list' aria-label='Status review'>
        <span className='reviewed-status-badge'>{reviewedIdentityStatusLabel(identity?.status)}</span>
        <span className='reviewed-status-badge'>{reviewedRenderStatusLabel(job?.status)}</span>
      </div>
    </div>

    {summary && <div className='reviewed-metric-grid' aria-label='Podsumowanie review'>
      <div className='reviewed-metric-card'><span>Potwierdzone</span><strong>{summary.confirmed}</strong><small>fragmentów</small></div>
      <div className='reviewed-metric-card'><span>Nierozpoznane</span><strong>{summary.unresolved}</strong><small>fragmentów</small></div>
      <div className='reviewed-metric-card'><span>Wymaga sprawdzenia</span><strong>{summary.conflicted}</strong><small>przypadków</small></div>
      <div className='reviewed-metric-card'><span>Pokrycie tożsamości</span><strong>{summary.confirmed_detected_observation_ratio === null ? '—' : `${Math.round(summary.confirmed_detected_observation_ratio * 100)}%`}</strong><small>wykrytych obserwacji</small></div>
    </div>}

    {showInitialCta && <div className='reviewed-next-step'>
      <div>
        <p className='eyebrow'>Następny krok</p>
        <h3>Przygotuj pierwsze wideo do sprawdzenia</h3>
        <p>Operacja wykorzysta istniejące wyniki analizy i nie uruchomi ponownie detekcji ani trackingu.</p>
      </div>
      <button type='button' onClick={() => void finalizeAndRegenerate()} disabled={busy || renderInProgress}>
        Przygotuj wideo do review
      </button>
    </div>}

    {renderInProgress && <div className='reviewed-rendering-card' role='status'>
      <span className='spinner' aria-hidden='true' />
      <div>
        <h3>Przygotowuję wideo do review…</h3>
        <p>Status: trwa renderowanie. Ten proces nie uruchamia ponownie YOLO ani trackingu.</p>
        {elapsed && <small>Czas od uruchomienia: {elapsed}</small>}
      </div>
    </div>}

    {dirty && <div className='reviewed-stale-banner'>
      <strong>Zapisano poprawki</strong>
      <span>Obecne wideo pokazuje stan sprzed zmian. Możesz zapisać kolejne poprawki albo odświeżyć wynik.</span>
      <button type='button' onClick={() => void finalizeAndRegenerate()} disabled={busy || renderInProgress}>
        Zastosuj poprawki i odśwież wideo
      </button>
    </div>}

    <details className='reviewed-video-settings'>
      <summary>Ustawienia wideo</summary>
      <div className='reviewed-checkboxes'>
        <label className='inline-check'><input type='checkbox' checked={includeMinimap} onChange={(event) => setIncludeMinimap(event.target.checked)} disabled={busy || renderInProgress} /> Pokaż minimapę</label>
        <label className='inline-check'><input type='checkbox' checked={includeBall} onChange={(event) => setIncludeBall(event.target.checked)} disabled={busy || renderInProgress} /> Pokaż piłkę</label>
        <label className='inline-check'><input type='checkbox' checked={showNumber} onChange={(event) => setShowNumber(event.target.checked)} disabled={busy || renderInProgress} /> Pokaż numer zawodnika przy imieniu</label>
      </div>
    </details>

    <details className='reviewed-advanced-options'>
      <summary>Zaawansowane opcje</summary>
      <div className='row'>
        <button type='button' className='secondary' onClick={() => void finalizeOnly()} disabled={busy || renderInProgress}>Zapisz sam review</button>
        <button type='button' className='secondary' onClick={() => void generate()} disabled={!canGenerate || busy || renderInProgress}>Wygeneruj wideo ponownie</button>
      </div>
    </details>

    {job?.status === 'failed' && <p className='status error'>Generowanie wideo nie powiodło się{job.error?.message ? `: ${job.error.message}` : '.'}</p>}
    {job?.status === 'stale' && <p className='status'>Wideo jest nieaktualne po zmianie review. Zastosuj poprawki, aby utworzyć aktualną wersję.</p>}

    {hasVideo && <section className='reviewed-video-section'>
      <h3>Wideo do review</h3>
      <p>Zatrzymaj nagranie na błędnej lub nierozpoznanej etykiecie, a następnie sprawdź osoby widoczne w tej klatce.</p>
      <video
        key={job?.video_digest}
        ref={videoRef}
        className='reviewed-video'
        controls
        src={reviewedVideoUrl(matchId, job?.video_digest ?? '')}
        onPause={(event) => setCurrentVideoTime(event.currentTarget.currentTime)}
        onSeeked={(event) => setCurrentVideoTime(event.currentTarget.currentTime)}
      />
      <button type='button' className='reviewed-inspect-button' onClick={() => void inspectCurrentTime()} disabled={busy}>
        Sprawdź osoby w klatce {formatReviewTime(currentVideoTime)}
      </button>
      {atTime && <ReviewedIdentityAtTimePanel matchId={matchId} document={atTime} onCorrectionSaved={correctionSaved} />}
      <details className='reviewed-stats-details'>
        <summary>Statystyki po review</summary>
        {!dirty && stats && <ReviewedPlayerStatsTable document={stats} />}
        {dirty && <p>Statystyki zostaną przeliczone po zastosowaniu poprawek.</p>}
      </details>
    </section>}

    <details className='reviewed-technical-details'>
      <summary>Szczegóły techniczne</summary>
      <p>Identity: {identity?.status ?? 'loading'} · render: {job?.status ?? 'loading'}</p>
      {identity?.semantic_digest && <p>Snapshot digest: {identity.semantic_digest}</p>}
      {job?.job_key && <p>Render job: {job.job_key}</p>}
    </details>
    {message && <p className='status'>{message}</p>}
  </section>;
}
