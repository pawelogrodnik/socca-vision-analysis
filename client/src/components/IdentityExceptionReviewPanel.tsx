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
  shouldFinalizeDeferredReview,
} from '../utils/identityExceptionQueue';
import { moveReviewCaseIndex } from '../utils/identityExceptionWorkspace';
import {
  apiTeamFilter,
  matchTeamName,
  teamReviewFilterOptions,
  type TeamReviewFilter,
} from '../utils/identityExceptionTeamFilter';
import { requiredCasesLabel } from '../utils/reviewWorkflowPresentation';
import { formatReviewTime } from '../utils/reviewedOutputPresentation';
import { ReviewedIdentityCorrectionForm } from './ReviewedIdentityCorrectionForm';

type Props = {
  match: Match;
  workflow: ReviewWorkflow;
  onWorkflowChanged: (workflow: ReviewWorkflow) => void;
  onRetryReview?: () => Promise<void>;
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
  const finalizeInFlight = useRef(false);
  const loadRequestIdRef = useRef(0);
  const cardsBySubjectRef = useRef<Map<string, IdentityRosterSubjectReviewCard> | null>(null);

  async function loadCases(
    ignore?: () => boolean,
    preserveMessage = false,
    offset = 0,
    preferredIndex = 0,
    teamFilter: TeamReviewFilter = activeTeamFilter,
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
        .filter((item) => item.priority === 'high' || item.priority === 'coverage')
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
      if (shouldFinalizeDeferredReview(
        actionable,
        progress.recompute_required,
        progress.filters?.counts.all
          ?? progress.pagination?.global_total_remaining
          ?? actionable.length,
        progress.coverage_readiness?.allows_finalize !== false,
      )) {
        setCases([]);
        setIndex(0);
        void finalizeCorrections(teamFilter);
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
    void loadCases(() => disposed, false, 0, 0, 'all');
    return () => { disposed = true; };
    // Cards are reloaded after a semantic decision, not for incidental workflow object updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id]);

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

  function changeTeamFilter(nextFilter: TeamReviewFilter) {
    if (nextFilter === activeTeamFilter || loading || finalizing) return;
    setActiveTeamFilter(nextFilter);
    setCases([]);
    setIndex(0);
    setPageOffset(0);
    setTotalRemaining(0);
    setMessage('');
    void loadCases(undefined, false, 0, 0, nextFilter);
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
    if (next.cases.length === 0 && hasMore) {
      void loadCases(
        undefined,
        true,
        pageOffset + REVIEW_PAGE_SIZE,
        0,
        activeTeamFilter,
      );
    } else if (shouldFinalizeDeferredReview(next.cases)) {
      void finalizeCorrections(activeTeamFilter);
    }
  }

  async function finalizeCorrections(teamFilter: TeamReviewFilter = activeTeamFilter) {
    if (finalizeInFlight.current) return;
    finalizeInFlight.current = true;
    setFinalizing(true);
    setFinalizeFailed(false);
    setMessage('Przeliczam Review po zapisaniu decyzji…');
    try {
      const { result } = await finalizeDeferredReviewBatch(
        () => finalizeReviewedIdentityCorrections(match.id),
        () => loadCases(undefined, true, 0, 0, teamFilter),
        onWorkflowChanged,
      );
      if (result.workflow.phase === 'exceptions') {
        setMessage(`Po przeliczeniu pozostały kolejne przypadki do sprawdzenia.`);
      } else {
        setCases([]);
        setIndex(0);
        setMessage('Review zostało przeliczone.');
      }
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
        <p className='eyebrow'>Krok 2</p>
        <h2>Pozostałe przypadki</h2>
        <p>Najpierw pokażemy konflikty, a potem największe nierozpoznane fragmenty wpływające na statystyki zawodników.</p>
      </div>
      <div className='identity-exception-case-context' aria-live='polite'>
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
        {cases.length > 0 && <span className='reviewed-status-badge'>Przypadek {pageOffset + index + 1} z {totalRemaining}</span>}
        {activeTeamFilter !== 'all' && <small>Łącznie pozostało: {globalRemaining}</small>}
        {caseTimeRange && <strong>{caseTimeRange}</strong>}
        {reviewCase && <span>{reviewCase.unit.detected_observation_count || card?.detected_frames || 0} wykrytych obserwacji</span>}
        <small>{requiredCasesLabel(workflow.issues.normal_blocking ?? workflow.issues.blocking)}</small>
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
        <span>Imiennie {Math.round((row.named_observation_coverage || 0) * 100)}%</span>
        <progress
          max={1}
          value={row.named_observation_coverage || 0}
          aria-label={`Pokrycie imienne ${matchTeamName(match.teams || [], team as 'A' | 'B')}`}
        />
      </div>)}
    </section>}
    {workload && workload.level !== 'normal' && <div className='status warning identity-coverage-warning'>
      <strong>Dużo fragmentów wymaga sprawdzenia ({workload.remaining_cases}).</strong>
      <p>To sygnał słabej ciągłości trackingu. Decyzje są uporządkowane według wpływu, a nie ucięte limitem.</p>
    </div>}

    {reviewCase && entity && hasVisualEvidence && evidence ? <>
      {reviewCase.unit.scope_kind === 'canonical_segment' && <div className='status'>
        <strong>System połączył w jednym tracklecie różne osoby.</strong>
        <p>Oceń tylko pokazany fragment. Decyzja nie obejmie sąsiednich ani niejednoznacznych klatek.</p>
      </div>}
      {reviewCase.unit.priority === 'coverage' && <div className='status identity-coverage-impact'>
        <strong>Ten fragment ma duży wpływ na kompletność statystyk.</strong>
        <p>Może przypisać do {reviewCase.unit.potential_named_observation_gain || reviewCase.unit.detected_observation_count} obserwacji
          {reviewCase.unit.potential_named_coverage_gain_pp && ['A', 'B'].includes(reviewCase.unit.coverage_team_label || '')
            ? ` (+${reviewCase.unit.potential_named_coverage_gain_pp.toFixed(1)} pp dla ${matchTeamName(match.teams || [], reviewCase.unit.coverage_team_label as 'A' | 'B')})`
            : ''}.</p>
      </div>}
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
            onCancel={() => setMessage('Decyzja nie została zapisana.')}
            onSaved={saved}
            deferRecompute
            navigation={{
              onPrevious: () => navigate('previous'),
              onNext: () => navigate('next'),
              previousDisabled: pageOffset === 0 && index === 0,
              nextDisabled: !hasMore && index >= cases.length - 1,
              saveLabel: 'Zapisz + następny',
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
      <p>Pozostała istotna liczba nierozpoznanych obserwacji, ale system nie ma bezpiecznych przypadków do ręcznego przypisania.</p>
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
    {finalizeFailed && <button
      type='button'
      className='secondary'
      onClick={() => void finalizeCorrections(activeTeamFilter)}
      disabled={finalizing}
    >Ponów przeliczenie Review</button>}
  </section>;
}
