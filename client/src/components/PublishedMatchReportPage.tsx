import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { artifactUrl, getPublishedMatch } from '../api';
import { errorMessage } from '../lib/helpers';
import type { PublishedMatchDetail } from '../types';
import {
  MatchReportContent,
  sourceFromPublishedPackage,
} from './MatchReportContent';

export function PublishedMatchReportPage() {
  const { matchId } = useParams();
  const [match, setMatch] = useState<PublishedMatchDetail | null>(null);
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
        setStatus('');
      })
      .catch((error) => {
        setMatch(null);
        setStatus(errorMessage(error));
      })
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
        <h1>{match?.title || 'Raport meczu'}</h1>
        <p>
          Publiczny snapshot raportu z bazy SQLite. Pokazuje realnie przypisanych
          zawodnikow oraz anonimowe stable sloty tylko w kontekscie tego meczu.
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

      {reportSource && (
        <MatchReportContent
          source={reportSource}
          mode='published'
          artifactHref={(artifactName) =>
            artifactUrl(reportSource.artifactMatchId || reportSource.id, artifactName)
          }
        />
      )}
    </main>
  );
}
