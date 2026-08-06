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

  async function finalizeOnly() {
    setBusy(true);
    setMessage('Finalizuję reviewed identity...');
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
        ? 'Reviewed identity nie zmieniło się; istniejący render jest aktualny.'
        : 'Zapisano reviewed identity. Wygeneruj aktualne wideo.');
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function generate() {
    setBusy(true);
    setMessage('Dodano render reviewed video do kolejki...');
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
    setMessage('Finalizuję poprawki identity...');
    try {
      const nextIdentity = await finalizeReviewedIdentity(matchId);
      setIdentity(nextIdentity);
      if (nextIdentity.status === 'blocked') {
        setMessage('Finalizacja jest hard-blocked. Render nie został uruchomiony.');
        return;
      }
      setAtTime(null);
      setStats(null);
      setMessage('Identity sfinalizowane. Generuję nowy reviewed output...');
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
    const currentTime = videoRef.current.currentTime;
    try {
      setAtTime(await getReviewedIdentityAt(matchId, currentTime));
    } catch (error) {
      setMessage(errorMessage(error));
    }
  }

  function correctionSaved(
    entity: ReviewedIdentityAtEntity,
    result: ReviewedCorrectionResponse,
  ) {
    setDirty(true);
    setStats(null);
    if (identity) setIdentity({ ...identity, status: 'stale' });
    const allocation = result.allocated_stable_slot_id
      ? ` Utworzono nowego zawodnika jako ${result.allocated_stable_slot_id}.`
      : '';
    setMessage(
      `Zapisano poprawkę dla ${entity.candidate_subject_id}.${allocation} `
      + 'Reviewed identity i statystyki wymagają ponownej finalizacji.',
    );
  }

  const summary = identity?.summary;
  const canGenerate = identity?.status === 'partial_reviewed'
    || identity?.status === 'complete_reviewed';

  return <section className='panel reviewed-output-panel'>
    <h2>Reviewed match output</h2>
    <p>Imiona są widoczne tylko dla potwierdzonych zawodników. Zakotwiczone fragmenty używają bounded slotów A01–A14/B01–B14, a niezakotwiczone pozostają A?/B?/U?.</p>
    {summary && <div className='stats-grid'>
      <span>Potwierdzone tracklety: <strong>{summary.confirmed}</strong></span>
      <span>Nieprzypisane tracklety: <strong>{summary.unresolved}</strong></span>
      <span>Wymaga sprawdzenia: <strong>{summary.conflicted}</strong></span>
      <span>Potwierdzone wykryte obserwacje: <strong>{summary.confirmed_detected_observation_ratio === null ? '—' : `${Math.round(summary.confirmed_detected_observation_ratio * 100)}%`}</strong></span>
    </div>}
    <p>Status reviewed identity: <strong>{identity?.status ?? 'ładowanie'}</strong></p>
    {!dirty && <div className='row'>
      <button type='button' onClick={() => void finalizeOnly()} disabled={busy}>Finalize reviewed identity</button>
    </div>}
    {dirty && <div className='reviewed-stale-banner'>
      <strong>To wideo pokazuje stan sprzed zapisanych poprawek.</strong>
      <span>Sfinalizuj identity i wygeneruj nowy output, aby zobaczyć zmiany.</span>
      <button type='button' onClick={() => void finalizeAndRegenerate()} disabled={busy}>
        Finalize and regenerate
      </button>
    </div>}
    <fieldset disabled={!canGenerate || busy}><legend>Reviewed video</legend>
      <label><input type='checkbox' checked={includeMinimap} onChange={(event) => setIncludeMinimap(event.target.checked)} /> Minimapa</label>
      <label><input type='checkbox' checked={includeBall} onChange={(event) => setIncludeBall(event.target.checked)} /> Piłka, jeśli dostępna</label>
      <label><input type='checkbox' checked={showNumber} onChange={(event) => setShowNumber(event.target.checked)} /> Numer przy imieniu</label>
      {!dirty && <button type='button' onClick={() => void generate()}>Generate reviewed video</button>}
    </fieldset>
    {job && <p>Status renderu: <strong>{job.status}</strong>{job.error?.message ? ` — ${job.error.message}` : ''}</p>}
    {job?.status === 'stale' && <p className='status'>Wideo jest nieaktualne po zmianie review. Sfinalizuj identity i wygeneruj je ponownie.</p>}
    {job?.status === 'completed' && job.video_digest && <>
      <video key={job.video_digest} ref={videoRef} className='reviewed-video' controls src={reviewedVideoUrl(matchId, job.video_digest)} />
      <div className='row'>
        <button type='button' onClick={() => void inspectCurrentTime()}>Sprawdź przypisania w tym momencie</button>
      </div>
      {atTime && <ReviewedIdentityAtTimePanel
        matchId={matchId}
        document={atTime}
        onCorrectionSaved={correctionSaved}
      />}
      {!dirty && stats && <ReviewedPlayerStatsTable document={stats} />}
      {dirty && <p className='status'>Statystyki ukryto, ponieważ po zapisanych poprawkach są nieaktualne.</p>}
    </>}
    {message && <p className='status'>{message}</p>}
  </section>;
}
