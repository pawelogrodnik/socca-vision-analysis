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
        <small>
          {moment.evidence.primary_signal === 'attacking_momentum'
            ? `Momentum ${Math.round(moment.importance_score * 100)}% · eksperymentalne`
            : `Rozpoznane posiadanie ${Math.round(moment.importance_score * 100)}%`}
        </small>
      </div>
      {currentYouTubeVideoId
        ? <a href={youtubeWatchUrl(currentYouTubeVideoId, moment.time_sec)}>Zobacz moment</a>
        : localVideoReady
          ? <button type='button' onClick={() => onSeekLocalVideo(moment.time_sec)}>Zobacz moment</button>
          : null}
    </article>)}
  </section>;
}
