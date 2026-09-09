import { useParams } from 'react-router-dom';
import { isPublishedReportId } from '../lib/redesignedPublicReportPresentation';
import { MatchReportPage } from './MatchReportPage';
import { RedesignedPublishedMatchReportPage } from './RedesignedPublishedMatchReportPage';

export function PublishedReportRoute() {
  const { matchId } = useParams();
  return isPublishedReportId(matchId)
    ? <RedesignedPublishedMatchReportPage />
    : <MatchReportPage />;
}
