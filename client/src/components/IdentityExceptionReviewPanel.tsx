import { useEffect, useMemo, useRef, useState } from 'react';

import {
  artifactUrl,
  finalizeReviewedIdentityCorrections,
  getIdentityRosterSubjectReview,
  getReviewedIdentityReviewProgress,
  isRequestAbortError,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type {
  IdentityRosterSubjectReviewCard,
  Match,
  ReviewedCorrectionResponse,
  ReviewedIdentityAtEntity,
  ReviewedIdentityCoverage,
  ReviewedIdentityCoverageDebt,
  ReviewedIdentityCoverageReadiness,
  ReviewedIdentityReviewFilters,
  ReviewedIdentityReviewQueue,
  ReviewedIdentityReviewUnit,
  ReviewedIdentityOptionalAudit,
  ReviewedIdentityWorkload,
  ReviewWorkflow,
} from '../types';
import { hasOperatorReviewableVisualEvidence } from '../utils/identityReviewWorkspace';
import {
  REQUIRED_REVIEW_WORKING_WINDOW_SIZE,
  beginRequiredReviewNavigation,
  beginRequiredReviewLifecycle,
  finalizeDeferredReviewBatch,
  recordRequiredReviewQueueMutation,
  recordDurableRequiredReviewSave,
  removeResolvedReviewCase,
  resolveRequiredReviewPageRequest,
  resolveReviewPageNavigation,
  reviewUnitKey,
  shouldRecoverRequiredReviewCompletion,
  shouldVerifyMutatedRequiredQueueEmpty,
} from '../utils/identityExceptionQueue';
import {
  createReviewCommitGuard,
  createReviewQueueConflictRecovery,
  moveReviewCaseIndex,
} from '../utils/identityExceptionWorkspace';
import {
  apiTeamFilter,
  matchTeamName,
  teamReviewFilterOptions,
  type TeamReviewFilter,
} from '../utils/identityExceptionTeamFilter';
import { requiredCasesLabel } from '../utils/reviewWorkflowPresentation';
import {
  reviewRecomputeMessage,
  teamAttributionBlockerMessage,
} from '../utils/reviewedIdentityBlockerPresentation';
import { formatReviewTime } from '../utils/reviewedOutputPresentation';
import {
  formatReviewedIdentityPercent,
  formatReviewedIdentityPercentagePoints,
} from '../utils/reviewedIdentityMaxPresentation';
import { ReviewedIdentityCorrectionForm } from './ReviewedIdentityCorrectionForm';
import { ReviewedIdentityCoverageDebtDialog } from './ReviewedIdentityCoverageDebtDialog';
import { prefetchReviewedCorrectionContext } from '../utils/reviewedCorrectionContextClientCache';

type Props = {
  match: Match;
  workflow: ReviewWorkflow;
  onWorkflowChanged: (workflow: ReviewWorkflow) => void;
  onCompletionSynchronizationChange?: (
    synchronizing: boolean,
    authoritativeWorkflow?: ReviewWorkflow,
  ) => void;
  onRetryReview?: () => Promise<void>;
  initialQueue?: ReviewedIdentityReviewQueue;
  onOptionalAuditSummaryChanged?: (summary: ReviewedIdentityOptionalAudit) => void;
  showPrimaryQueueSwitch?: boolean;
  onMixedResolveNow?: (caseId: string) => void;
  requiredTeamFilter?: TeamReviewFilter;
  onRequiredTeamFilterChange?: (teamFilter: TeamReviewFilter) => void;
};

type ReviewCase = {
  unit: ReviewedIdentityReviewUnit;
  card: IdentityRosterSubjectReviewCard | null;
};

function toCorrectionEntity(reviewCase: ReviewCase): ReviewedIdentityAtEntity {
  const { card, unit } = reviewCase;
  const evidence = unit.visual_evidence || card?.visual_evidence;
  const crop = evidence?.anchor_crops[0];
  const frameStart = unit.frame_start ?? card?.start_frame ?? crop?.frame ?? 0;
  const frameEnd = unit.frame_end ?? card?.end_frame ?? crop?.frame ?? frameStart;
  return {
    frame: crop?.frame ?? frameStart,
    time_sec: crop?.time_sec ?? 0,
    tracklet_id: crop?.tracklet_id || unit.tracklet_ids[0] || unit.candidate_subject_id,
    candidate_subject_id: unit.candidate_subject_id,
    candidate_subject_ids: [unit.candidate_subject_id],
    team_label: unit.source_team_label || card?.team_label || 'U',
    stable_anonymous_slot_id: null,
    canonical_player_id: null,
    player_name: null,
    display_label: '',
    identity_status: 'unresolved',
    identity_source: null,
    fallback_label: 'Nieznany zawodnik',
    requires_review: true,
    hard_blockers: [],
    conflicts: [],
    detected_evidence_count: unit.detected_observation_count || card?.detected_frames || 0,
    frame_start: frameStart,
    frame_end: frameEnd,
    review_target_id: unit.review_target_id,
    scope_kind: unit.scope_kind,
    source_ownership_digest: unit.source_ownership_digest,
  };
}

function reviewCaseTimeRange(reviewCase: ReviewCase): string | null {
  const evidence = reviewCase.unit.visual_evidence || reviewCase.card?.visual_evidence;
  const timedCrop = evidence?.anchor_crops.find((crop) => crop.time_sec && crop.frame);
  const frameStart = reviewCase.unit.frame_start ?? reviewCase.card?.start_frame;
  const frameEnd = reviewCase.unit.frame_end ?? reviewCase.card?.end_frame;
  if (!timedCrop?.time_sec || !timedCrop.frame || frameStart == null || frameEnd == null) return null;
  const fps = timedCrop.frame / timedCrop.time_sec;
  if (!Number.isFinite(fps) || fps <= 0) return null;
  return `${formatReviewTime(frameStart / fps)}–${formatReviewTime(frameEnd / fps)}`;
}

function cropQualityLabel(qualityClass?: string | null): string | null {
  if (!qualityClass) return null;
  const labels: Record<string, string> = {
    high: 'wysoka jakość',
    medium: 'średnia jakość',
    low: 'niska jakość',
  };
  return labels[qualityClass] || qualityClass.split('_').join(' ');
}

export function IdentityExceptionReviewPanel({
  match,
  workflow,
  onWorkflowChanged,
  onCompletionSynchronizationChange,
  onRetryReview,
  initialQueue = 'required',
  onOptionalAuditSummaryChanged,
  showPrimaryQueueSwitch = true,
  onMixedResolveNow,
  requiredTeamFilter = 'all',
  onRequiredTeamFilterChange,
}: Props) {
  const [coverageDetailsOpen, setCoverageDetailsOpen] = useState(false);
  const [cases, setCases] = useState<ReviewCase[]>([]);
  const [index, setIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [finalizing, setFinalizing] = useState(false);
  const [finalizeFailed, setFinalizeFailed] = useState(false);
  const [message, setMessage] = useState('');
  const [totalRemaining, setTotalRemaining] = useState(0);
  const [pageOffset, setPageOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [coverage, setCoverage] = useState<ReviewedIdentityCoverage | null>(null);
  const [coverageDebt, setCoverageDebt] = useState<ReviewedIdentityCoverageDebt | null>(null);
  const [coverageReadiness, setCoverageReadiness] = useState<ReviewedIdentityCoverageReadiness | null>(null);
  const [workload, setWorkload] = useState<ReviewedIdentityWorkload | null>(null);
  const [uncontrolledTeamFilter, setUncontrolledTeamFilter] = useState<TeamReviewFilter>('all');
  const activeTeamFilter = onRequiredTeamFilterChange ? requiredTeamFilter : uncontrolledTeamFilter;
  const [reviewFilters, setReviewFilters] = useState<ReviewedIdentityReviewFilters | null>(null);
  const [activeQueue, setActiveQueue] = useState<ReviewedIdentityReviewQueue>(initialQueue);
  const [optionalAuditRemaining, setOptionalAuditRemaining] = useState(0);
  const finalizeInFlight = useRef(false);
  const requiredReviewLifecycleRef = useRef(beginRequiredReviewLifecycle(0));
  const requiredReviewNavigationRef = useRef(beginRequiredReviewNavigation());
  const committedReviewKeysRef = useRef(createReviewCommitGuard());
  const loadRequestIdRef = useRef(0);
  const cardsBySubjectRef = useRef<Map<string, IdentityRosterSubjectReviewCard> | null>(null);

  async function loadCases(
    ignore?: () => boolean,
    preserveMessage = false,
    offset = 0,
    preferredIndex = 0,
    teamFilter: TeamReviewFilter = activeTeamFilter,
    queue: ReviewedIdentityReviewQueue = activeQueue,
    signal?: AbortSignal,
  ): Promise<ReviewCase[]> {
    const requestId = ++loadRequestIdRef.current;
    setLoading(true);
    if (!preserveMessage) setMessage('');
    try {
      const [document, progress] = await Promise.all([
        cardsBySubjectRef.current
          ? Promise.resolve(null)
          : getIdentityRosterSubjectReview(match.id, { signal }),
        getReviewedIdentityReviewProgress(
          match.id,
          offset,
          REQUIRED_REVIEW_WORKING_WINDOW_SIZE,
          apiTeamFilter(teamFilter),
          queue,
          { signal },
        ),
      ]);
      if (ignore?.() || requestId !== loadRequestIdRef.current) return [];
      // A completed progress request is authoritative. A unit can reappear
      // after a Review recompute and must not inherit an old duplicate-save
      // marker from the prior queue projection.
      committedReviewKeysRef.current.resetForAuthoritativeQueue();
      if (document) {
        cardsBySubjectRef.current = new Map(
          document.cards.map((nextCard) => [nextCard.candidate_subject_id, nextCard]),
        );
      }
      const cardsBySubject = cardsBySubjectRef.current || new Map();
      const actionable = progress.next_cases
        .filter((item) => ['high', 'coverage', 'continuity', 'optional'].includes(item.priority || ''))
        .map((unit) => ({
          unit,
          card: cardsBySubject.get(unit.candidate_subject_id) || null,
        }));
      setTotalRemaining(progress.pagination?.total_remaining ?? actionable.length);
      const knownGlobalRequired = progress.pagination?.global_total_remaining
        ?? progress.filters?.counts.all
        ?? actionable.length;
      if (queue === 'required') {
        requiredReviewLifecycleRef.current = beginRequiredReviewLifecycle(knownGlobalRequired);
        // This accepted hot-progress response is a new positional snapshot.
        requiredReviewNavigationRef.current = beginRequiredReviewNavigation();
      }
      setPageOffset(progress.pagination?.offset ?? offset);
      setHasMore(progress.pagination?.has_more ?? false);
      setCoverage(progress.identity_coverage || null);
      setCoverageDebt(progress.coverage_debt || null);
      setCoverageReadiness(progress.coverage_readiness || null);
      setWorkload(progress.workload || null);
      setReviewFilters(progress.filters || null);
      const nextOptionalRemaining = progress.optional_audit?.remaining_cases || 0;
      setOptionalAuditRemaining(nextOptionalRemaining);
      if (progress.optional_audit) onOptionalAuditSummaryChanged?.(progress.optional_audit);
      if (queue === 'required' && shouldRecoverRequiredReviewCompletion(
        progress.recompute_required,
        requiredReviewLifecycleRef.current.knownRemaining,
        progress.coverage_readiness?.allows_finalize !== false,
      )) {
        setCases([]);
        setIndex(0);
        void finalizeCorrections(teamFilter, queue);
        return [];
      }
      setCases(actionable);
      setIndex(actionable.length > 0
        ? Math.min(Math.max(0, preferredIndex), actionable.length - 1)
        : 0);
      return actionable;
    } catch (error) {
      if (isRequestAbortError(error)) return [];
      if (!ignore?.() && requestId === loadRequestIdRef.current) setMessage(errorMessage(error));
      return [];
    } finally {
      if (!ignore?.() && requestId === loadRequestIdRef.current) setLoading(false);
    }
  }

  useEffect(() => {
    let disposed = false;
    const controller = new AbortController();
    setActiveQueue(initialQueue);
    requiredReviewLifecycleRef.current = beginRequiredReviewLifecycle(0);
    requiredReviewNavigationRef.current = beginRequiredReviewNavigation();
    void loadCases(
      () => disposed,
      false,
      0,
      0,
      activeTeamFilter,
      initialQueue,
      controller.signal,
    );
    return () => {
      disposed = true;
      controller.abort();
    };
    // Cards are reloaded after a semantic decision, not for incidental workflow object updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQueue, match.id]);

  const reviewCase = cases[index] || null;
  const card = reviewCase?.card || null;
  const unitEvidence = reviewCase?.unit.visual_evidence;
  const evidence = unitEvidence || card?.visual_evidence || null;
  const entity = useMemo(
    () => (reviewCase ? toCorrectionEntity(reviewCase) : null),
    [reviewCase],
  );
  const hasVisualEvidence = Boolean(
    unitEvidence?.anchor_crops.length
    || (card && hasOperatorReviewableVisualEvidence(card)),
  );
  const caseTimeRange = reviewCase ? reviewCaseTimeRange(reviewCase) : null;
  const optionalMaxSlot = reviewCase?.unit.stable_slot_id || null;
  const optionalMaxDuration = reviewCase?.unit.detected_time_sec ?? null;
  const optionalMaxMarginalObservations = reviewCase?.unit.marginal_named_observation_gain ?? 0;
  const optionalMaxMarginalCoverage = reviewCase?.unit.optional_max_marginal_coverage_gain_pp ?? null;
  const filterOptions = useMemo(
    () => teamReviewFilterOptions(match.teams || [], reviewFilters),
    [match.teams, reviewFilters],
  );
  const activeTeamName = activeTeamFilter === 'all'
    ? 'Wszystkie'
    : activeTeamFilter === 'U'
      ? 'Drużyna / konflikt'
      : matchTeamName(match.teams || [], activeTeamFilter);
  const globalRemaining = activeQueue === 'required'
    ? requiredReviewLifecycleRef.current.knownRemaining
    : reviewFilters?.counts.all ?? totalRemaining;
  const coverageBlockedWithoutCases = globalRemaining === 0
    && coverageReadiness?.allows_finalize === false;
  const teamAttributionBlocker = teamAttributionBlockerMessage(coverageReadiness);
  const guidanceCount = Number(reviewCase?.unit.scope_kind === 'canonical_segment')
    + Number(reviewCase?.unit.scope_kind === 'material_continuity')
    + Number(activeQueue === 'optional_audit')
    + Number(reviewCase?.unit.priority === 'coverage')
    + Number(unitEvidence?.kind === 'team_attribution');

  useEffect(() => {
    const next = cases[index + 1]?.unit;
    if (next?.candidate_subject_id) {
      prefetchReviewedCorrectionContext(match.id, next.candidate_subject_id, next.review_target_id);
    }
  }, [cases, index, match.id]);

  function changeTeamFilter(nextFilter: TeamReviewFilter) {
    if (nextFilter === activeTeamFilter || loading || finalizing) return;
    if (onRequiredTeamFilterChange) onRequiredTeamFilterChange(nextFilter);
    else setUncontrolledTeamFilter(nextFilter);
    setCases([]);
    setIndex(0);
    setPageOffset(0);
    setTotalRemaining(0);
    setMessage('');
    requiredReviewLifecycleRef.current = beginRequiredReviewLifecycle(0);
    requiredReviewNavigationRef.current = beginRequiredReviewNavigation();
    void loadCases(undefined, false, 0, 0, nextFilter, activeQueue);
  }

  function changeQueue(nextQueue: ReviewedIdentityReviewQueue) {
    if (nextQueue === activeQueue || loading || finalizing) return;
    setActiveQueue(nextQueue);
    setCases([]);
    setIndex(0);
    setPageOffset(0);
    setTotalRemaining(0);
    setMessage('');
    requiredReviewLifecycleRef.current = beginRequiredReviewLifecycle(0);
    requiredReviewNavigationRef.current = beginRequiredReviewNavigation();
    void loadCases(undefined, false, 0, 0, 'all', nextQueue);
  }

  function moveToCase(nextIndex: number) {
    setIndex(moveReviewCaseIndex(nextIndex, cases.length));
    setMessage('');
  }

  function navigate(direction: 'previous' | 'next') {
    const destination = resolveReviewPageNavigation({
      direction,
      currentIndex: index,
      pageLength: cases.length,
      pageOffset,
      pageSize: REQUIRED_REVIEW_WORKING_WINDOW_SIZE,
      hasMore,
    });
    if (destination.kind === 'local') {
      moveToCase(destination.index);
    } else if (destination.kind === 'page') {
      const request = resolveRequiredReviewPageRequest(
        activeQueue,
        destination,
        requiredReviewNavigationRef.current,
      );
      void loadCases(
        undefined,
        true,
        request.offset,
        request.index,
        activeTeamFilter,
        activeQueue,
      );
    }
  }

  function saved(result: ReviewedCorrectionResponse) {
    if (result.coverage_debt) setCoverageDebt(result.coverage_debt);
    if (!reviewCase || !result.recompute_deferred) {
      if (result.workflow) onWorkflowChanged(result.workflow);
      return;
    }
    if (result.idempotent_replay) {
      // A request can arrive after a previous accepted save (for example from
      // an old card kept by a pre-policy cache). It is not another decision:
      // do not decrement, finalize, or locally advance the Required queue.
      recoverFromReviewSaveConflict();
      return;
    }
    const savedKey = reviewUnitKey(reviewCase.unit);
    if (!committedReviewKeysRef.current.markIfNew(savedKey)) return;
    if (activeQueue === 'required') {
      requiredReviewNavigationRef.current = recordRequiredReviewQueueMutation();
    }
    if (result.review_state_rebuild_required) {
      // Do not derive a new queue from stale local cards after a topology
      // change. The next GET performs one authoritative materialization.
      setFinalizeFailed(false);
      requiredReviewLifecycleRef.current = beginRequiredReviewLifecycle(0);
      setMessage('Zapisano decyzję. Odświeżam przypadki po zmianie struktury…');
      if (result.workflow) onWorkflowChanged(result.workflow);
      void loadCases(
        undefined,
        true,
        0,
        0,
        activeTeamFilter,
        activeQueue,
      );
      return;
    }
    const next = removeResolvedReviewCase(
      cases,
      index,
      reviewUnitKey(reviewCase.unit),
    );
    setCases(next.cases);
    setIndex(next.index);
    setFinalizeFailed(false);
    setMessage('Zapisano decyzję.');
    setTotalRemaining((remaining) => Math.max(0, remaining - 1));
    if (activeQueue === 'optional_audit') {
      if (result.workflow) onWorkflowChanged(result.workflow);
      // Coverage is never inferred in the client. The new queue and complete
      // MAX summary come from the read-only progress endpoint after every save.
      void loadCases(undefined, true, 0, 0, 'all', 'optional_audit');
    } else {
      const transition = recordDurableRequiredReviewSave(requiredReviewLifecycleRef.current);
      requiredReviewLifecycleRef.current = transition.lifecycle;
      if (transition.synchronization === 'completion') {
        // Canonical synchronization is an authoritative completion/recovery
        // operation, never periodic hot-queue batching.
        void finalizeCorrections(activeTeamFilter, activeQueue);
        return;
      }
      if (result.workflow) onWorkflowChanged(result.workflow);
      if (transition.synchronization === 'replenish') {
        // Required Review is a shrinking queue. Refresh its current head from
        // the valid hot projection; this does not call corrections/finalize.
        void loadCases(
          undefined,
          true,
          0,
          0,
          activeTeamFilter,
          activeQueue,
        );
      } else if (shouldVerifyMutatedRequiredQueueEmpty(
        activeQueue,
        next.cases.length,
        requiredReviewNavigationRef.current,
      )) {
        // `hasMore` belongs to the page before its durable mutations. Verify
        // an empty local page once against the current hot filtered queue.
        void loadCases(
          undefined,
          true,
          0,
          0,
          activeTeamFilter,
          activeQueue,
        );
      } else if (next.cases.length === 0 && hasMore) {
        // Offset 0 is the head of the *current* shrinking queue. All local
        // entries were just persisted, so this cannot skip its next sources.
        void loadCases(
          undefined,
          true,
          0,
          0,
          activeTeamFilter,
          activeQueue,
        );
      }
    }
  }

  function recoverFromReviewSaveConflict() {
    const recovery = createReviewQueueConflictRecovery(
      activeTeamFilter,
      activeQueue,
      totalRemaining,
    );
    setFinalizeFailed(false);
    setCases(recovery.localCases);
    setIndex(recovery.index);
    setTotalRemaining(recovery.totalRemaining);
    requiredReviewLifecycleRef.current = recovery.lifecycle;
    requiredReviewNavigationRef.current = recovery.navigation;
    setMessage('Review został zsynchronizowany z zapisaną decyzją.');
    void loadCases(
      undefined,
      true,
      recovery.progressRequest.offset,
      recovery.progressRequest.preferredIndex,
      recovery.progressRequest.teamFilter,
      recovery.progressRequest.queue,
    );
  }

  async function finalizeCorrections(
    teamFilter: TeamReviewFilter = activeTeamFilter,
    queue: ReviewedIdentityReviewQueue = activeQueue,
  ) {
    if (finalizeInFlight.current) return;
    finalizeInFlight.current = true;
    onCompletionSynchronizationChange?.(true);
    setFinalizing(true);
    setFinalizeFailed(false);
    setMessage('Przeliczam Review po zapisaniu decyzji…');
    let authoritativeWorkflow: ReviewWorkflow | undefined;
    try {
      const { result, cases: refreshedCases } = await finalizeDeferredReviewBatch(
        () => finalizeReviewedIdentityCorrections(match.id),
        () => loadCases(undefined, true, 0, 0, teamFilter, queue),
        onWorkflowChanged,
      );
      authoritativeWorkflow = result.workflow;
      if (refreshedCases.length === 0) {
        setCases([]);
        setIndex(0);
      }
      requiredReviewLifecycleRef.current = beginRequiredReviewLifecycle(0);
      requiredReviewNavigationRef.current = beginRequiredReviewNavigation();
      setMessage(reviewRecomputeMessage(
        refreshedCases.length,
        result.workflow.issues.coverage_readiness?.allows_finalize === false,
      ));
    } catch (error) {
      setFinalizeFailed(true);
      setMessage(`Decyzje są zapisane, ale przeliczenie Review nie powiodło się. ${errorMessage(error)}`);
    } finally {
      finalizeInFlight.current = false;
      setFinalizing(false);
      onCompletionSynchronizationChange?.(false, authoritativeWorkflow);
    }
  }

  if (loading && !finalizing) return <p className='loading-line'><span className='spinner' /> Ładuję przypadki do sprawdzenia…</p>;

  return <section className='identity-exception-review'>
    <header className='identity-exception-header'>
      <div className='identity-exception-heading'>
        <h2>{activeQueue === 'required' ? 'Wymagane przypadki' : 'Opcjonalny MAX'}</h2>
        <p>{activeQueue === 'required'
          ? 'Rozwiąż przypadki wymagane do zakończenia Review.'
          : `Pełny audyt tożsamości ${matchTeamName(match.teams || [], 'A')} jest dobrowolny i nie blokuje zakończenia Review.`}</p>
      </div>
      <div className='identity-exception-case-context' aria-live='polite'>
        <div className='identity-exception-controls'>
          {showPrimaryQueueSwitch && <nav className='identity-review-queue-switch' aria-label='Rodzaj kolejki Review'>
            <button type='button' className={activeQueue === 'required' ? 'active' : ''} onClick={() => changeQueue('required')} disabled={loading || finalizing}>Wymagane</button>
            <button type='button' className={activeQueue === 'optional_audit' ? 'active' : ''} onClick={() => changeQueue('optional_audit')} disabled={loading || finalizing}>Kontynuuj do MAX <span>{optionalAuditRemaining}</span></button>
          </nav>}
          {activeQueue === 'required' && <nav className='identity-team-review-filter' aria-label='Filtr przypadków według drużyny'>
            {filterOptions.map((option) => <button
              type='button'
              key={option.value}
              className={activeTeamFilter === option.value ? 'active' : ''}
              aria-pressed={activeTeamFilter === option.value}
              onClick={() => changeTeamFilter(option.value)}
              disabled={loading || finalizing}
            >{option.label} <span>{option.count}</span></button>)}
          </nav>}
        </div>
        <div className='identity-exception-case-meta'>
          {cases.length > 0 && <strong className='reviewed-status-badge'>Przypadek {pageOffset + index + 1} z {totalRemaining}</strong>}
          {caseTimeRange && <span>{caseTimeRange}</span>}
          {reviewCase && <span>{reviewCase.unit.detected_observation_count || card?.detected_frames || 0} obserwacji</span>}
          {activeQueue === 'optional_audit' && reviewCase && <span className='identity-optional-max-impact'>
            <strong>Opcjonalny MAX{optionalMaxSlot ? ` · ${optionalMaxSlot}` : ''}</strong>
            <span>
              {caseTimeRange && `${caseTimeRange} · `}
              {optionalMaxDuration != null && `${optionalMaxDuration.toFixed(1)} s · `}
              {optionalMaxMarginalObservations} obserwacji · {optionalMaxMarginalCoverage != null
                ? formatReviewedIdentityPercentagePoints(optionalMaxMarginalCoverage)
                : '+0 pp'}
            </span>
            <span className='sr-only'>Potencjalny wzrost pokrycia wynosi {optionalMaxMarginalCoverage != null
              ? formatReviewedIdentityPercentagePoints(optionalMaxMarginalCoverage)
              : '+0 punktów procentowych'}.</span>
          </span>}
          {activeTeamFilter !== 'all' && <span>Łącznie: {globalRemaining}</span>}
          <span>{activeQueue === 'required'
            ? requiredCasesLabel(workflow.issues.normal_blocking ?? workflow.issues.blocking)
            : `${totalRemaining} opcjonalnych`}</span>
        </div>
      </div>
    </header>

    {coverage && <section className='identity-coverage-summary' aria-label='Pokrycie rozpoznania zawodników'>
      <div>
        <strong>Pokrycie rozpoznania</strong>
        <span>Imiennie: {formatReviewedIdentityPercent(coverage.named_observation_coverage)}</span>
        <span>Drużyna znana: {formatReviewedIdentityPercent(coverage.team_known_observation_coverage)}</span>
      </div>
      {Object.entries(coverage.per_team).filter(([team]) => team === 'A' || team === 'B').map(([team, row]) => <div
        key={team}
        className={activeTeamFilter === team ? 'active-team' : ''}
      >
        <strong>{matchTeamName(match.teams || [], team as 'A' | 'B')}</strong>
        <span>Imiennie {formatReviewedIdentityPercent(row.named_observation_coverage)}
          {row.named_coverage_status === 'not_required_by_scope' ? ' · informacyjnie' : ''}
        </span>
        <progress
          max={1}
          value={row.named_observation_coverage || 0}
          aria-label={`Pokrycie imienne ${matchTeamName(match.teams || [], team as 'A' | 'B')}`}
        />
      </div>)}
      {coverageDebt && <button type='button' className='identity-coverage-details-trigger' onClick={() => setCoverageDetailsOpen(true)}>Szczegóły pokrycia</button>}
    </section>}
    {coverageDebt && coverageDetailsOpen && <ReviewedIdentityCoverageDebtDialog
      match={match}
      debt={coverageDebt}
      onClose={() => setCoverageDetailsOpen(false)}
    />}
    {workload && workload.level !== 'normal' && <details className='identity-exception-guidance identity-coverage-warning'>
      <summary>Duża kolejka: {workload.remaining_cases} fragmentów <span>Szczegóły</span></summary>
      <p>To sygnał słabej ciągłości trackingu. Decyzje są uporządkowane według wpływu, a nie ucięte limitem.</p>
    </details>}

    {reviewCase && entity && hasVisualEvidence && evidence ? <>
      {guidanceCount > 0 && <details className='identity-exception-guidance'>
        <summary>Informacje o tym przypadku <span>{guidanceCount}</span></summary>
        <div className='identity-exception-guidance-content'>
          {reviewCase.unit.scope_kind === 'canonical_segment' && <div>
            <strong>System połączył w jednym tracklecie różne osoby.</strong>
            <p>Oceń tylko pokazany fragment. Decyzja nie obejmie sąsiednich ani niejednoznacznych klatek.</p>
          </div>}
          {reviewCase.unit.scope_kind === 'material_continuity' && <div>
            <strong>Duża luka ciągłości: {reviewCase.unit.stable_slot_id}</strong>
            <p>To {reviewCase.unit.continuity_fragment_count || reviewCase.unit.tracklet_count} bezpieczne fragmenty tego samego lokalnego ciągu. Wybór obejmie wyłącznie pokazane obserwacje, nie cały slot.</p>
          </div>}
          {activeQueue === 'optional_audit' && <div>
            <strong>Opcjonalny MAX — decyzja nie jest wymagana.</strong>
            <p>Możesz nazwać zawodnika, oznaczyć „Nie wiem” albo pominąć na razie bez zapisywania.</p>
            <p>
              Potencjalny wzrost: {reviewCase.unit.marginal_named_observation_gain || 0} obserwacji
              {reviewCase.unit.optional_max_marginal_coverage_gain_pp != null
                ? ` (${formatReviewedIdentityPercentagePoints(reviewCase.unit.optional_max_marginal_coverage_gain_pp)})`
                : ''}.
            </p>
          </div>}
          {unitEvidence?.kind === 'team_attribution' && <div>
            <strong>Potwierdź tylko drużynę albo rodzaj detekcji.</strong>
            <p>Te widoki nie służą do rozpoznania imienia. Wybierz Team A, Team B, sędziego, fałszywą detekcję albo „Nie wiem”.</p>
          </div>}
          {reviewCase.unit.priority === 'coverage' && <div>
            <strong>Ten fragment ma duży wpływ na kompletność statystyk.</strong>
            <p>Może przypisać do {reviewCase.unit.potential_named_observation_gain || reviewCase.unit.detected_observation_count} obserwacji
              {reviewCase.unit.potential_named_coverage_gain_pp && ['A', 'B'].includes(reviewCase.unit.coverage_team_label || '')
                ? ` (+${reviewCase.unit.potential_named_coverage_gain_pp.toFixed(1)} pp dla ${matchTeamName(match.teams || [], reviewCase.unit.coverage_team_label as 'A' | 'B')})`
                : ''}.</p>
          </div>}
        </div>
      </details>}
      <div className='identity-exception-workstation'>
        <section className='identity-exception-evidence-column' aria-label='Widoki zawodnika'>
          <div className='identity-exception-column-heading'>
            <strong>Porównaj widoki</strong>
            <span>{evidence.anchor_crops.length} {evidence.anchor_crops.length === 1 ? 'widok' : 'widoków'}</span>
          </div>
          <div className='identity-exception-evidence'>
            {evidence.anchor_crops.map((crop) => <figure
              key={crop.anchor_crop_id}
              className={`team-${reviewCase.unit.source_team_label.toLowerCase()}`}
            >
              <img src={artifactUrl(match.id, crop.artifact)} alt='Widok zawodnika do identyfikacji' />
              <figcaption>
                <span>Wybrany widok zawodnika</span>
                {cropQualityLabel(crop.quality_class) && <small>{cropQualityLabel(crop.quality_class)}</small>}
              </figcaption>
            </figure>)}
            {(unitEvidence?.boundary_crops || []).map((crop) => <figure
              key={`boundary-${crop.anchor_crop_id}`}
              className='outside-target'
            >
              <img src={artifactUrl(match.id, crop.artifact)} alt='Sąsiedni fragment poza zakresem decyzji' />
              <figcaption><span>Sąsiedni fragment</span><small>poza zakresem decyzji</small></figcaption>
            </figure>)}
          </div>
        </section>
        <aside className='identity-exception-decision-column' aria-label='Decyzja operatora'>
          <ReviewedIdentityCorrectionForm
            key={reviewUnitKey(reviewCase.unit)}
            matchId={match.id}
            entity={entity}
            teams={match.teams}
            teamAttributionOnly={unitEvidence?.kind === 'team_attribution'}
            onCancel={() => setMessage('Decyzja nie została zapisana.')}
            onSaved={saved}
            onSaveConflict={recoverFromReviewSaveConflict}
            onMixedStaged={(caseId, disposition) => {
              if (disposition === 'resolve_now') onMixedResolveNow?.(caseId);
            }}
            deferRecompute
            mixedHandling={activeQueue === 'optional_audit' ? 'direct' : 'stage'}
            navigation={{
              onPrevious: () => navigate('previous'),
              onNext: () => navigate('next'),
              previousDisabled: pageOffset === 0 && index === 0,
              nextDisabled: !hasMore && index >= cases.length - 1,
              saveLabel: 'Zapisz + następny',
              nextLabel: activeQueue === 'optional_audit' ? 'Pomiń na razie' : 'Następny',
            }}
          />
        </aside>
      </div>
    </> : reviewCase && entity ? <div className='status'>
      <strong>Brak materiału pozwalającego wiarygodnie rozstrzygnąć ten przypadek.</strong>
      <p>Odśwież Review — ten przypadek nie powinien wymagać ręcznej decyzji.</p>
      {onRetryReview && <button type='button' className='secondary' onClick={() => void onRetryReview()}>
        Odśwież Review
      </button>}
    </div> : finalizing ? null : coverageBlockedWithoutCases ? <div className='status error identity-coverage-blocked' role='alert'>
      <strong>Nie można zakończyć Review.</strong>
      <p>{teamAttributionBlocker || 'Pozostała istotna liczba nierozpoznanych obserwacji, ale system nie ma bezpiecznych przypadków do ręcznego przypisania.'}</p>
      <p>To wskazuje na problem jakości lub struktury identity. Review nie zostanie automatycznie zakończone ani opublikowane.</p>
      {onRetryReview && <button type='button' className='secondary' onClick={() => void onRetryReview()}>
        Odśwież Review
      </button>}
    </div> : activeTeamFilter !== 'all' && totalRemaining === 0 && globalRemaining > 0 ? <div className='status identity-team-filter-empty'>
      <strong>Brak pozostałych przypadków dla {activeTeamName}.</strong>
      <p>Możesz wybrać inną drużynę. Globalny Review nadal ma {globalRemaining} przypadków do sprawdzenia.</p>
    </div> : <div className='status'>
      <strong>Nie udało się przygotować podglądu przypadku wymagającego decyzji.</strong>
      <p>Workflow nadal wskazuje: {requiredCasesLabel(workflow.issues.normal_blocking ?? workflow.issues.blocking)}. Odśwież Review albo otwórz diagnostykę.</p>
      {onRetryReview && <button type='button' className='secondary' onClick={() => void onRetryReview()}>
        Odśwież Review
      </button>}
    </div>}
    {finalizing && <p className='loading-line'><span className='spinner' /> Przeliczam Review po zapisaniu decyzji…</p>}
    {message && <p className={`status${finalizeFailed ? ' error' : ''}`}>{message}</p>}
    {activeQueue === 'optional_audit' && !reviewCase && !loading && <button type='button' className='secondary' onClick={() => changeQueue('required')}>Wróć do wymaganych</button>}
    {finalizeFailed && <button
      type='button'
      className='secondary'
      onClick={() => void finalizeCorrections(activeTeamFilter, activeQueue)}
      disabled={finalizing}
    >Ponów przeliczenie Review</button>}
  </section>;
}
