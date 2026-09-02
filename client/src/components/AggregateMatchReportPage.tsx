import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getMatchGroupReport, getMatchGroupVideo } from '../api';
import { errorMessage } from '../lib/helpers';
import type { AggregatePublicMatchReport, MatchGroupCompatibility, MatchGroupVideoStatus } from '../types';
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
  const [status, setStatus] = useState('');
  useEffect(() => {
    if (!groupId) return;
    void Promise.all([getMatchGroupReport(groupId), getMatchGroupVideo(groupId)]).then(([response, videoStatus]) => {
      setReport(response.report); setValidation(response.validation); setVideo(videoStatus);
    }).catch((error: unknown) => setStatus(errorMessage(error)));
  }, [groupId]);
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
      {video?.status === 'ready' && video.artifact_url && <section className='panel'><h2>Pełne wideo meczu</h2><video className='reviewed-video' controls src={video.artifact_url} /><p>{duration(report.timing.timeline_span_sec)}</p></section>}
      {video && video.status !== 'ready' && <section className='panel'><p>Łączne wideo: {videoMessage(video)}</p></section>}
      <AggregateMatchReportContent report={report} />
    </>}
  </main>;
}

function videoMessage(video: MatchGroupVideoStatus): string {
  if (video.status === 'generating') return 'jest przygotowywane w tle.';
  if (video.status === 'not_generated') return 'nie zostało jeszcze wygenerowane.';
  if (video.status === 'unavailable_source_video') return 'nie jest dostępne, ponieważ co najmniej jeden fragment nie ma zweryfikowanego końcowego wideo Review.';
  if (video.status === 'stale') return 'wymaga ponownego wygenerowania, ponieważ źródłowy fragment lub jego publikacja uległy zmianie.';
  return 'nie zostało wygenerowane; poprzednia poprawna wersja pozostaje bez zmian.';
}
