import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { artifactUrl, createMatchPackage, getMatch, publishLocalMatch } from '../api';
import { errorMessage } from '../lib/helpers';
import type { Match } from '../types';
import {
  MatchReportContent,
  sourceFromLocalMatch,
} from './MatchReportContent';
import { ReportActions } from './ReportActions';

type ReportBusyAction = 'package' | 'publish' | 'replace' | null;

export function MatchReportPage() {
  const { matchId } = useParams();
  const [match, setMatch] = useState<Match | null>(null);
  const [status, setStatus] = useState('');
  const [actionStatus, setActionStatus] = useState('');
  const [busyAction, setBusyAction] = useState<ReportBusyAction>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!matchId) {
      setStatus('Missing match id.');
      return;
    }
    setLoading(true);
    getMatch(matchId)
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
    () => (match ? sourceFromLocalMatch(match) : null),
    [match],
  );

  async function refreshMatch() {
    if (!matchId) return;
    setMatch(await getMatch(matchId));
  }

  async function buildPackage() {
    if (!matchId || busyAction) return;
    setBusyAction('package');
    setActionStatus('Generuje match_package.json...');
    try {
      await createMatchPackage(matchId);
      await refreshMatch();
      setActionStatus('Wygenerowano match_package.json.');
    } catch (error) {
      setActionStatus(errorMessage(error));
    } finally {
      setBusyAction(null);
    }
  }

  async function publish(replace = false) {
    if (!matchId || busyAction) return;
    setBusyAction(replace ? 'replace' : 'publish');
    setActionStatus(replace ? 'Nadpisuje opublikowany raport...' : 'Publikuje raport...');
    try {
      const published = await publishLocalMatch(matchId, replace);
      await refreshMatch();
      setActionStatus(`Opublikowano jako ${published.id}.`);
    } catch (error) {
      setActionStatus(errorMessage(error));
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <main className='app'>
      <section className='hero compact-hero'>
        <p className='eyebrow'>Match report</p>
        <h1>{match?.title || 'Raport meczu'}</h1>
        <p>
          Raport tracking-only dla pojedynczego meczu. Anonimowe sloty sa
          czescia raportu meczowego, ale nie sa agregowane do profili
          zawodnikow.
        </p>
        <div className='row'>
          <Link to='/admin-panel'>Panel admin</Link>
          <Link to='/teams'>Druzyny</Link>
        </div>
      </section>

      {loading && (
        <p className='loading-line'>
          <span className='spinner' />
          Laduje raport meczu...
        </p>
      )}
      {status && <p className='status'>{status}</p>}

      {match && (
        <ReportActions
          mode='local'
          packageHref={match.match_package ? artifactUrl(match.id, 'match_package.json') : undefined}
          publicReportPath={
            match.published_match_id
              ? `/published/matches/${encodeURIComponent(match.published_match_id)}/report`
              : undefined
          }
          jsonDownload={{
            label: 'Pobierz local match JSON',
            filename: `match-${match.id}.json`,
            data: match,
          }}
          busyAction={busyAction}
          status={actionStatus}
          onBuildPackage={buildPackage}
          onPublish={() => publish(false)}
          onReplacePublish={() => publish(true)}
        />
      )}

      {reportSource && (
        <MatchReportContent
          source={reportSource}
          mode='local'
          artifactHref={(artifactName) => artifactUrl(reportSource.id, artifactName)}
        />
      )}
    </main>
  );
}
