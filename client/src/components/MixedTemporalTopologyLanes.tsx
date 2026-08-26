import { artifactUrl } from '../api';
import type { MixedPlayerCase } from '../types';
import { mixedTimeForFrame, sortedMixedEvidenceCrops } from '../utils/mixedPlayersReview';
import { formatReviewTime } from '../utils/reviewedOutputPresentation';

type Props = {
  matchId: string;
  reviewCase: MixedPlayerCase;
};

function frameLabel(reviewCase: MixedPlayerCase, frame: number): string {
  const time = mixedTimeForFrame(reviewCase, frame);
  return time === null ? `klatka ${frame}` : formatReviewTime(time);
}

function relativeRange(reviewCase: MixedPlayerCase, start: number, end: number) {
  const span = Math.max(1, reviewCase.frame_end - reviewCase.frame_start + 1);
  return {
    left: `${Math.max(0, (start - reviewCase.frame_start) / span) * 100}%`,
    width: `${Math.max(1 / span, (end - start + 1) / span) * 100}%`,
  };
}

export function MixedTemporalTopologyLanes({ matchId, reviewCase }: Props) {
  const topology = reviewCase.temporal_topology;
  if (!topology || topology.kind !== 'concurrent') return null;
  const crops = sortedMixedEvidenceCrops(reviewCase.temporal_evidence.anchor_crops);

  return <section className='mixed-concurrent-topology' aria-label='Równoległe tracklety'>
    <div className='mixed-topology-warning' role='alert'>
      <strong>Ten przypadek zawiera tracklety występujące równocześnie.</strong>
      <span>Nie można go bezpiecznie rozdzielić jedną granicą czasu. Kilku zawodników jest widocznych w nakładających się fragmentach materiału.</span>
    </div>
    <div className='mixed-topology-scale' aria-hidden='true'>
      <span>{frameLabel(reviewCase, reviewCase.frame_start)}</span>
      <span>{frameLabel(reviewCase, reviewCase.frame_end)}</span>
    </div>
    <div className='mixed-topology-lanes'>
      {topology.tracklets.map((tracklet) => {
        const laneCrops = crops.filter((crop) => crop.tracklet_id === tracklet.tracklet_id);
        return <article className='mixed-topology-lane' key={tracklet.tracklet_id}>
          <header>
            <strong>{tracklet.tracklet_id}</strong>
            <span>{frameLabel(reviewCase, tracklet.frame_start)}–{frameLabel(reviewCase, tracklet.frame_end)}</span>
            <small>{tracklet.observation_count} obserwacji</small>
          </header>
          <div className='mixed-topology-track'>
            {topology.overlap_ranges.map((overlap) => overlap.tracklet_ids.includes(tracklet.tracklet_id) && <span
              className='mixed-topology-overlap'
              key={`${overlap.frame_start}-${overlap.frame_end}-${tracklet.tracklet_id}`}
              style={relativeRange(reviewCase, overlap.frame_start, overlap.frame_end)}
              title={`Nakładanie ${frameLabel(reviewCase, overlap.frame_start)}–${frameLabel(reviewCase, overlap.frame_end)}`}
            />)}
            <span className='mixed-topology-tracklet-range' style={relativeRange(reviewCase, tracklet.frame_start, tracklet.frame_end)} />
          </div>
          <div className='mixed-topology-crops'>
            {laneCrops.map((crop) => <figure key={crop.anchor_crop_id} className={`team-${(crop.team_label || 'u').toLowerCase()}`}>
              <img src={artifactUrl(matchId, crop.artifact)} alt={`Widok trackletu ${tracklet.tracklet_id}`} />
              <figcaption>{frameLabel(reviewCase, crop.frame)}</figcaption>
            </figure>)}
            {laneCrops.length === 0 && <span className='mixed-topology-no-crop'>Brak reprezentatywnego cropa w ograniczonym podglądzie.</span>}
          </div>
        </article>;
      })}
    </div>
  </section>;
}
