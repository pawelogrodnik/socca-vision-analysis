import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getMatchGroupReport, getMatchGroupVideo } from '../api';
import { errorMessage } from '../lib/helpers';
import type { AggregatePublicMatchReport, MatchGroupCompatibility, MatchGroupExternalVideoStatus, MatchGroupVideoStatus } from '../types';
import { AggregateMatchReportContent } from './AggregateMatchReportContent';

function duration(seconds: number): string {
  const value = Math.max(0, Math.round(seconds));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}

export function AggregateMatchReportPage() {
  const { groupId } = useParams();
  const [report, setReport] = useState<AggregatePublicMatchReport | null>(null);
  const [validation, setValidation] = useState<MatchGroupCompatibility | null>(null);
  const [video, setVideo] = useState<MatchGroupVideoStatus | null>(null);
  const [externalVideo, setExternalVideo] = useState<MatchGroupExternalVideoStatus | null>(null);
  const [status, setStatus] = useState('');
  useEffect(() => {
    if (!groupId) return;
    void Promise.all([getMatchGroupReport(groupId), getMatchGroupVideo(groupId)]).then(([response, videoStatus]) => {
      setReport(response.report); setValidation(response.validation); setExternalVideo(response.external_video); setVideo(videoStatus);
    }).catch((error: unknown) => setStatus(errorMessage(error)));
  }, [groupId]);
  useEffect(() => {
    if (!groupId || !isVideoGenerationInFlight(video)) return undefined;
    let cancelled = false;
    let inFlight = false;
    let timer: number | undefined;
    const poll = async () => {
      if (cancelled || inFlight) return;
      inFlight = true;
      try {
        const next = await getMatchGroupVideo(groupId);
        if (!cancelled) setVideo(next);
      } catch (error) {
        if (!cancelled) setStatus(errorMessage(error));
      } finally {
        inFlight = false;
        if (!cancelled) timer = window.setTimeout(() => void poll(), 7_500);
      }
    };
    timer = window.setTimeout(() => void poll(), 7_500);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [groupId, video?.status, video?.last_attempt?.status]);
  return <main className='app'>
    <section className='hero compact-hero'>
      <p className='eyebrow'>Scalony raport</p>
      <h1>{report?.match.title || 'Raport łączony'}</h1>
      <p>Osobny raport logicznego meczu. Nie zmienia żadnego z raportów źródłowych.</p>
      <Link to='/match-groups'>Scalone raporty</Link>
    </section>
    {status && <p className='status'>{status}</p>}
    {validation && validation.status !== 'compatible' && <section className='status'>
      <strong>Raport jest nieaktualny.</strong> {validation.blocking_reasons[0]?.detail || 'Źródła wymagają ponownej weryfikacji.'}
    </section>}
    {!report && !status && <p className='loading-line'>Ładuję scalony raport…</p>}
    {report && <>
      <section className='panel'><h2>Podsumowanie</h2><p>Łączny analizowany czas: <strong>{duration(report.timing.analyzed_duration_sec)}</strong></p></section>
      <MatchGroupReportVideo video={video} externalVideo={externalVideo} durationText={duration(report.timing.timeline_span_sec)} />
      {video?.status === 'ready' && video.last_attempt?.status === 'generating' && <section className='panel'><p>Pełne wideo meczu jest gotowe — trwa regeneracja nowszej wersji.</p></section>}
      {video && video.status !== 'ready' && <section className='panel'><p>Łączne wideo: {videoMessage(video)}</p></section>}
      <AggregateMatchReportContent report={report} />
    </>}
  </main>;
}

function MatchGroupReportVideo({ video, externalVideo, durationText }: { video: MatchGroupVideoStatus | null; externalVideo: MatchGroupExternalVideoStatus | null; durationText: string }) {
  const external = externalVideo?.external_video;
  if (externalVideo?.status === 'current' && external?.embed_url) return <section className='panel'><h2>Pełne wideo meczu</h2><iframe className='external-video-frame' src={external.embed_url} title='Pełne wideo meczu na YouTube' allow='accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share' allowFullScreen referrerPolicy='strict-origin-when-cross-origin' /><p><a href={external.source_url}>Otwórz na YouTube</a>{video?.status === 'ready' && video.artifact_url && <> · <a href={video.artifact_url}>Otwórz lokalne wideo</a></>} · {durationText}</p></section>;
  return <>
    {externalVideo?.status === 'stale' && <section className='panel'><p>Link YouTube dotyczy starszej wersji łącznego wideo. <a href={external?.source_url}>Otwórz poprzedni link na YouTube</a></p></section>}
    {externalVideo?.status === 'invalid' && <section className='panel'><p>Konfiguracja linku YouTube jest nieprawidłowa i nie została osadzona.</p></section>}
    {video?.status === 'ready' && video.artifact_url && <section className='panel'><h2>Pełne wideo meczu</h2><video key={video.generation_id ?? video.artifact_url} className='reviewed-video' controls src={video.artifact_url} /><p>{durationText}</p></section>}
  </>;
}

function videoMessage(video: MatchGroupVideoStatus): string {
  if (video.status === 'generating') return 'jest przygotowywane w tle.';
  if (video.status === 'not_generated') return 'nie zostało jeszcze wygenerowane.';
  if (video.status === 'unavailable_source_video') return 'nie jest dostępne, ponieważ co najmniej jeden fragment nie ma zweryfikowanego końcowego wideo Review.';
  if (video.status === 'stale') return 'wymaga ponownego wygenerowania, ponieważ źródłowy fragment lub jego publikacja uległy zmianie.';
  return 'nie zostało wygenerowane; poprzednia poprawna wersja pozostaje bez zmian.';
}

function isVideoGenerationInFlight(video: MatchGroupVideoStatus | null): boolean {
  return video?.status === 'generating' || video?.last_attempt?.status === 'generating';
}
