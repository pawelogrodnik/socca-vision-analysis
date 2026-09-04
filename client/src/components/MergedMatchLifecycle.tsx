import { useEffect, useRef, useState, type RefObject } from 'react';
import {
  deleteMergedMatchExternalVideo,
  generateMergedMatchVideo,
  getMergedMatchExternalVideo,
  getMergedMatchVideo,
  previewMergedRefresh,
  refreshMergedToLatest,
  regenerateMergedReport,
  saveMergedMatchExternalVideo,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type {
  MatchGroupExternalVideoStatus,
  MatchGroupRefreshPreview,
  MatchGroupVideoStatus,
  PublicMatchReport,
  PublishedMatchDetail,
} from '../types';
import { KeyMoments } from './KeyMoments';
import { MatchGroupExternalVideoSection } from './MatchGroupExternalVideoSection';

type Props = {
  mergedId: string;
  report: PublicMatchReport | null;
  onReportUpdated: (match: PublishedMatchDetail, report: PublicMatchReport | null) => void;
};

function duration(seconds: number): string {
  const value = Math.max(0, Math.round(seconds));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
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

export function MergedMatchLifecycle({ mergedId, report, onReportUpdated }: Props) {
  const [video, setVideo] = useState<MatchGroupVideoStatus | null>(null);
  const [externalVideo, setExternalVideo] = useState<MatchGroupExternalVideoStatus | null>(null);
  const [refreshPreview, setRefreshPreview] = useState<MatchGroupRefreshPreview | null>(null);
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  const localVideoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    let cancelled = false;
    setVideo(null);
    setExternalVideo(null);
    setRefreshPreview(null);
    void getMergedMatchVideo(mergedId)
      .then((next) => { if (!cancelled) setVideo(next); })
      .catch((error: unknown) => { if (!cancelled) setStatus(errorMessage(error)); });
    void getMergedMatchExternalVideo(mergedId)
      .then((next) => { if (!cancelled) setExternalVideo(next); })
      .catch(() => { if (!cancelled) setExternalVideo(null); });
    void previewMergedRefresh(mergedId)
      .then((next) => { if (!cancelled) setRefreshPreview(next); })
      .catch(() => { if (!cancelled) setRefreshPreview(null); });
    return () => { cancelled = true; };
  }, [mergedId]);

  useEffect(() => {
    if (!isVideoGenerationInFlight(video)) return undefined;
    let cancelled = false;
    let inFlight = false;
    let timer: number | undefined;
    const poll = async () => {
      if (cancelled || inFlight) return;
      inFlight = true;
      try {
        const next = await getMergedMatchVideo(mergedId);
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
  }, [mergedId, video?.status, video?.last_attempt?.status]);

  const regenerate = async () => {
    setBusy(true);
    setStatus('Regeneruję raport scalonego meczu...');
    try {
      const updated = await regenerateMergedReport(mergedId);
      onReportUpdated(updated, updated.public_report || null);
      setStatus('Raport scalonego meczu został przebudowany z aktualnie przypiętych fragmentów.');
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const refresh = async () => {
    setBusy(true);
    setStatus('Odświeżam do najnowszych danych...');
    try {
      const updated = await refreshMergedToLatest(mergedId);
      onReportUpdated(updated, updated.public_report || null);
      setRefreshPreview(await previewMergedRefresh(mergedId).catch(() => null));
      const nextVideo = await getMergedMatchVideo(mergedId).catch(() => null);
      if (nextVideo) setVideo(nextVideo);
      setStatus('Scalony mecz korzysta z najnowszych danych źródłowych.');
    } catch (error) {
      const message = errorMessage(error);
      try {
        setRefreshPreview(await previewMergedRefresh(mergedId).catch(() => null));
      } catch { /* keep the original conflict reason */ }
      setStatus(message);
    } finally {
      setBusy(false);
    }
  };

  const generateVideo = async () => {
    setBusy(true);
    setStatus('');
    try {
      setVideo(await generateMergedMatchVideo(mergedId));
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const saveExternalVideo = async (_id: string, url: string) => {
    setBusy(true);
    setStatus('');
    try {
      const next = await saveMergedMatchExternalVideo(mergedId, url);
      setExternalVideo(next);
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const removeExternalVideo = async () => {
    setBusy(true);
    setStatus('');
    try {
      setExternalVideo(await deleteMergedMatchExternalVideo(mergedId));
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  return <>
    <section className='card report-actions'>
      <div className='row between'>
        <div>
          <h2>Cykl życia scalonego meczu</h2>
          <p className='muted'>
            Raport jest budowany z przypiętych fragmentów. Regeneracja nie zmienia przypięć;
            odświeżenie podmienia je na najnowsze publikacje bez zmiany identyfikatora meczu.
          </p>
        </div>
        <span className='confidence-pill'>Scalony mecz</span>
      </div>
      <div className='report-actions-main'>
        <button type='button' className='secondary' onClick={() => void regenerate()} disabled={busy}>
          {busy ? 'Pracuję...' : 'Regeneruj raport'}
        </button>
        <button type='button' onClick={() => void refresh()} disabled={busy || refreshPreview?.status !== 'refreshable'}>
          Odśwież do najnowszych danych
        </button>
        <button
          type='button'
          className='secondary'
          onClick={() => void generateVideo()}
          disabled={busy || isVideoGenerationInFlight(video)}
        >
          {video?.status === 'ready' ? 'Regeneruj wideo' : 'Generuj wideo'}
        </button>
      </div>
      {refreshPreview?.status === 'current' && <p className='status success'>Dane źródłowe są aktualne.</p>}
      {refreshPreview?.status === 'refreshable' && <p className='status'>Dostępna jest nowsza wersja danych źródłowych.</p>}
      {refreshPreview?.status === 'blocked' && <p className='status'>Nie można odświeżyć: {refreshPreview.blocking_reasons[0]?.detail || 'źródła nie są zgodne.'}</p>}
      {status && <p className='report-action-status'>{status}</p>}
    </section>

    <MergedMatchVideo
      report={report}
      video={video}
      externalVideo={externalVideo}
      localVideoRef={localVideoRef}
    />

    {report && (
      <KeyMoments
        report={report}
        video={video}
        externalVideo={externalVideo}
        onSeekLocalVideo={(timeSec) => { if (localVideoRef.current) localVideoRef.current.currentTime = timeSec; }}
      />
    )}

    <section className='panel'>
      <MatchGroupExternalVideoSection
        groupId={mergedId}
        localVideo={video || undefined}
        externalVideo={externalVideo || undefined}
        busy={busy}
        onSave={saveExternalVideo}
        onRemove={removeExternalVideo}
      />
    </section>
  </>;
}

function MergedMatchVideo({
  report,
  video,
  externalVideo,
  localVideoRef,
}: {
  report: PublicMatchReport | null;
  video: MatchGroupVideoStatus | null;
  externalVideo: MatchGroupExternalVideoStatus | null;
  localVideoRef: RefObject<HTMLVideoElement>;
}) {
  const external = externalVideo?.external_video;
  const durationText = report?.match.duration_sec != null ? duration(report.match.duration_sec) : '';
  if (externalVideo?.status === 'current' && external?.embed_url) {
    return <section className='panel'>
      <h2>Pełne wideo meczu</h2>
      <iframe
        className='external-video-frame'
        src={external.embed_url}
        title='Pełne wideo meczu na YouTube'
        allow='accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share'
        allowFullScreen
        referrerPolicy='strict-origin-when-cross-origin'
      />
      <p><a href={external.source_url}>Otwórz na YouTube</a>{video?.status === 'ready' && video.artifact_url && <> · <a href={video.artifact_url}>Otwórz lokalne wideo</a></>} · {durationText}</p>
    </section>;
  }
  return <>
    {externalVideo?.status === 'stale' && <section className='panel'><p>Link YouTube dotyczy starszej wersji łącznego wideo. <a href={external?.source_url}>Otwórz poprzedni link na YouTube</a></p></section>}
    {externalVideo?.status === 'invalid' && <section className='panel'><p>Konfiguracja linku YouTube jest nieprawidłowa i nie została osadzona.</p></section>}
    {video?.status === 'ready' && video.artifact_url && <section className='panel'><h2>Pełne wideo meczu</h2><video ref={localVideoRef} key={video.generation_id ?? video.artifact_url} className='reviewed-video' controls src={video.artifact_url} /><p>{durationText}</p></section>}
    {video && video.status !== 'ready' && <section className='panel'><p>Łączne wideo: {videoMessage(video)}</p></section>}
    {video?.status === 'ready' && video.last_attempt?.status === 'generating' && <section className='panel'><p>Pełne wideo meczu jest gotowe — trwa regeneracja nowszej wersji.</p></section>}
  </>;
}
