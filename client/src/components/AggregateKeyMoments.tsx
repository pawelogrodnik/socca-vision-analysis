import type { AggregatePublicMatchReport, MatchGroupExternalVideoStatus, MatchGroupVideoStatus } from '../types';

type AggregateKeyMomentsProps = {
  report: AggregatePublicMatchReport;
  video: MatchGroupVideoStatus | null;
  externalVideo: MatchGroupExternalVideoStatus | null;
  onSeekLocalVideo: (timeSec: number) => void;
};

function clock(seconds: number): string {
  const value = Math.max(0, Math.floor(seconds));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}

export function youtubeWatchUrl(videoId: string, timeSec: number): string {
  return `https://www.youtube.com/watch?v=${encodeURIComponent(videoId)}&t=${Math.floor(Math.max(0, timeSec))}s`;
}

function percent(value: number | undefined): string | null {
  return typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value * 100)}%` : null;
}

type AggregateKeyMoment = NonNullable<AggregatePublicMatchReport['key_moments']>['moments'][number];

function evidenceLabel(moment: AggregateKeyMoment): string {
  const signal = moment.evidence.primary;
  if (moment.evidence.primary_signal === 'attacking_momentum') {
    const intensity = percent(signal?.intensity);
    const confidence = percent(signal?.confidence);
    return [
      intensity ? `Momentum: intensywność ${intensity}` : 'Momentum',
      confidence ? `pewność ${confidence}` : null,
      signal?.experimental === true ? 'eksperymentalne' : null,
    ].filter((item): item is string => item !== null).join(' · ');
  }
  const share = typeof signal?.share_percent === 'number' && Number.isFinite(signal.share_percent)
    ? `${Math.round(signal.share_percent)}%`
    : null;
  const coverage = percent(signal?.coverage);
  return [
    share ? `Rozpoznane posiadanie: ${share}` : 'Rozpoznane posiadanie',
    coverage ? `pokrycie ${coverage}` : null,
  ].filter((item): item is string => item !== null).join(' · ');
}

export function AggregateKeyMoments({ report, video, externalVideo, onSeekLocalVideo }: AggregateKeyMomentsProps) {
  const keyMoments = report.key_moments;
  if (!keyMoments?.moments.length) return null;

  const teamNames = new Map(report.teams.map((team) => [team.team_id, team.team_name || team.team_id]));
  const currentYouTubeVideoId = externalVideo?.status === 'current'
    ? externalVideo.external_video?.video_id
    : undefined;
  const localVideoReady = video?.status === 'ready' && Boolean(video.artifact_url);

  return <section className='panel key-moments'>
    <h2>Najważniejsze momenty</h2>
    {keyMoments.moments.map((moment) => <article key={moment.moment_id} className='key-moment-card'>
      <strong>{clock(moment.time_sec)}</strong>
      <div>
        <h3>{moment.headline} {teamNames.get(moment.team_id) || moment.team_id}</h3>
        <small>{evidenceLabel(moment)}</small>
      </div>
      {currentYouTubeVideoId
        ? <a href={youtubeWatchUrl(currentYouTubeVideoId, moment.time_sec)}>Zobacz moment</a>
        : localVideoReady
          ? <button type='button' onClick={() => onSeekLocalVideo(moment.time_sec)}>Zobacz moment</button>
          : null}
    </article>)}
  </section>;
}
