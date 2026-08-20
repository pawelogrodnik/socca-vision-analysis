import { useEffect, useState } from 'react';

import {
  finalizeReviewWorkflow,
  getReviewWorkflow,
  retryReviewRecompute,
  retryReviewRender,
  getReviewedOutputStatus,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type { Match, ReviewedOutputJob, ReviewWorkflow } from '../types';
import {
  identityReviewProgress,
  identityReviewStage,
  reviewWorkflowErrorMessage,
  workflowAllows,
} from '../utils/identityReviewWorkspace';
import {
  createReviewedRenderStatusPolling,
  isReviewedRenderInProgress,
} from '../utils/reviewedRenderPolling';
import { IdentityExceptionReviewPanel } from './IdentityExceptionReviewPanel';
import { IdentityReviewScopeSummary } from './IdentityReviewScopeSummary';
import { MixedPlayersReviewPanel } from './MixedPlayersReviewPanel';
import { InitialIdentityAuditPanel } from './InitialIdentityAuditPanel';
import { ReviewedVideoQaPanel } from './ReviewedVideoQaPanel';

type Props = {
  match: Match;
  initialWorkflow: ReviewWorkflow | null;
  onWorkflowChanged: (workflow: ReviewWorkflow) => void;
  onOpenReport: () => void;
};

type VideoSettings = {
  include_minimap: boolean;
  include_ball: boolean;
  show_roster_number: boolean;
};

const defaultVideoSettings: VideoSettings = {
  include_minimap: true,
  include_ball: true,
  show_roster_number: false,
};

export function IdentityReviewWorkspace({
  match,
  initialWorkflow,
  onWorkflowChanged,
  onOpenReport,
}: Props) {
  const [workflow, setWorkflow] = useState<ReviewWorkflow | null>(initialWorkflow);
  const [processingJob, setProcessingJob] = useState<ReviewedOutputJob | null>(initialWorkflow?.processing || null);
  const [videoSettings, setVideoSettings] = useState<VideoSettings>(defaultVideoSettings);
  const [showApprovedVideo, setShowApprovedVideo] = useState(false);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [showOptionalAudit, setShowOptionalAudit] = useState(false);
  const [showOptionalFinishConfirmation, setShowOptionalFinishConfirmation] = useState(false);
  const [optionalAuditRemaining, setOptionalAuditRemaining] = useState<number | null>(null);

  function applyWorkflow(next: ReviewWorkflow) {
    setWorkflow(next);
    setProcessingJob(next.processing || null);
    onWorkflowChanged(next);
  }

  async function refreshWorkflow() {
    try {
      applyWorkflow(await getReviewWorkflow(match.id));
    } catch (error) {
      setMessage(errorMessage(error));
    }
  }

  useEffect(() => {
    setWorkflow(initialWorkflow);
    setProcessingJob(initialWorkflow?.processing || null);
    setMessage('');
    void refreshWorkflow();
    // The persisted match ID determines the workflow session. The callback is stable at the call site.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id]);

  useEffect(() => {
    if (workflow?.phase !== 'complete') setShowApprovedVideo(false);
  }, [workflow?.phase]);

  const stage = identityReviewStage(workflow);
  useEffect(() => {
    if (!['remaining_issues', 'mixed_players'].includes(stage)) return undefined;
    document.body.classList.add('identity-exception-workspace-active');
    return () => document.body.classList.remove('identity-exception-workspace-active');
  }, [stage]);

  useEffect(() => {
    if (stage !== 'rendering' || !isReviewedRenderInProgress(processingJob?.status)) return undefined;
    const polling = createReviewedRenderStatusPolling({
      loadStatus: () => getReviewedOutputStatus(match.id),
      onStatus: setProcessingJob,
      onTerminalStatus: () => { void refreshWorkflow(); },
      onError: (error) => setMessage(errorMessage(error)),
    });
    polling.start();
    return polling.stop;
    // refreshWorkflow intentionally works with the current match session only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id, processingJob?.status, stage]);

  async function finalize() {
    if (!workflowAllows(workflow, 'finalize_identity')) return;
    setBusy(true);
    setMessage('Przygotowuję wideo do sprawdzenia…');
    try {
      applyWorkflow(await finalizeReviewWorkflow(match.id, videoSettings));
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  function requestFinalize() {
    const optional = workflow?.issues.optional_audit_summary;
    const remaining = optionalAuditRemaining ?? optional?.remaining_cases ?? 0;
    if (showOptionalAudit && remaining > 0) {
      setShowOptionalFinishConfirmation(true);
      return;
    }
    void finalize();
  }

  const optionalAudit = workflow?.issues.optional_audit_summary;
  const optionalRemaining = optionalAuditRemaining ?? optionalAudit?.remaining_cases ?? 0;

  async function retry(action: 'retry_render' | 'retry_review_recompute') {
    if (!workflowAllows(workflow, action)) return;
    setBusy(true);
    try {
      applyWorkflow(action === 'retry_render'
        ? await retryReviewRender(match.id)
        : await retryReviewRecompute(match.id));
      setMessage('Odświeżanie zostało uruchomione ponownie.');
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return <section className={`identity-review-workspace${['remaining_issues', 'mixed_players'].includes(stage) ? ' remaining-issues-active' : ''}`}>
    <div className='identity-review-workspace-chrome'>
      <IdentityReviewScopeSummary teams={match.teams || []} scope={match.identity_review_scope} />
      <header className='identity-review-workspace-heading'>
        <div>
          <p className='eyebrow'>Krok 3</p>
          <h1>Review zawodników</h1>
          <p>Rozpoznaj osoby, rozwiąż tylko pozostałe ważne przypadki, a na końcu sprawdź gotowe wideo.</p>
        </div>
      </header>

      <ol className='identity-review-progress' aria-label='Postęp review zawodników'>
        {identityReviewProgress(workflow).map((item) => <li key={item.id} className={item.status}>
          <span aria-hidden='true'>{item.status === 'completed' ? '✓' : item.status === 'current' || item.status === 'processing' ? '→' : '○'}</span>
          {item.label}
        </li>)}
      </ol>
    </div>

    {!workflow && <p className='loading-line'><span className='spinner' /> Ładuję status review…</p>}
    {stage === 'unavailable' && workflow && <div className='status'>Review będzie dostępny po zakończeniu analizy meczu.</div>}
    {stage === 'error' && workflow && <div className='reviewed-stale-banner'>
      <div><strong>{reviewWorkflowErrorMessage(workflow)}</strong><span>Możesz bezpiecznie ponowić tylko wymagane odświeżenie.</span></div>
      {workflowAllows(workflow, 'retry_review_recompute') && <button type='button' onClick={() => void retry('retry_review_recompute')} disabled={busy}>Spróbuj ponownie</button>}
      {workflowAllows(workflow, 'retry_render') && <button type='button' onClick={() => void retry('retry_render')} disabled={busy}>Spróbuj ponownie</button>}
    </div>}

    {stage === 'identify_players' && workflow && <section className='identity-review-current-stage'>
      <div className='identity-review-stage-copy'>
        <p className='eyebrow'>Krok 1</p>
        <h2>Rozpoznaj zawodników</h2>
        <p>Sprawdź kilka wybranych klatek i przypisz osoby, które rozpoznajesz. Nie musisz oznaczać każdego fragmentu filmu.</p>
        <strong>{workflow.steps.find((step) => step.id === 'initial_audit')?.completed ?? 0} / {workflow.steps.find((step) => step.id === 'initial_audit')?.total ?? 0} potwierdzonych</strong>
      </div>
      <InitialIdentityAuditPanel
        match={match}
        onStatus={setMessage}
        onWorkflowChanged={applyWorkflow}
      />
    </section>}

    {stage === 'remaining_issues' && workflow && <IdentityExceptionReviewPanel
      match={match}
      workflow={workflow}
      onWorkflowChanged={(next) => {
        if (next) applyWorkflow(next);
        else void refreshWorkflow();
      }}
      onRetryReview={workflowAllows(workflow, 'retry_review_recompute')
        ? () => retry('retry_review_recompute')
        : undefined}
    />}

    {stage === 'mixed_players' && workflow && <MixedPlayersReviewPanel
      match={match}
      workflow={workflow}
      onWorkflowChanged={applyWorkflow}
    />}

    {stage === 'prepare_result' && workflow && !showOptionalAudit && <section className='reviewed-next-step'>
      <div>
        <p className='eyebrow'>Krok 3</p>
        <h2>Tożsamości są gotowe</h2>
        <p>Możesz zakończyć Review albo dobrowolnie podnieść imienne pokrycie Corgi do bezpiecznego maksimum.</p>
        {optionalAudit?.status === 'available' && <p>
          Corgi: {Math.round((optionalAudit.current_named_coverage || 0) * 100)}% teraz → {Math.round((optionalAudit.projected_named_coverage || 0) * 100)}% po zapisanych decyzjach → maksymalnie {Math.round((optionalAudit.safe_max_named_coverage || 0) * 100)}% przy obecnych bezpiecznych dowodach.
        </p>}
        {optionalAudit?.status === 'safe_max_reached' && <p>
          Bezpieczne maksimum Corgi zostało osiągnięte: {Math.round((optionalAudit.safe_max_named_coverage || 0) * 100)}% imiennie.
        </p>}
      </div>
      <button type='button' onClick={requestFinalize} disabled={busy || !workflowAllows(workflow, 'finalize_identity')}>Zakończ przegląd — Przygotuj wideo do sprawdzenia</button>
      {optionalAudit?.status === 'available' && <button type='button' className='secondary' onClick={() => { setOptionalAuditRemaining(null); setShowOptionalAudit(true); }}>
        Kontynuuj do MAX ({optionalRemaining})
      </button>}
      <details className='reviewed-video-settings'>
        <summary>Ustawienia wideo</summary>
        <div className='reviewed-checkboxes'>
          <label className='inline-check'><input type='checkbox' checked={videoSettings.include_minimap} onChange={(event) => setVideoSettings((current) => ({ ...current, include_minimap: event.target.checked }))} /> Pokaż minimapę</label>
          <label className='inline-check'><input type='checkbox' checked={videoSettings.include_ball} onChange={(event) => setVideoSettings((current) => ({ ...current, include_ball: event.target.checked }))} /> Pokaż piłkę</label>
          <label className='inline-check'><input type='checkbox' checked={videoSettings.show_roster_number} onChange={(event) => setVideoSettings((current) => ({ ...current, show_roster_number: event.target.checked }))} /> Pokaż numer zawodnika przy imieniu</label>
        </div>
      </details>
    </section>}

    {stage === 'prepare_result' && workflow && showOptionalAudit && <>
      <section className='reviewed-next-step identity-optional-audit-summary'>
        <div>
          <p className='eyebrow'>Dobrowolny audyt</p>
          <h2>Pełny audyt tożsamości — Corgi</h2>
          <p>Sprawdzaj wyłącznie bezpieczne, pozostałe fragmenty. Nie musisz dojść do 100% i możesz zakończyć Review w każdej chwili.</p>
          <p>Imiennie: {Math.round((optionalAudit?.current_named_coverage || 0) * 100)}% teraz → {Math.round((optionalAudit?.projected_named_coverage || 0) * 100)}% po zapisanych decyzjach → bezpieczne maksimum: {Math.round((optionalAudit?.safe_max_named_coverage || 0) * 100)}%.</p>
        </div>
        <div className='row'>
          <button type='button' className='secondary' onClick={() => { setOptionalAuditRemaining(null); setShowOptionalAudit(false); void refreshWorkflow(); }}>Wróć</button>
          <button type='button' onClick={requestFinalize} disabled={busy || !workflowAllows(workflow, 'finalize_identity')}>Zakończ przegląd</button>
        </div>
      </section>
      {showOptionalFinishConfirmation && <div className='status identity-optional-finish-confirmation' role='alert'>
        <strong>Pozostało {optionalRemaining} dobrowolnych przypadków.</strong>
        <p>Możesz zakończyć bez ich rozstrzygania — nie będą blokować raportu.</p>
        <div className='row'>
          <button type='button' className='secondary' onClick={() => setShowOptionalFinishConfirmation(false)}>Kontynuuj audyt</button>
          <button type='button' onClick={() => { setShowOptionalFinishConfirmation(false); void finalize(); }} disabled={busy}>Zakończ mimo to</button>
        </div>
      </div>}
      <IdentityExceptionReviewPanel
        match={match}
        workflow={workflow}
        initialQueue='optional_audit'
        onOptionalAuditRemainingChanged={setOptionalAuditRemaining}
        onWorkflowChanged={(next) => {
          if (next) applyWorkflow(next);
          else void refreshWorkflow();
        }}
      />
    </>}

    {stage === 'rendering' && <div className='reviewed-rendering-card' role='status'>
      <span className='spinner' aria-hidden='true' />
      <div><h2>Przygotowuję wideo do sprawdzenia…</h2><p>Render wykorzystuje obecny wynik review; analiza wideo nie jest uruchamiana ponownie.</p></div>
    </div>}

    {(stage === 'video_qa' || (stage === 'complete' && showApprovedVideo)) && workflow && <ReviewedVideoQaPanel
      matchId={match.id}
      workflow={workflow}
      onWorkflowChanged={applyWorkflow}
      onWorkflowRefresh={refreshWorkflow}
    />}

    {stage === 'complete' && workflow && <div className='reviewed-next-step identity-review-complete'>
      <div><h2>Review zakończony ✓</h2><p>Wideo jest zatwierdzone. Możesz je nadal otworzyć i poprawić przypisanie, jeśli zauważysz błąd.</p></div>
      <div className='row'>
        <button type='button' onClick={onOpenReport}>Przejdź do raportu</button>
        <button type='button' className='secondary' onClick={() => setShowApprovedVideo(true)}>Sprawdź wideo ponownie</button>
      </div>
    </div>}

    {message && <p className='status'>{message}</p>}
  </section>;
}
