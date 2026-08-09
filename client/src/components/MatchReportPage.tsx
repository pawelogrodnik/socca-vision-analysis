import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  artifactUrl,
  getMatch,
  getReviewedMatchReport,
  getReviewWorkflow,
  publishLocalMatch,
} from '../api';
import { errorMessage } from '../lib/helpers';
import { reportWorkflowGate } from '../lib/reviewWorkflowGating';
import type { Match, PublicMatchReport, ReviewWorkflow } from '../types';
import {
  MatchReportContent,
  sourceFromLocalMatch,
} from './MatchReportContent';
import { ReportActions } from './ReportActions';
import { PublicMatchReportContent } from './PublicMatchReportContent';

type ReportBusyAction = 'publish' | 'replace' | null;

export function MatchReportPage() {
  const { matchId } = useParams();
  const [match, setMatch] = useState<Match | null>(null);
  const [status, setStatus] = useState('');
  const [actionStatus, setActionStatus] = useState('');
  const [busyAction, setBusyAction] = useState<ReportBusyAction>(null);
  const [loading, setLoading] = useState(false);
  const [workflow, setWorkflow] = useState<ReviewWorkflow | null>(null);
  const [reviewedReport, setReviewedReport] = useState<PublicMatchReport | null>(null);
  const [reportStatus, setReportStatus] = useState('');

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
    getReviewedMatchReport(matchId)
      .then((value) => {
        if (!ignore) {
          setReviewedReport(value);
          setReportStatus('');
        }
      })
      .catch((error) => {
        if (!ignore) {
          setReviewedReport(null);
          setReportStatus(errorMessage(error));
        }
      });
    return () => { ignore = true; };
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
    const [nextMatch, nextWorkflow, nextReport] = await Promise.all([
      getMatch(matchId),
      getReviewWorkflow(matchId),
      getReviewedMatchReport(matchId),
    ]);
    setMatch(nextMatch);
    setWorkflow(nextWorkflow);
    setReviewedReport(nextReport);
    setReportStatus('');
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
        <p className='eyebrow'>Raport meczu</p>
        <h1>{match?.title || 'Raport meczu'}</h1>
        <p>
          Czytelne podsumowanie drużyn i rozpoznanych zawodników po zakończonym review.
          Dane techniczne trackera nie są częścią głównego raportu.
        </p>
        <div className='row'>
          <Link to='/admin-panel'>Panel admin</Link>
          <Link to='/teams'>Drużyny</Link>
        </div>
      </section>

      {loading && (
        <p className='loading-line'>
          <span className='spinner' />
          Ładuję raport meczu...
        </p>
      )}
      {status && <p className='status'>{status}</p>}
      {reportStatus && workflow?.review_complete && (
        <p className='status'>Nie udało się przygotować raportu po review: {reportStatus}</p>
      )}

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
            label: reviewedReport ? 'Pobierz raport JSON' : 'Pobierz dane techniczne JSON',
            filename: reviewedReport ? `raport-${match.id}.json` : `match-${match.id}.json`,
            data: reviewedReport || match,
          }}
          busyAction={busyAction}
          status={actionStatus}
          onPublish={() => publish(Boolean(match.published_match_id))}
          publishLabel={match.published_match_id ? 'Zaktualizuj opublikowany raport' : 'Opublikuj raport'}
          workflowAllowed={workflowGate.allowed}
          workflowReason={workflowGate.allowed ? undefined : 'Najpierw zakończ Review i zatwierdź Video QA.'}
        />
      )}

      {reviewedReport ? (
        <PublicMatchReportContent report={reviewedReport} />
      ) : reportSource ? (
        <MatchReportContent
          source={reportSource}
          mode='local'
          artifactHref={(artifactName) => artifactUrl(reportSource.id, artifactName)}
        />
      ) : null}
    </main>
  );
}
