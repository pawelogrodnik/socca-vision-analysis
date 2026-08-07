import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { artifactUrl, createMatchPackage, getMatch, getReviewWorkflow, publishLocalMatch } from '../api';
import { errorMessage } from '../lib/helpers';
import { reportWorkflowGate } from '../lib/reviewWorkflowGating';
import type { Match, ReviewWorkflow } from '../types';
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
  const [workflow, setWorkflow] = useState<ReviewWorkflow | null>(null);

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

  useEffect(() => {
    if (!matchId) return;
    let ignore = false;
    getReviewWorkflow(matchId)
      .then((value) => {
        if (!ignore) setWorkflow(value);
      })
      .catch((error) => {
        if (!ignore) setStatus(errorMessage(error));
      });
    return () => { ignore = true; };
  }, [matchId]);

  const reportSource = useMemo(
    () => (match ? sourceFromLocalMatch(match) : null),
    [match],
  );
  const workflowGate = reportWorkflowGate(workflow);

  async function refreshMatch() {
    if (!matchId) return;
    const [nextMatch, nextWorkflow] = await Promise.all([getMatch(matchId), getReviewWorkflow(matchId)]);
    setMatch(nextMatch);
    setWorkflow(nextWorkflow);
  }

  async function buildPackage() {
    if (!matchId || busyAction) return;
    if (!workflowGate.allowed) {
      setActionStatus('Publikacja jest zablokowana do czasu zatwierdzenia Video QA.');
      return;
    }
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
    if (!workflowGate.allowed) {
      setActionStatus('Publikacja jest zablokowana do czasu zatwierdzenia Video QA.');
      return;
    }
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

      {match && !workflow?.review_complete && <section className='status'>
        <strong>Review meczu nie jest jeszcze zakończony.</strong>
        <p>Wróć do kroku Review zawodników, aby dokończyć identyfikację i sprawdzenie wideo.</p>
        <Link to='/admin-panel'>Wróć do Identity Review</Link>
      </section>}

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
          workflowAllowed={workflowGate.allowed}
          workflowReason={workflowGate.allowed ? undefined : `Najpierw zakoncz Review i zatwierdz Video QA (${workflowGate.reasonCode}).`}
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
