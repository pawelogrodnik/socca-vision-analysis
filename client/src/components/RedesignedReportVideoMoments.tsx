import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { loadYouTubeIframeApi, type YouTubeIframePlayer } from '../lib/youtubeIframePlayer';
import type { MatchGroupExternalVideoStatus, PublicMatchReport } from '../types';
import { formatReportClock } from '../lib/redesignedPublicReportPresentation';

type Props = {
  report: PublicMatchReport;
  externalVideo: MatchGroupExternalVideoStatus | null;
  externalVideoLoading?: boolean;
  editorAllowed: boolean;
  onEditKeyMoments: () => void;
};

export function youtubePlayerEmbedUrl(embedUrl: string, origin: string | null = typeof window === 'undefined' ? null : window.location.origin): string {
  const url = new URL(embedUrl);
  url.searchParams.set('enablejsapi', '1');
  if (origin) url.searchParams.set('origin', origin);
  return url.toString();
}

export function RedesignedReportVideoMoments({ report, externalVideo, externalVideoLoading = false, editorAllowed, onEditKeyMoments }: Props) {
  const configuredEmbedUrl = externalVideo?.external_video?.embed_url || null;
  const playerEmbedUrl = useMemo(
    () => configuredEmbedUrl ? youtubePlayerEmbedUrl(configuredEmbedUrl) : null,
    [configuredEmbedUrl],
  );
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const playerRef = useRef<YouTubeIframePlayer | null>(null);
  const playerReadyRef = useRef(false);
  const pendingSeekRef = useRef<number | null>(null);
  const [expanded, setExpanded] = useState(false);
  const moments = report.key_moments?.moments || [];
  const teamNames = useMemo(
    () => new Map(report.teams.map((team) => [team.team_id, team.team_name || team.team_label || team.team_id])),
    [report.teams],
  );

  const seekAndPlay = useCallback((timeSec: number) => {
    const player = playerRef.current;
    if (!player || !playerReadyRef.current) {
      pendingSeekRef.current = timeSec;
      return;
    }
    player.seekTo(timeSec, true);
    player.playVideo();
  }, []);

  useEffect(() => {
    if (!playerEmbedUrl || !iframeRef.current) return;
    let disposed = false;
    playerReadyRef.current = false;
    playerRef.current = null;
    void loadYouTubeIframeApi()
      .then((api) => {
        if (disposed || !iframeRef.current) return;
        const player = new api.Player(iframeRef.current, {
          events: {
            onReady: ({ target }) => {
              if (disposed) return;
              playerRef.current = target;
              playerReadyRef.current = true;
              const pendingSeek = pendingSeekRef.current;
              pendingSeekRef.current = null;
              if (pendingSeek != null) seekAndPlay(pendingSeek);
            },
          },
        });
        playerRef.current = player;
      })
      .catch(() => undefined);
    return () => {
      disposed = true;
      playerReadyRef.current = false;
      playerRef.current?.destroy();
      playerRef.current = null;
    };
  }, [playerEmbedUrl, seekAndPlay]);

  useEffect(() => {
    if (!expanded) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setExpanded(false);
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [expanded]);

  if (!configuredEmbedUrl && !moments.length && !editorAllowed && !externalVideoLoading) return null;

  return <>
    {expanded ? <div className='redesign-analysis-backdrop' aria-hidden='true' /> : null}
    <section className={`redesign-section redesign-video-section${expanded ? ' expanded' : ''}`} aria-labelledby={expanded ? undefined : 'redesign-moments-title'} role={expanded ? 'dialog' : undefined} aria-modal={expanded || undefined} aria-label={expanded ? 'Rozszerzona analiza meczu' : undefined}>
    <div className='redesign-section-heading'>
      <div><p className='redesign-kicker'>Wideo i analiza</p><h2 id='redesign-moments-title'>Najważniejsze momenty</h2></div>
      <div className='redesign-video-actions'>
        {editorAllowed ? <button className='redesign-quiet-button' type='button' onClick={onEditKeyMoments}>Edytuj momenty</button> : null}
        {configuredEmbedUrl ? <button className='redesign-quiet-button redesign-expand-analysis' type='button' data-desktop-only='true' aria-label={expanded ? 'Zwiń analizę' : 'Rozszerz analizę'} title={expanded ? 'Zwiń analizę' : 'Rozszerz analizę'} onClick={() => setExpanded((value) => !value)}><span aria-hidden='true'>{expanded ? '×' : '⛶'}</span></button> : null}
      </div>
    </div>
    {externalVideoLoading ? <p className='redesign-video-loading' role='status'><span className='spinner' aria-hidden='true' /> Sprawdzam zapisane wideo YouTube…</p> : null}
    <div className={`redesign-video-layout${configuredEmbedUrl ? '' : ' moments-only'}`}>
      {playerEmbedUrl ? <div className='redesign-youtube-frame'>
        <iframe
          ref={iframeRef}
          src={playerEmbedUrl}
          title='Wideo meczu na YouTube'
          allow='accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share'
          allowFullScreen
        />
      </div> : null}
      <div className='redesign-moments-panel' aria-label='Lista najważniejszych momentów'>
        {moments.length ? moments.map((moment) => <article className='redesign-moment-row' key={moment.moment_id}>
          <time dateTime={`PT${Math.max(0, Math.floor(moment.time_sec))}S`}>{formatReportClock(moment.time_sec)}</time>
          <div>
            <h3>{moment.headline}</h3>
            {moment.note ? <p>{moment.note}</p> : moment.team_id ? <p>{teamNames.get(moment.team_id) || moment.team_id}</p> : null}
          </div>
          {configuredEmbedUrl ? <button type='button' onClick={() => seekAndPlay(moment.time_sec)}>Odtwórz</button> : null}
        </article>) : <p className='redesign-empty-state'>Brak opublikowanych momentów dla tego raportu.</p>}
      </div>
    </div>
    </section>
  </>;
}
