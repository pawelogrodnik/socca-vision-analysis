import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { artifactUrl, getPublishedMatch, getStaticPublicMatchReport, rebuildPublishedMatch } from '../api';
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
  const [busyAction, setBusyAction] = useState<'rebuild' | null>(null);
  const [actionStatus, setActionStatus] = useState('');

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

  async function rebuildPublication() {
    if (!matchId || busyAction) return;
    setBusyAction('rebuild');
    setActionStatus('Przebudowuję publikację...');
    try {
      const rebuilt = await rebuildPublishedMatch(matchId);
      setMatch(rebuilt);
      setPublicReport(rebuilt.public_report || null);
      setActionStatus('Publikacja została przebudowana z najnowszych danych lokalnych.');
    } catch (error) {
      setActionStatus(errorMessage(error));
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <main className='app'>
      <section className='hero compact-hero'>
        <p className='eyebrow'>Opublikowany raport meczu</p>
        <h1>{publicReport?.match.title || match?.title || 'Raport meczu'}</h1>
        <p>
          Raport dla zawodników: statystyki drużyn, rozpoznani gracze i ich heatmapy,
          bez technicznych danych trackera.
        </p>
        <div className='row'>
          <Link to='/'>Lista meczów</Link>
          <Link to='/admin-panel'>Panel admin</Link>
        </div>
      </section>

      {loading && (
        <p className='loading-line'>
          <span className='spinner' />
          Ładuję publiczny raport...
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
          busyAction={busyAction}
          status={actionStatus}
          onRebuildPublished={match ? rebuildPublication : undefined}
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
