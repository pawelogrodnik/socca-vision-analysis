import { useEffect, useRef, useState } from 'react';

import {
  finalizeReviewWorkflow,
  getReviewWorkflow,
  getReviewedIdentityReviewProgress,
  retryReviewRecompute,
  retryReviewRender,
  getReviewedOutputStatus,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type { Match, ReviewedIdentityOptionalAudit, ReviewedOutputJob, ReviewWorkflow } from '../types';
import {
  identityReviewProgress,
  identityReviewStage,
  initialMandatoryQueue,
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
import { ReviewedIdentityMaxSummary } from './ReviewedIdentityMaxSummary';
import {
  ReviewedIdentityQueueTabs,
  type ReviewedIdentityMandatoryQueue,
} from './ReviewedIdentityQueueTabs';
import { matchTeamName } from '../utils/identityExceptionTeamFilter';
import type { TeamReviewFilter } from '../utils/identityExceptionTeamFilter';
import { formatReviewedIdentityPercent } from '../utils/reviewedIdentityMaxPresentation';

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
  const [liveOptionalAuditSummary, setLiveOptionalAuditSummary] = useState<ReviewedIdentityOptionalAudit | null>(null);
  const [optionalSummaryRefreshError, setOptionalSummaryRefreshError] = useState(false);
  const [optionalSummaryRefreshAttempt, setOptionalSummaryRefreshAttempt] = useState(0);
  const [activeMandatoryQueue, setActiveMandatoryQueue] = useState<ReviewedIdentityMandatoryQueue>(
    () => initialMandatoryQueue(initialWorkflow),
  );
  const [mixedFocusCaseId, setMixedFocusCaseId] = useState<string | null>(null);
  const [mixedEntryMode, setMixedEntryMode] = useState<'manual' | 'resolve_now'>('manual');
  const [requiredTeamFilter, setRequiredTeamFilter] = useState<TeamReviewFilter>('all');
  const mixedLeaveGuardRef = useRef<() => boolean>(() => true);

  function applyWorkflow(next: ReviewWorkflow) {
    setWorkflow(next);
    setActiveMandatoryQueue((current) => (
      current === 'required' && initialMandatoryQueue(next) === 'mixed'
        ? 'mixed'
        : current
    ));
    setLiveOptionalAuditSummary(null);
    setOptionalSummaryRefreshError(false);
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
    setLiveOptionalAuditSummary(null);
    setOptionalSummaryRefreshError(false);
    setOptionalSummaryRefreshAttempt(0);
    setShowOptionalAudit(false);
    setShowOptionalFinishConfirmation(false);
    setActiveMandatoryQueue(initialMandatoryQueue(initialWorkflow));
    setMixedFocusCaseId(null);
    setMixedEntryMode('manual');
    setRequiredTeamFilter('all');
    setMessage('');
    void refreshWorkflow();
    // The persisted match ID determines the workflow session. The callback is stable at the call site.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id]);

  useEffect(() => {
    if (workflow?.phase !== 'complete') setShowApprovedVideo(false);
  }, [workflow?.phase]);

  const stage = identityReviewStage(workflow);
  const mandatoryReviewActive = stage === 'remaining_issues' || stage === 'mixed_players';

  useEffect(() => {
    if (!mandatoryReviewActive) setMixedFocusCaseId(null);
  }, [mandatoryReviewActive]);
  const optionalSummaryFingerprint = workflow?.issues.optional_audit_summary
    ? [
      workflow.issues.optional_audit_summary.current_named_observations,
      workflow.issues.optional_audit_summary.pending_named_gain,
      workflow.issues.optional_audit_summary.remaining_cases,
      workflow.issues.optional_audit_summary.policy_version,
    ].join(':')
    : '';

  useEffect(() => {
    if (stage !== 'prepare_result' || !workflow?.issues.optional_audit_summary) return undefined;
    let disposed = false;
    void getReviewedIdentityReviewProgress(match.id, 0, 1, undefined, 'optional_audit')
      .then((progress) => {
        if (disposed) return;
        if (progress.optional_audit) setLiveOptionalAuditSummary(progress.optional_audit);
        setOptionalSummaryRefreshError(false);
      })
      .catch(() => {
        if (!disposed) setOptionalSummaryRefreshError(true);
      });
    return () => { disposed = true; };
  }, [match.id, optionalSummaryFingerprint, optionalSummaryRefreshAttempt, stage, workflow?.issues.optional_audit_summary]);

  function retryOptionalSummaryRefresh() {
    setOptionalSummaryRefreshError(false);
    setOptionalSummaryRefreshAttempt((attempt) => attempt + 1);
  }

  useEffect(() => {
    if (!mandatoryReviewActive) return undefined;
    const className = activeMandatoryQueue === 'mixed'
      ? 'identity-mixed-workspace-active'
      : 'identity-exception-workspace-active';
    document.body.classList.add(className);
    return () => document.body.classList.remove(className);
  }, [activeMandatoryQueue, mandatoryReviewActive]);

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
    const optional = liveOptionalAuditSummary || workflow?.issues.optional_audit_summary;
    const remaining = optional?.remaining_cases ?? 0;
    if (showOptionalAudit && remaining > 0) {
      setShowOptionalFinishConfirmation(true);
      return;
    }
    void finalize();
  }

  const optionalAudit = liveOptionalAuditSummary || workflow?.issues.optional_audit_summary;
  const teamAName = matchTeamName(match.teams || [], 'A');
  const acceptedTeamAttributionResidual = workflow?.issues.coverage_readiness
    ?.team_attribution_residual?.status === 'accepted_within_tolerance'
    ? workflow.issues.coverage_readiness.team_attribution_residual
    : null;
  const terminalDataQualityBlocker = Boolean(
    workflow
    && stage === 'error'
    && workflow.mandatory_operator_review_complete
    && workflow.issues.coverage_readiness_blocked,
  );

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
      <div>{terminalDataQualityBlocker ? <>
        <strong>Wymagany Review zakończony</strong>
        <span>{reviewWorkflowErrorMessage(workflow)} Nie ma już kolejnych bezpiecznych decyzji manualnych.</span>
        <details><summary>Diagnostyka jakości danych</summary>
          <span>Pozostałe nierozstrzygnięte obserwacje są zachowane jako nieznane i wyłączone z niepewnych statystyk.</span>
        </details>
      </> : <>
        <strong>{reviewWorkflowErrorMessage(workflow)}</strong>
        <span>Możesz bezpiecznie ponowić tylko wymagane odświeżenie.</span>
      </>}</div>
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

    {mandatoryReviewActive && workflow && <section className='identity-review-parallel-workspace'>
      <ReviewedIdentityQueueTabs
        workflow={workflow}
        activeQueue={activeMandatoryQueue}
        onSelect={(queue) => {
          if (queue === 'required' && activeMandatoryQueue === 'mixed' && !mixedLeaveGuardRef.current()) return;
          setMixedFocusCaseId(null);
          setMixedEntryMode('manual');
          setActiveMandatoryQueue(queue);
        }}
      />
      {activeMandatoryQueue === 'required' && <IdentityExceptionReviewPanel
        match={match}
        workflow={workflow}
        showPrimaryQueueSwitch={false}
        onMixedResolveNow={(caseId) => {
          setMixedFocusCaseId(caseId);
          setMixedEntryMode('resolve_now');
          setActiveMandatoryQueue('mixed');
        }}
        requiredTeamFilter={requiredTeamFilter}
        onRequiredTeamFilterChange={setRequiredTeamFilter}
        onWorkflowChanged={(next) => {
          if (next) applyWorkflow(next);
          else void refreshWorkflow();
        }}
        onRetryReview={workflowAllows(workflow, 'retry_review_recompute')
          ? () => retry('retry_review_recompute')
          : undefined}
      />}
      {activeMandatoryQueue === 'mixed' && <MixedPlayersReviewPanel
        match={match}
        workflow={workflow}
        focusCaseId={mixedFocusCaseId}
        entryMode={mixedEntryMode}
        onLeaveGuard={(guard) => { mixedLeaveGuardRef.current = guard; }}
        onReturnToRequired={() => {
          setMixedFocusCaseId(null);
          setMixedEntryMode('manual');
          setActiveMandatoryQueue('required');
        }}
        onResolveNowComplete={() => {
          setMixedFocusCaseId(null);
          setMixedEntryMode('manual');
        }}
        onWorkflowChanged={applyWorkflow}
      />}
    </section>}

    {stage === 'prepare_result' && workflow && !showOptionalAudit && <section className='reviewed-next-step'>
      <div>
        <p className='eyebrow'>Krok 3</p>
        <h2>Wymagany przegląd zakończony</h2>
        <p>{teamAName}</p>
        {optionalAudit && <ReviewedIdentityMaxSummary teamName={teamAName} summary={optionalAudit} />}
        {optionalSummaryRefreshError && <p className='identity-optional-summary-refresh-error' role='status'>
          Nie udało się odświeżyć podsumowania MAX. <button type='button' className='link-button' onClick={retryOptionalSummaryRefresh}>Spróbuj ponownie</button>
        </p>}
        {optionalAudit?.status === 'available' && <p>Wymagany poziom jakości został osiągnięty. Możesz zakończyć Review teraz albo opcjonalnie zwiększyć dokładność indywidualnych statystyk.</p>}
        {optionalAudit?.status === 'safe_max_reached' && <p>✓ Bezpieczne maksimum osiągnięte. Nie ma więcej obserwacji, które można bezpiecznie przypisać przy obecnym materiale.</p>}
        {acceptedTeamAttributionResidual && <p className='reviewed-residual-diagnostic'>
          {acceptedTeamAttributionResidual.observations} obserwacji pozostało bez bezpiecznego przypisania drużyny. Mieszczą się w limicie jakości ({acceptedTeamAttributionResidual.residual_budget_observations}), pozostają oznaczone jako nieznane i nie trafiają do statystyk wymagających pewnej drużyny lub zawodnika.
        </p>}
      </div>
      {optionalAudit?.status === 'available' && <button type='button' onClick={() => setShowOptionalAudit(true)}>
        Kontynuuj do MAX
      </button>}
      <button type='button' className='secondary' onClick={requestFinalize} disabled={busy || !workflowAllows(workflow, 'finalize_identity')}>Zakończ przegląd — Przygotuj wideo do sprawdzenia</button>
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
          <h2>Pełny audyt tożsamości — {teamAName}</h2>
          <p>Sprawdzaj wyłącznie bezpieczne, pozostałe fragmenty. Nie musisz dojść do 100% i możesz zakończyć Review w każdej chwili.</p>
          {optionalAudit && <ReviewedIdentityMaxSummary teamName={teamAName} summary={optionalAudit} compact />}
          {optionalSummaryRefreshError && <p className='identity-optional-summary-refresh-error' role='status'>
            Nie udało się odświeżyć podsumowania MAX. <button type='button' className='link-button' onClick={retryOptionalSummaryRefresh}>Spróbuj ponownie</button>
          </p>}
        </div>
        <div className='row'>
          <button type='button' className='secondary' onClick={() => {
            setShowOptionalFinishConfirmation(false);
            setShowOptionalAudit(false);
          }}>Wróć</button>
          <button type='button' onClick={requestFinalize} disabled={busy || !workflowAllows(workflow, 'finalize_identity')}>Zakończ przegląd</button>
        </div>
      </section>
      {showOptionalFinishConfirmation && <div className='status identity-optional-finish-confirmation' role='alert'>
        <strong>Pozostało {optionalAudit?.remaining_cases ?? 0} opcjonalnych przypadków.</strong>
        <p>Wymagane minimum oraz wszystkie obowiązkowe kontrole są zakończone. Możesz zakończyć teraz; pozostałe fragmenty pozostaną anonimowe w tym raporcie.</p>
        <div className='row'>
          <button type='button' className='secondary' onClick={() => setShowOptionalFinishConfirmation(false)}>Kontynuuj audyt</button>
          <button type='button' onClick={() => { setShowOptionalFinishConfirmation(false); void finalize(); }} disabled={busy}>Zakończ mimo to</button>
        </div>
      </div>}
      {optionalAudit?.status === 'safe_max_reached' ? <section className='status' aria-live='polite'>
        <strong>✓ Bezpieczne maksimum osiągnięte</strong>
        <p>{teamAName}: pokrycie po zapisanych decyzjach wynosi {formatReviewedIdentityPercent(optionalAudit.projected_named_coverage)}. Pozostałe bezpieczne przypadki: 0.</p>
      </section> : <IdentityExceptionReviewPanel
        match={match}
        workflow={workflow}
        initialQueue='optional_audit'
        onOptionalAuditSummaryChanged={setLiveOptionalAuditSummary}
        onWorkflowChanged={(next) => {
          if (next) applyWorkflow(next);
          else void refreshWorkflow();
        }}
      />}
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
