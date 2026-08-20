import { useEffect, useMemo, useRef, useState } from 'react';

import {
  artifactUrl,
  finalizeReviewedIdentityCorrections,
  getIdentityRosterSubjectReview,
  getReviewedIdentityReviewProgress,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type {
  IdentityRosterSubjectReviewCard,
  Match,
  ReviewedCorrectionResponse,
  ReviewedIdentityAtEntity,
  ReviewedIdentityCoverage,
  ReviewedIdentityCoverageReadiness,
  ReviewedIdentityReviewFilters,
  ReviewedIdentityReviewQueue,
  ReviewedIdentityReviewUnit,
  ReviewedIdentityWorkload,
  ReviewWorkflow,
} from '../types';
import { hasOperatorReviewableVisualEvidence } from '../utils/identityReviewWorkspace';
import {
  finalizeDeferredReviewBatch,
  removeResolvedReviewCase,
  resolveReviewPageNavigation,
  reviewUnitKey,
  shouldAutoFinalizeDeferredQueue,
} from '../utils/identityExceptionQueue';
import { moveReviewCaseIndex } from '../utils/identityExceptionWorkspace';
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
import { ReviewedIdentityCorrectionForm } from './ReviewedIdentityCorrectionForm';

type Props = {
  match: Match;
  workflow: ReviewWorkflow;
  onWorkflowChanged: (workflow: ReviewWorkflow) => void;
  onRetryReview?: () => Promise<void>;
  initialQueue?: ReviewedIdentityReviewQueue;
  onOptionalAuditRemainingChanged?: (remaining: number) => void;
};

type ReviewCase = {
  unit: ReviewedIdentityReviewUnit;
  card: IdentityRosterSubjectReviewCard | null;
};

const REVIEW_PAGE_SIZE = 20;

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
  onRetryReview,
  initialQueue = 'required',
  onOptionalAuditRemainingChanged,
}: Props) {
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
  const [coverageReadiness, setCoverageReadiness] = useState<ReviewedIdentityCoverageReadiness | null>(null);
  const [workload, setWorkload] = useState<ReviewedIdentityWorkload | null>(null);
  const [activeTeamFilter, setActiveTeamFilter] = useState<TeamReviewFilter>('all');
  const [reviewFilters, setReviewFilters] = useState<ReviewedIdentityReviewFilters | null>(null);
  const [activeQueue, setActiveQueue] = useState<ReviewedIdentityReviewQueue>(initialQueue);
  const [optionalAuditRemaining, setOptionalAuditRemaining] = useState(0);
  const finalizeInFlight = useRef(false);
  const loadRequestIdRef = useRef(0);
  const cardsBySubjectRef = useRef<Map<string, IdentityRosterSubjectReviewCard> | null>(null);

  async function loadCases(
    ignore?: () => boolean,
    preserveMessage = false,
    offset = 0,
    preferredIndex = 0,
    teamFilter: TeamReviewFilter = activeTeamFilter,
    queue: ReviewedIdentityReviewQueue = activeQueue,
  ): Promise<ReviewCase[]> {
    const requestId = ++loadRequestIdRef.current;
    setLoading(true);
    if (!preserveMessage) setMessage('');
    try {
      const [document, progress] = await Promise.all([
        cardsBySubjectRef.current
          ? Promise.resolve(null)
          : getIdentityRosterSubjectReview(match.id),
        getReviewedIdentityReviewProgress(
          match.id,
          offset,
          REVIEW_PAGE_SIZE,
          apiTeamFilter(teamFilter),
          queue,
        ),
      ]);
      if (ignore?.() || requestId !== loadRequestIdRef.current) return [];
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
      setPageOffset(progress.pagination?.offset ?? offset);
      setHasMore(progress.pagination?.has_more ?? false);
      setCoverage(progress.identity_coverage || null);
      setCoverageReadiness(progress.coverage_readiness || null);
      setWorkload(progress.workload || null);
      setReviewFilters(progress.filters || null);
      const nextOptionalRemaining = progress.summary.optional_audit_cases_remaining || 0;
      setOptionalAuditRemaining(nextOptionalRemaining);
      onOptionalAuditRemainingChanged?.(nextOptionalRemaining);
      if (shouldAutoFinalizeDeferredQueue(
        queue,
        actionable,
        progress.recompute_required,
        progress.filters?.counts.all
          ?? progress.pagination?.global_total_remaining
          ?? actionable.length,
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
      if (!ignore?.() && requestId === loadRequestIdRef.current) setMessage(errorMessage(error));
      return [];
    } finally {
      if (!ignore?.() && requestId === loadRequestIdRef.current) setLoading(false);
    }
  }

  useEffect(() => {
    let disposed = false;
    setActiveTeamFilter('all');
    setActiveQueue(initialQueue);
    void loadCases(() => disposed, false, 0, 0, 'all', initialQueue);
    return () => { disposed = true; };
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
  const filterOptions = useMemo(
    () => teamReviewFilterOptions(match.teams || [], reviewFilters),
    [match.teams, reviewFilters],
  );
  const activeTeamName = activeTeamFilter === 'all'
    ? 'Wszystkie'
    : matchTeamName(match.teams || [], activeTeamFilter);
  const globalRemaining = reviewFilters?.counts.all ?? totalRemaining;
  const coverageBlockedWithoutCases = globalRemaining === 0
    && coverageReadiness?.allows_finalize === false;
  const teamAttributionBlocker = teamAttributionBlockerMessage(coverageReadiness);
  const guidanceCount = Number(reviewCase?.unit.scope_kind === 'canonical_segment')
    + Number(reviewCase?.unit.scope_kind === 'material_continuity')
    + Number(activeQueue === 'optional_audit')
    + Number(reviewCase?.unit.priority === 'coverage')
    + Number(unitEvidence?.kind === 'team_attribution');

  function changeTeamFilter(nextFilter: TeamReviewFilter) {
    if (nextFilter === activeTeamFilter || loading || finalizing) return;
    setActiveTeamFilter(nextFilter);
    setCases([]);
    setIndex(0);
    setPageOffset(0);
    setTotalRemaining(0);
    setMessage('');
    void loadCases(undefined, false, 0, 0, nextFilter, activeQueue);
  }

  function changeQueue(nextQueue: ReviewedIdentityReviewQueue) {
    if (nextQueue === activeQueue || loading || finalizing) return;
    setActiveQueue(nextQueue);
    setActiveTeamFilter('all');
    setCases([]);
    setIndex(0);
    setPageOffset(0);
    setTotalRemaining(0);
    setMessage('');
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
      pageSize: REVIEW_PAGE_SIZE,
      hasMore,
    });
    if (destination.kind === 'local') {
      moveToCase(destination.index);
    } else if (destination.kind === 'page') {
      void loadCases(
        undefined,
        true,
        destination.offset,
        destination.index,
        activeTeamFilter,
        activeQueue,
      );
    }
  }

  function saved(result: ReviewedCorrectionResponse) {
    if (!reviewCase || !result.recompute_deferred) {
      if (result.workflow) onWorkflowChanged(result.workflow);
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
    if (activeQueue === 'optional_audit') {
      onOptionalAuditRemainingChanged?.(Math.max(0, totalRemaining - 1));
    }
    if (next.cases.length === 0 && hasMore) {
      void loadCases(
        undefined,
        true,
        pageOffset + REVIEW_PAGE_SIZE,
        0,
        activeTeamFilter,
        activeQueue,
      );
    } else if (shouldAutoFinalizeDeferredQueue(activeQueue, next.cases)) {
      void finalizeCorrections(activeTeamFilter, activeQueue);
    }
  }

  async function finalizeCorrections(
    teamFilter: TeamReviewFilter = activeTeamFilter,
    queue: ReviewedIdentityReviewQueue = activeQueue,
  ) {
    if (finalizeInFlight.current) return;
    finalizeInFlight.current = true;
    setFinalizing(true);
    setFinalizeFailed(false);
    setMessage('Przeliczam Review po zapisaniu decyzji…');
    try {
      const { result, cases: refreshedCases } = await finalizeDeferredReviewBatch(
        () => finalizeReviewedIdentityCorrections(match.id),
        () => loadCases(undefined, true, 0, 0, teamFilter, queue),
        onWorkflowChanged,
      );
      if (refreshedCases.length === 0) {
        setCases([]);
        setIndex(0);
      }
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
    }
  }

  if (loading && !finalizing) return <p className='loading-line'><span className='spinner' /> Ładuję przypadki do sprawdzenia…</p>;

  return <section className='identity-exception-review'>
    <header className='identity-exception-header'>
      <div className='identity-exception-heading'>
        <h2>Pozostałe przypadki</h2>
        <p>{activeQueue === 'required'
          ? 'Rozwiąż przypadki wymagane do zakończenia Review.'
          : 'Pełny audyt tożsamości Corgi jest dobrowolny i nie blokuje zakończenia Review.'}</p>
      </div>
      <div className='identity-exception-case-context' aria-live='polite'>
        <div className='identity-exception-controls'>
          <nav className='identity-review-queue-switch' aria-label='Rodzaj kolejki Review'>
            <button type='button' className={activeQueue === 'required' ? 'active' : ''} onClick={() => changeQueue('required')} disabled={loading || finalizing}>Wymagane</button>
            <button type='button' className={activeQueue === 'optional_audit' ? 'active' : ''} onClick={() => changeQueue('optional_audit')} disabled={loading || finalizing}>Kontynuuj do MAX <span>{optionalAuditRemaining}</span></button>
          </nav>
          <nav className='identity-team-review-filter' aria-label='Filtr przypadków według drużyny'>
            {filterOptions.map((option) => <button
              type='button'
              key={option.value}
              className={activeTeamFilter === option.value ? 'active' : ''}
              aria-pressed={activeTeamFilter === option.value}
              onClick={() => changeTeamFilter(option.value)}
              disabled={loading || finalizing}
            >{option.label} <span>{option.count}</span></button>)}
          </nav>
        </div>
        <div className='identity-exception-case-meta'>
          {cases.length > 0 && <strong className='reviewed-status-badge'>Przypadek {pageOffset + index + 1} z {totalRemaining}</strong>}
          {caseTimeRange && <span>{caseTimeRange}</span>}
          {reviewCase && <span>{reviewCase.unit.detected_observation_count || card?.detected_frames || 0} obserwacji</span>}
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
        <span>Imiennie: {Math.round((coverage.named_observation_coverage || 0) * 100)}%</span>
        <span>Drużyna znana: {Math.round((coverage.team_known_observation_coverage || 0) * 100)}%</span>
      </div>
      {Object.entries(coverage.per_team).filter(([team]) => team === 'A' || team === 'B').map(([team, row]) => <div
        key={team}
        className={activeTeamFilter === team ? 'active-team' : ''}
      >
        <strong>{matchTeamName(match.teams || [], team as 'A' | 'B')}</strong>
        <span>Imiennie {Math.round((row.named_observation_coverage || 0) * 100)}%
          {row.named_coverage_status === 'not_required_by_scope' ? ' · informacyjnie' : ''}
        </span>
        <progress
          max={1}
          value={row.named_observation_coverage || 0}
          aria-label={`Pokrycie imienne ${matchTeamName(match.teams || [], team as 'A' | 'B')}`}
        />
      </div>)}
    </section>}
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
            <strong>Pełny audyt tożsamości Corgi — decyzja nie jest wymagana.</strong>
            <p>Możesz nazwać zawodnika, oznaczyć „Nie wiem” albo pominąć ten fragment bez zapisywania.</p>
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
            deferRecompute
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
