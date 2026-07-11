import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { artifactUrl, getPublishedMatch, getStaticPublicMatchReport } from '../api';
import { errorMessage } from '../lib/helpers';
import type { PublicMatchReport, PublishedMatchDetail } from '../types';
import {
  MatchReportContent,
  sourceFromPublishedPackage,
} from './MatchReportContent';
import { PublicMatchReportContent } from './PublicMatchReportContent';
import { ReportActions } from './ReportActions';

export function PublishedMatchReportPage() {
  const { matchId } = useParams();
  const [match, setMatch] = useState<PublishedMatchDetail | null>(null);
  const [publicReport, setPublicReport] = useState<PublicMatchReport | null>(null);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!matchId) {
      setStatus('Missing published match id.');
      return;
    }
    setLoading(true);
    getPublishedMatch(matchId)
      .then((data) => {
        setMatch(data);
        setPublicReport(data.public_report || null);
        setStatus('');
      })
      .catch(() =>
        getStaticPublicMatchReport(matchId)
          .then((data) => {
            setMatch(null);
            setPublicReport(data);
            setStatus('');
          })
          .catch((error) => {
            setMatch(null);
            setPublicReport(null);
            setStatus(errorMessage(error));
          }),
      )
      .finally(() => setLoading(false));
  }, [matchId]);

  const reportSource = useMemo(
    () => (match ? sourceFromPublishedPackage(match.package) : null),
    [match],
  );

  return (
    <main className='app'>
      <section className='hero compact-hero'>
        <p className='eyebrow'>Published match report</p>
        <h1>{publicReport?.match.title || match?.title || 'Raport meczu'}</h1>
        <p>
          Publiczny raport meczowy dla zawodnikow: statystyki druzyn,
          potwierdzeni gracze i heatmapy bez technicznego review.
        </p>
        <div className='row'>
          <Link to='/'>Lista meczow</Link>
          <Link to='/admin-panel'>Panel admin</Link>
        </div>
      </section>

      {loading && (
        <p className='loading-line'>
          <span className='spinner' />
          Laduje publiczny raport...
        </p>
      )}
      {status && <p className='status'>{status}</p>}

      {(match || publicReport) && (
        <ReportActions
          mode='published'
          jsonDownload={{
            label: 'Pobierz public report JSON',
            filename: `${publicReport?.id || match?.id || 'public-report'}.json`,
            data: publicReport || match?.package,
          }}
        />
      )}

      {publicReport ? (
        <PublicMatchReportContent
          report={publicReport}
          assetHref={(path) => (path.startsWith('http') || path.startsWith('/') ? path : `/${path}`)}
        />
      ) : reportSource ? (
        <MatchReportContent
          source={reportSource}
          mode='published'
          artifactHref={(artifactName) =>
            artifactUrl(reportSource.artifactMatchId || reportSource.id, artifactName)
          }
        />
      ) : null}
    </main>
  );
}
