import { useEffect, useRef, useState } from 'react';

import {
  approveReviewVideoQa,
  getReviewedIdentity,
  getReviewedIdentityAt,
  getReviewedOutputStatus,
  getReviewedStats,
  reviewedVideoUrl,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type {
  ReviewedCorrectionResponse,
  ReviewedIdentityAt,
  ReviewedIdentityDocument,
  ReviewedOutputJob,
  ReviewedStatsResponse,
  ReviewWorkflow,
} from '../types';
import { formatReviewTime } from '../utils/reviewedOutputPresentation';
import {
  createReviewedRenderStatusPolling,
  isReviewedRenderInProgress,
} from '../utils/reviewedRenderPolling';
import { reviewedCorrectionWorkflowPresentation } from '../utils/reviewedOutputWorkflow';
import { workflowAllows } from '../utils/identityReviewWorkspace';
import { ReviewedIdentityAtTimePanel } from './ReviewedIdentityAtTimePanel';
import { ReviewedPlayerStatsTable } from './ReviewedPlayerStatsTable';

type Props = {
  matchId: string;
  workflow: ReviewWorkflow;
  onWorkflowChanged: (workflow: ReviewWorkflow) => void;
  onWorkflowRefresh: () => Promise<void>;
};

export function ReviewedVideoQaPanel({
  matchId,
  workflow,
  onWorkflowChanged,
  onWorkflowRefresh,
}: Props) {
  const [identity, setIdentity] = useState<ReviewedIdentityDocument | null>(null);
  const [job, setJob] = useState<ReviewedOutputJob | null>(workflow.processing || null);
  const [stats, setStats] = useState<ReviewedStatsResponse | null>(null);
  const [atTime, setAtTime] = useState<ReviewedIdentityAt | null>(null);
  const [currentVideoTime, setCurrentVideoTime] = useState(0);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  async function refreshOutput() {
    try {
      const [nextIdentity, nextJob] = await Promise.all([
        getReviewedIdentity(matchId),
        getReviewedOutputStatus(matchId),
      ]);
      setIdentity(nextIdentity);
      setJob(nextJob);
      if (nextJob.status === 'completed' && nextIdentity.status !== 'stale') {
        setStats(await getReviewedStats(matchId));
      } else {
        setStats(null);
      }
    } catch (error) {
      setMessage(errorMessage(error));
    }
  }

  useEffect(() => { void refreshOutput(); }, [matchId]);
  useEffect(() => {
    setJob(workflow.processing || null);
  }, [workflow.processing]);
  useEffect(() => {
    if (!isReviewedRenderInProgress(job?.status)) return undefined;
    const polling = createReviewedRenderStatusPolling({
      loadStatus: () => getReviewedOutputStatus(matchId),
      onStatus: setJob,
      onTerminalStatus: () => {
        void refreshOutput();
        void onWorkflowRefresh();
      },
      onError: (error) => setMessage(errorMessage(error)),
    });
    polling.start();
    return polling.stop;
  }, [job?.status, matchId, onWorkflowRefresh]);

  async function inspectCurrentTime() {
    if (!videoRef.current) return;
    const time = videoRef.current.currentTime;
    setCurrentVideoTime(time);
    try {
      setAtTime(await getReviewedIdentityAt(matchId, time));
    } catch (error) {
      setMessage(errorMessage(error));
    }
  }

  function correctionSaved(result: ReviewedCorrectionResponse) {
    setStats(null);
    setAtTime(null);
    const presentation = reviewedCorrectionWorkflowPresentation(result);
    if (presentation.queuedRenderJob) setJob(presentation.queuedRenderJob);
    if (result.workflow) onWorkflowChanged(result.workflow);
    setMessage(
      presentation.mode === 'automatic_rerender'
        ? 'Zapisano poprawkę. Przygotowuję zaktualizowane wideo automatycznie.'
        : presentation.mode === 'exceptions'
          ? 'Zapisano poprawkę. Ten przypadek wymaga jeszcze decyzji przed ponownym wideo.'
          : 'Zapisano poprawkę.',
    );
  }

  async function approve() {
    setBusy(true);
    setMessage('Zatwierdzam sprawdzenie wideo…');
    try {
      onWorkflowChanged(await approveReviewVideoQa(matchId));
      setMessage('Wideo zostało zatwierdzone.');
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const hasVideo = job?.status === 'completed' && Boolean(job.video_digest);
  const renderInProgress = isReviewedRenderInProgress(job?.status);
  const approvalAvailable = workflowAllows(workflow, 'approve_video_qa');
  const alreadyApproved = workflow.phase === 'complete';

  return <section className='reviewed-output-panel reviewed-video-qa-panel'>
    <div className='reviewed-output-heading'>
      <div>
        <p className='eyebrow'>Krok 4</p>
        <h2>Sprawdź wideo</h2>
        <p>{alreadyApproved
          ? 'To wideo jest już zatwierdzone. Jeżeli zauważysz błąd, możesz nadal poprawić przypisanie.'
          : 'Zatrzymaj nagranie na błędnej etykiecie i popraw osobę, jeśli to potrzebne. Po poprawce wideo odświeży się automatycznie.'}</p>
      </div>
      {identity && <span className='reviewed-status-badge'>{identity.status === 'complete_reviewed' ? 'Tożsamości gotowe' : 'Wideo do sprawdzenia'}</span>}
    </div>

    {renderInProgress && <div className='reviewed-rendering-card' role='status'>
      <span className='spinner' aria-hidden='true' />
      <div><h3>Przygotowuję zaktualizowane wideo…</h3><p>Nie uruchamiam ponownie analizy ani trackingu.</p></div>
    </div>}

    {hasVideo && <section className='reviewed-video-section'>
      <video
        key={job?.video_digest}
        ref={videoRef}
        className='reviewed-video'
        controls
        src={reviewedVideoUrl(matchId, job?.video_digest ?? '')}
        onPause={(event) => setCurrentVideoTime(event.currentTarget.currentTime)}
        onSeeked={(event) => setCurrentVideoTime(event.currentTarget.currentTime)}
      />
      <button type='button' className='reviewed-inspect-button' onClick={() => void inspectCurrentTime()} disabled={busy || renderInProgress}>
        Sprawdź osoby w klatce {formatReviewTime(currentVideoTime)}
      </button>
      {atTime && <ReviewedIdentityAtTimePanel
        matchId={matchId}
        document={atTime}
        onCorrectionSaved={(_entity, result) => correctionSaved(result)}
      />}
      <details className='reviewed-stats-details'>
        <summary>Statystyki po review</summary>
        {stats ? <ReviewedPlayerStatsTable document={stats} /> : <p>Statystyki zostaną pokazane po zakończeniu odświeżania.</p>}
      </details>
    </section>}

    {approvalAvailable && <div className='reviewed-next-step'>
      <div><h3>Wideo wygląda poprawnie?</h3><p>Zatwierdź je, aby odblokować raport i publikację.</p></div>
      <button type='button' onClick={() => void approve()} disabled={busy || renderInProgress}>Zatwierdź Video QA</button>
    </div>}
    {message && <p className='status'>{message}</p>}
  </section>;
}
