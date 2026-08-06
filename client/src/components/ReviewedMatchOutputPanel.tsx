import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { finalizeReviewedIdentity, generateReviewedOutput, getReviewedIdentity, getReviewedIdentityAt, getReviewedOutputStatus, getReviewedStats, reviewedVideoUrl } from '../api';
import { errorMessage } from '../lib/helpers';
import type { ReviewedIdentityAt, ReviewedIdentityDocument, ReviewedOutputJob, ReviewedStatsResponse } from '../types';
import { ReviewedPlayerStatsTable } from './ReviewedPlayerStatsTable';

export function ReviewedMatchOutputPanel({ matchId }: { matchId: string }) {
  const [identity, setIdentity] = useState<ReviewedIdentityDocument | null>(null);
  const [job, setJob] = useState<ReviewedOutputJob | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [includeMinimap, setIncludeMinimap] = useState(true);
  const [includeBall, setIncludeBall] = useState(true);
  const [showNumber, setShowNumber] = useState(false);
  const [atTime, setAtTime] = useState<ReviewedIdentityAt | null>(null);
  const [stats, setStats] = useState<ReviewedStatsResponse | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  async function refresh() {
    try {
      const [nextIdentity, nextJob] = await Promise.all([getReviewedIdentity(matchId), getReviewedOutputStatus(matchId)]);
      setIdentity(nextIdentity); setJob(nextJob);
      if (nextJob.status === 'completed') setStats(await getReviewedStats(matchId));
    } catch (error) { setMessage(errorMessage(error)); }
  }
  useEffect(() => { void refresh(); }, [matchId]);
  useEffect(() => {
    if (job?.status !== 'queued' && job?.status !== 'running') return undefined;
    const timer = window.setInterval(() => { void refresh(); }, 1500);
    return () => window.clearInterval(timer);
  }, [job?.status]);
  async function finalize() {
    setBusy(true); setMessage('Finalizuję reviewed identity...');
    try { setIdentity(await finalizeReviewedIdentity(matchId)); setMessage('Zapisano reviewed identity. Możesz wygenerować wideo.'); }
    catch (error) { setMessage(errorMessage(error)); } finally { setBusy(false); }
  }
  async function generate() {
    setBusy(true); setMessage('Dodano render reviewed video do kolejki...');
    try { setJob(await generateReviewedOutput(matchId, { include_minimap: includeMinimap, include_ball: includeBall, show_roster_number: showNumber })); }
    catch (error) { setMessage(errorMessage(error)); } finally { setBusy(false); }
  }
  async function inspectCurrentTime() {
    if (!videoRef.current) return;
    try { setAtTime(await getReviewedIdentityAt(matchId, videoRef.current.currentTime)); }
    catch (error) { setMessage(errorMessage(error)); }
  }
  const summary = identity?.summary;
  const canGenerate = identity?.status === 'partial_reviewed' || identity?.status === 'complete_reviewed';
  return <section className='panel reviewed-output-panel'>
    <h2>Reviewed match output</h2>
    <p>Imiona są widoczne tylko dla potwierdzonych zawodników. Pozostali dostają stabilne oznaczenia A01/B01.</p>
    {summary && <div className='stats-grid'>
      <span>Potwierdzone tracklety: <strong>{summary.confirmed}</strong></span><span>Nieprzypisane tracklety: <strong>{summary.unresolved}</strong></span><span>Wymaga sprawdzenia: <strong>{summary.conflicted}</strong></span><span>Potwierdzone wykryte obserwacje: <strong>{summary.confirmed_detected_observation_ratio === null ? '—' : `${Math.round(summary.confirmed_detected_observation_ratio * 100)}%`}</strong></span>
    </div>}
    <p>Status reviewed identity: <strong>{identity?.status ?? 'ładowanie'}</strong></p>
    <div className='row'><button type='button' onClick={() => void finalize()} disabled={busy}>Finalize reviewed identity</button></div>
    <fieldset disabled={!canGenerate || busy}><legend>Reviewed video</legend>
      <label><input type='checkbox' checked={includeMinimap} onChange={(event) => setIncludeMinimap(event.target.checked)} /> Minimapa</label>
      <label><input type='checkbox' checked={includeBall} onChange={(event) => setIncludeBall(event.target.checked)} /> Piłka, jeśli dostępna</label>
      <label><input type='checkbox' checked={showNumber} onChange={(event) => setShowNumber(event.target.checked)} /> Numer przy imieniu</label>
      <button type='button' onClick={() => void generate()}>Generate reviewed video</button>
    </fieldset>
    {job && <p>Status renderu: <strong>{job.status}</strong>{job.error?.message ? ` — ${job.error.message}` : ''}</p>}
    {job?.status === 'stale' && <p className='status'>Wideo jest nieaktualne po zmianie review. Sfinalizuj identity i wygeneruj je ponownie.</p>}
    {job?.status === 'completed' && job.video_digest && <>
      <video key={job.video_digest} ref={videoRef} className='reviewed-video' controls src={reviewedVideoUrl(matchId, job.video_digest)} />
      <div className='row'><button type='button' onClick={() => void inspectCurrentTime()}>Sprawdź przypisania w tym momencie</button></div>
      {atTime && <p>W klatce {atTime.frame}: {atTime.entities.length ? atTime.entities.map((entity) => entity.display_label).join(', ') : 'brak aktywnych oznaczeń'} · <Link to='/admin-panel'>Otwórz istniejący review, aby poprawić przypisanie</Link></p>}
      {stats && <ReviewedPlayerStatsTable document={stats} />}
    </>}
    {message && <p className='status'>{message}</p>}
  </section>;
}
