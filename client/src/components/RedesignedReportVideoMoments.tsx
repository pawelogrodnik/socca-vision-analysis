import { useMemo, useState } from 'react';
import type { MatchGroupExternalVideoStatus, PublicMatchReport } from '../types';
import { formatReportClock } from '../lib/redesignedPublicReportPresentation';

type Props = {
  report: PublicMatchReport;
  externalVideo: MatchGroupExternalVideoStatus | null;
  editorAllowed: boolean;
  onEditKeyMoments: () => void;
};

export function embedAtTimestamp(embedUrl: string, timeSec: number): string {
  const url = new URL(embedUrl);
  url.searchParams.set('start', String(Math.max(0, Math.floor(timeSec))));
  url.searchParams.set('autoplay', '1');
  return url.toString();
}

export function RedesignedReportVideoMoments({ report, externalVideo, editorAllowed, onEditKeyMoments }: Props) {
  const configuredEmbedUrl = externalVideo?.status === 'current'
    ? externalVideo.external_video?.embed_url
    : null;
  const [embedUrl, setEmbedUrl] = useState<string | null>(configuredEmbedUrl || null);
  const moments = report.key_moments?.moments || [];
  const teamNames = useMemo(
    () => new Map(report.teams.map((team) => [team.team_id, team.team_name || team.team_label || team.team_id])),
    [report.teams],
  );

  if (!configuredEmbedUrl && !moments.length && !editorAllowed) return null;
  const playMoment = (timeSec: number) => {
    if (configuredEmbedUrl) setEmbedUrl(embedAtTimestamp(configuredEmbedUrl, timeSec));
  };

  return <section className='redesign-section redesign-video-section' aria-labelledby='redesign-moments-title'>
    <div className='redesign-section-heading'>
      <div><p className='redesign-kicker'>Wideo i analiza</p><h2 id='redesign-moments-title'>Najważniejsze momenty</h2></div>
      {editorAllowed ? <button className='redesign-quiet-button' type='button' onClick={onEditKeyMoments}>Edytuj momenty</button> : null}
    </div>
    <div className={`redesign-video-layout${configuredEmbedUrl ? '' : ' moments-only'}`}>
      {configuredEmbedUrl ? <div className='redesign-youtube-frame'>
        <iframe
          src={embedUrl || configuredEmbedUrl}
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
          {configuredEmbedUrl ? <button type='button' onClick={() => playMoment(moment.time_sec)}>Odtwórz</button> : null}
        </article>) : <p className='redesign-empty-state'>Brak opublikowanych momentów dla tego raportu.</p>}
      </div>
    </div>
  </section>;
}
