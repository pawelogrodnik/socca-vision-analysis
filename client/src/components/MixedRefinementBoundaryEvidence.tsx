import { artifactUrl } from '../api';
import type { ConcurrentLaneRefinement, MixedBoundaryRefinement } from '../types';
import { formatReviewTime } from '../utils/reviewedOutputPresentation';
import { ReviewedEvidenceImage } from './ReviewedEvidenceImage';

type Props = {
  matchId: string;
  refinement: Pick<MixedBoundaryRefinement, 'boundary_crops'> | Pick<ConcurrentLaneRefinement, 'boundary_crops'>;
};

export function MixedRefinementBoundaryEvidence({ matchId, refinement }: Props) {
  const { after, before } = refinement.boundary_crops;
  return <div className='mixed-refinement-boundaries' aria-label='Dokładne widoki graniczne'>
    <figure>
      <ReviewedEvidenceImage src={artifactUrl(matchId, after.artifact)} alt='Lewy widok graniczny — podział następuje po tym widoku' />
      <figcaption>Po tym widoku · {formatReviewTime(after.time_sec)}</figcaption>
    </figure>
    <span className='mixed-refinement-boundary-arrow' aria-hidden='true'>→</span>
    <figure>
      <ReviewedEvidenceImage src={artifactUrl(matchId, before.artifact)} alt='Prawy widok graniczny — podział następuje przed tym widokiem' />
      <figcaption>Przed tym widokiem · {formatReviewTime(before.time_sec)}</figcaption>
    </figure>
  </div>;
}
