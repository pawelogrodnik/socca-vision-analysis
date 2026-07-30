import { useEffect, useMemo, useRef, useState } from 'react';

import {
  artifactUrl,
  getSecondHalfIdentityReanchor,
  getSecondHalfIdentityReanchorSeeds,
  saveSecondHalfIdentityReanchorSeeds,
} from '../api';
import { errorMessage } from '../lib/helpers';
import { IdentityAuditObservationPreview } from './IdentityAuditObservationPreview';
import type {
  InitialIdentityAuditObservation,
  InitialIdentityAuditSeedStoreDocument,
  InitialIdentityAuditSeedUpdate,
  InitialIdentityAuditTelemetryEvent,
  Match,
  SecondHalfIdentityReanchorDocument,
} from '../types';
import {
  createInitialIdentityAuditEventId,
  initialIdentityAuditActionLabel,
  initialIdentityAuditClearUpdate,
  initialIdentityAuditDecisionMap,
  initialIdentityAuditPlayerAction,
  initialIdentityAuditSeedUpdate,
  observationBoxStyle,
  type InitialIdentityAuditAction,
  type InitialIdentityAuditDecision,
} from '../utils/initialIdentityAudit';
import { RequiredFinalSaveQueue } from '../utils/requiredFinalSaveQueue';
import {
  secondHalfObservationLabel,
  secondHalfSuggestionSourceLabel,
  secondHalfTeamClass,
  secondHalfVisibleSuggestion,
} from '../utils/secondHalfIdentityReanchorPresentation';

interface SecondHalfIdentityReanchorPanelProps {
  match: Match;
  onStatus: (message: string) => void;
  maximumConfirmations?: number;
  benchmarkState?: string;
  onFinished?: () => Promise<void>;
}

type ReanchorSaveRequest = {
  updates: InitialIdentityAuditSeedUpdate[];
  telemetryEvents: InitialIdentityAuditTelemetryEvent[];
};

type ReanchorSuggestion = {
  player_id: string;
  player_name: string;
  rank?: number;
  advisory_only?: boolean;
  suggestion_source?: string;
  candidate_subject_id?: string | null;
};

function statusLabel(document: SecondHalfIdentityReanchorDocument): string {
  if (document.status === 'not_applicable') {
    return 'Nie dotyczy: mecz nie ma jawnie skonfigurowanej drugiej polowy.';
  }
  if (document.status === 'skipped_already_resolved') {
    return 'Pominiety: pierwsze potwierdzenia bezpiecznie pokrywaja druga polowe.';
  }
  return `Gotowy: ${document.frames.length} klatki, tylko szybkie potwierdzenia.`;
}

function actionForSuggestion(
  suggestion: ReanchorSuggestion,
  observation: InitialIdentityAuditObservation,
  document: SecondHalfIdentityReanchorDocument,
): InitialIdentityAuditAction | null {
  for (const team of document.roster) {
    const player = team.players.find(
      (candidate) => candidate.player_id === suggestion.player_id,
    );
    if (player) {
      if (
        observation.team_label !== 'U'
        && team.team_label !== observation.team_label
      ) {
        return null;
      }
      const action = initialIdentityAuditPlayerAction(player, team);
      if (action.kind !== 'player') return null;
      return {
        ...action,
        suggestionContext: {
          suggestion_source: (
            suggestion.suggestion_source ?? 'h1_safe_lineage'
          ),
          advisory_only: suggestion.advisory_only ?? false,
          rank: suggestion.rank ?? null,
          candidate_subject_id: (
            suggestion.candidate_subject_id ?? null
          ),
          observation_key: observation.observation_key,
          player_id: suggestion.player_id,
        },
      };
    }
  }
  return null;
}

export function SecondHalfIdentityReanchorPanel({
  match,
  onStatus,
  maximumConfirmations,
  benchmarkState,
  onFinished,
}: SecondHalfIdentityReanchorPanelProps) {
  const [document, setDocument] = useState<SecondHalfIdentityReanchorDocument | null>(null);
  const [seedStore, setSeedStore] = useState<InitialIdentityAuditSeedStoreDocument | null>(null);
  const [decisions, setDecisions] = useState<Record<string, InitialIdentityAuditDecision>>({});
  const [frameIndex, setFrameIndex] = useState(0);
  const [selectedObservationKey, setSelectedObservationKey] = useState<string | null>(null);
  const [showRoster, setShowRoster] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [open, setOpen] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const sessionIdRef = useRef('');
  const saveQueueRef = useRef(
    new RequiredFinalSaveQueue<InitialIdentityAuditSeedStoreDocument>(),
  );
  const failedSaveRequestRef = useRef<ReanchorSaveRequest | null>(null);
  const finishingRef = useRef(false);

  const frame = document?.frames[frameIndex] ?? null;
  const selectedObservation = useMemo(
    () => frame?.observations.find(
      (observation) => observation.observation_key === selectedObservationKey,
    ) ?? null,
    [frame, selectedObservationKey],
  );
  const selectedVisibleSuggestion = selectedObservation
    ? secondHalfVisibleSuggestion(selectedObservation)
    : null;
  const currentSuggestion = document && selectedObservation
    && selectedVisibleSuggestion
    ? actionForSuggestion(
        selectedVisibleSuggestion,
        selectedObservation,
        document,
      )
    : null;
  const selectedDecision = selectedObservationKey
    ? decisions[selectedObservationKey]
    : undefined;
  const activeDecisionCount = Object.values(decisions).filter(
    (decision) => decision.kind !== 'skip',
  ).length;
  const confirmationBudgetReached = seedStore?.operator_budget?.reached
    ?? (
      maximumConfirmations !== undefined
      && activeDecisionCount >= maximumConfirmations
    );
  const canCreateConfirmation = !confirmationBudgetReached || Boolean(selectedDecision);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setDocument(null);
    setSeedStore(null);
    setDecisions({});
    setOpen(false);
    setFailure(null);
    setSaving(false);
    setFinishing(false);
    saveQueueRef.current = (
      new RequiredFinalSaveQueue<InitialIdentityAuditSeedStoreDocument>()
    );
    failedSaveRequestRef.current = null;
    finishingRef.current = false;
    void getSecondHalfIdentityReanchor(match.id)
      .then((nextDocument) => {
        if (active) setDocument(nextDocument);
      })
      .catch((error: unknown) => {
        if (active) setFailure(errorMessage(error));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [match.id]);

  function event(
    eventType: InitialIdentityAuditTelemetryEvent['event_type'],
    observationKey?: string,
  ): InitialIdentityAuditTelemetryEvent {
    return {
      event_id: createInitialIdentityAuditEventId(),
      session_id: sessionIdRef.current,
      event_type: eventType,
      audit_frame_key: frame?.audit_frame_key,
      observation_key: observationKey,
      occurred_at: new Date().toISOString(),
    };
  }

  function selectDefaultObservation(nextFrameIndex: number) {
    const nextFrame = document?.frames[nextFrameIndex];
    const suggestion = nextFrame?.observations.find(
      (observation) => Boolean(secondHalfVisibleSuggestion(observation)),
    );
    setSelectedObservationKey(
      suggestion?.observation_key
      ?? nextFrame?.observations[0]?.observation_key
      ?? null,
    );
    setShowRoster(false);
  }

  async function performSave(
    request: ReanchorSaveRequest,
  ): Promise<InitialIdentityAuditSeedStoreDocument> {
    setSaving(true);
    try {
      const nextStore = await saveSecondHalfIdentityReanchorSeeds(
        match.id,
        request.updates,
        request.telemetryEvents,
      );
      setSeedStore(nextStore);
      setDecisions(initialIdentityAuditDecisionMap(nextStore.decisions));
      if (
        failedSaveRequestRef.current === null
        || failedSaveRequestRef.current === request
      ) {
        failedSaveRequestRef.current = null;
        setFailure(null);
      }
      return nextStore;
    } catch (error) {
      const message = errorMessage(error);
      failedSaveRequestRef.current ??= request;
      setFailure(message);
      onStatus(message);
      throw error;
    } finally {
      setSaving(false);
    }
  }

  async function retryFailedSave() {
    const request = failedSaveRequestRef.current;
    if (!request || finishingRef.current) return;
    try {
      await saveQueueRef.current.enqueue(() => performSave(request));
      onStatus('Zapis H2 zostal ponowiony poprawnie. Mozesz zakonczyc etap.');
    } catch {
      // performSave keeps the actionable error visible in the modal.
    }
  }

  async function openAudit() {
    if (!document || document.status !== 'ready') return;
    setLoading(true);
    setFailure(null);
    try {
      const nextStore = await getSecondHalfIdentityReanchorSeeds(match.id);
      sessionIdRef.current = createInitialIdentityAuditEventId();
      setSeedStore(nextStore);
      setDecisions(initialIdentityAuditDecisionMap(nextStore.decisions));
      setFrameIndex(0);
      selectDefaultObservation(0);
      setOpen(true);
      onStatus('Re-anchor drugiej polowy gotowy. Potwierdz tylko pewne sugestie.');
    } catch (error) {
      const message = errorMessage(error);
      setFailure(message);
      onStatus(message);
    } finally {
      setLoading(false);
    }
  }

  async function applyAction(action: InitialIdentityAuditAction) {
    if (!selectedObservation || finishingRef.current) return;
    const existingDecision = decisions[selectedObservation.observation_key];
    const budgetReached = seedStore?.operator_budget?.reached
      ?? (
        maximumConfirmations !== undefined
        && Object.values(decisions).filter((decision) => decision.kind !== 'skip').length
          >= maximumConfirmations
      );
    if (budgetReached && !existingDecision && action.kind !== 'skip') {
      onStatus('Limit potwierdzen H2 zostal osiagniety. Mozesz zmienic lub usunac istniejace potwierdzenie.');
      return;
    }
    const decision: InitialIdentityAuditDecision = {
      ...action,
      observationKey: selectedObservation.observation_key,
    };
    const request: ReanchorSaveRequest = {
      updates: [initialIdentityAuditSeedUpdate(decision)],
      telemetryEvents: [
        event('action', selectedObservation.observation_key),
      ],
    };
    try {
      await saveQueueRef.current.enqueue(() => performSave(request));
    } catch {
      // performSave keeps the modal open and the save error visible.
    }
  }

  function moveFrame(nextIndex: number) {
    if (!document) return;
    const bounded = Math.min(
      document.frames.length - 1,
      Math.max(0, nextIndex),
    );
    setFrameIndex(bounded);
    selectDefaultObservation(bounded);
  }

  async function finishAudit() {
    if (finishingRef.current) return;
    if (failedSaveRequestRef.current) {
      const message = 'Nie mozna zakonczyc H2: poprzedni zapis nie powiodl sie. Ponow zapis i sprobuj ponownie.';
      setFailure(message);
      onStatus(message);
      return;
    }
    finishingRef.current = true;
    setFinishing(true);
    const finalRequest: ReanchorSaveRequest = {
      updates: [],
      telemetryEvents: [event('session_finished')],
    };
    try {
      await saveQueueRef.current.finalize(
        async () => {
          if (failedSaveRequestRef.current) {
            throw new Error(
              'Nie mozna zakonczyc H2: oczekujacy autosave nie powiodl sie.',
            );
          }
          return performSave(finalRequest);
        },
        async (finalStore) => {
          setSeedStore(finalStore);
          setDecisions(initialIdentityAuditDecisionMap(finalStore.decisions));
          if (onFinished) {
            onStatus('H2 zakonczony. Tworze finalny raport benchmarku...');
            await onFinished();
          }
        },
      );
      setOpen(false);
    } catch (error) {
      const message = errorMessage(error);
      setFailure(message);
      onStatus(message);
    } finally {
      finishingRef.current = false;
      setFinishing(false);
    }
  }

  async function clearSelectedDecision() {
    if (
      finishingRef.current
      || !selectedObservationKey
      || !decisions[selectedObservationKey]
    ) return;
    const request: ReanchorSaveRequest = {
      updates: [initialIdentityAuditClearUpdate(selectedObservationKey)],
      telemetryEvents: [event('action', selectedObservationKey)],
    };
    try {
      await saveQueueRef.current.enqueue(() => performSave(request));
    } catch {
      // performSave keeps the modal open and the save error visible.
    }
  }

  if (loading && !document) {
    return (
      <section className='initial-identity-audit-panel'>
        <div>
          <h3>Re-anchor drugiej polowy</h3>
          <p className='muted'>Sprawdzam, czy jest potrzebny...</p>
        </div>
      </section>
    );
  }

  if (!document || failure && !open) {
    return (
      <section className='initial-identity-audit-panel'>
        <div>
          <h3>Re-anchor drugiej polowy</h3>
          <p className='error'>{failure ?? 'Nie udalo sie odczytac audytu.'}</p>
        </div>
      </section>
    );
  }

  return (
    <section className='initial-identity-audit-panel'>
      <div>
        <h3>Re-anchor drugiej polowy</h3>
        <p className='muted'>
          {statusLabel(document)}
          {benchmarkState ? ` Stan benchmarku: ${benchmarkState}.` : ''}
        </p>
      </div>
      {document.status === 'ready' && (
        <button type='button' onClick={() => void openAudit()} disabled={loading}>
          {loading ? 'Otwieram...' : 'Potwierdz H2'}
        </button>
      )}

      {open && frame && document.video && (
        <div className='initial-identity-audit-modal' role='dialog' aria-modal='true'>
          <div className='initial-identity-audit-shell'>
            <header className='initial-identity-audit-header'>
              <div>
                <h2>Potwierdzenia drugiej polowy</h2>
                <span className='chip'>
                  {saving ? 'Zapisuje...' : 'Maksymalnie 3 klatki'}
                </span>
              </div>
              <button
                type='button'
                onClick={() => void finishAudit()}
                disabled={saving || finishing || Boolean(failedSaveRequestRef.current)}
              >
                {finishing ? 'Koncze...' : 'Zakoncz'}
              </button>
            </header>

            {failure && (
              <div>
                <p className='error'>{failure}</p>
                {failedSaveRequestRef.current && (
                  <button
                    type='button'
                    disabled={saving || finishing}
                    onClick={() => void retryFailedSave()}
                  >
                    Ponow zapis
                  </button>
                )}
              </div>
            )}

            <div className='initial-identity-audit-progress'>
              <progress value={frameIndex + 1} max={document.frames.length} />
              <span>
                Klatka {frameIndex + 1}/{document.frames.length}
                {' · '}
                Potwierdzenia {seedStore?.decisions.length ?? 0}
                {seedStore?.operator_budget
                  ? ` · Aktywne ${seedStore.operator_budget.active_decisions}/${seedStore.operator_budget.limit}`
                  : maximumConfirmations !== undefined
                    ? ` · Limit aktywnych ${maximumConfirmations}`
                    : ''}
              </span>
            </div>

            <main className='initial-identity-audit-main'>
              <div className='initial-identity-audit-context second-half'>
                <div
                  className='second-half-team-legend'
                  aria-label='Legenda automatycznego przypisania do drużyny'
                >
                  <span className='team-a'>Team A</span>
                  <span className='team-b'>Team B</span>
                  <span className='team-unknown'>Nieznany team</span>
                  <small>Imię z „?” jest hipotezą do potwierdzenia.</small>
                </div>
                <div
                  className='initial-identity-audit-frame'
                  style={{ aspectRatio: `${document.video.width} / ${document.video.height}` }}
                >
                  <img
                    src={artifactUrl(match.id, frame.full_frame_artifact)}
                    alt={`Druga polowa, klatka ${frameIndex + 1}`}
                  />
                  {frame.observations.map((observation, index) => {
                    const selected = observation.observation_key === selectedObservationKey;
                    const decided = decisions[observation.observation_key];
                    const visibleSuggestion = secondHalfVisibleSuggestion(
                      observation,
                    );
                    return (
                      <button
                        type='button'
                        key={observation.observation_key}
                        className={[
                          'initial-identity-observation-box',
                          secondHalfTeamClass(observation.team_label),
                          selected ? 'selected' : '',
                          decided ? 'decided' : '',
                        ].filter(Boolean).join(' ')}
                        style={observationBoxStyle(observation, document.video!)}
                        onClick={() => {
                          setSelectedObservationKey(observation.observation_key);
                          setShowRoster(false);
                        }}
                        aria-label={[
                          `BBox ${index + 1}`,
                          observation.team_label === 'U'
                            ? 'nieznany team'
                            : `Team ${observation.team_label}`,
                          visibleSuggestion
                            ? `sugestia ${visibleSuggestion.player_name}`
                            : 'bez sugestii imiennej',
                        ].join(', ')}
                        aria-pressed={selected}
                      >
                        <span>
                          {secondHalfObservationLabel(
                            observation,
                            index + 1,
                            Boolean(decided),
                          )}
                        </span>
                      </button>
                    );
                  })}
                </div>
                <div className='initial-identity-audit-navigation'>
                  <button
                    type='button'
                    onClick={() => moveFrame(frameIndex - 1)}
                    disabled={frameIndex === 0}
                  >
                    Poprzednia
                  </button>
                  <button
                    type='button'
                    onClick={() => moveFrame(frameIndex + 1)}
                    disabled={frameIndex === document.frames.length - 1}
                  >
                    Nastepna
                  </button>
                </div>
              </div>

              <aside className='initial-identity-audit-actions'>
                <div className='initial-identity-audit-crop'>
                  <IdentityAuditObservationPreview
                    observation={selectedObservation}
                    video={document.video!}
                    frameArtifactUrl={artifactUrl(match.id, frame.full_frame_artifact)}
                    emptyLabel='Kliknij zawodnika.'
                  />
                </div>

                {selectedObservation && (
                  <div className='second-half-detection-summary'>
                    <strong>
                      Automatyczny team:{' '}
                      {selectedObservation.team_label === 'U'
                        ? 'nieznany'
                        : `Team ${selectedObservation.team_label}`}
                    </strong>
                    {selectedVisibleSuggestion ? (
                      <>
                        <span>
                          Hipoteza imienna:{' '}
                          <strong>
                            {selectedVisibleSuggestion.player_name}
                          </strong>
                        </span>
                        <small>
                          Źródło:{' '}
                          {secondHalfSuggestionSourceLabel(
                            selectedVisibleSuggestion.suggestion_source,
                          )}
                          . To nie jest pewne rozpoznanie — potwierdź albo wybierz
                          właściwego zawodnika.
                        </small>
                      </>
                    ) : (
                      <span>Brak sugestii imiennej dla tego bboxa.</span>
                    )}
                  </div>
                )}

                {currentSuggestion?.kind === 'player' && (
                  <div>
                    <p className='muted'>
                      {currentSuggestion.suggestionContext?.suggestion_source
                        === 'h1_safe_lineage'
                        ? 'Sugestia z bezpiecznej linii H1:'
                        : 'Sugestia ReID advisory:'}
                    </p>
                    <button
                      type='button'
                      className='primary'
                      disabled={saving || finishing || !canCreateConfirmation}
                      onClick={() => void applyAction(currentSuggestion)}
                    >
                      Potwierdz: {initialIdentityAuditActionLabel(currentSuggestion)}
                    </button>
                  </div>
                )}

                {(selectedObservation?.reid_suggestions?.length ?? 0) > 0 && (
                  <div>
                    <p className='muted'>ReID top-3 (tylko sugestie):</p>
                    {selectedObservation?.reid_suggestions?.map((suggestion) => {
                      const action = document
                        ? actionForSuggestion(
                            suggestion,
                            selectedObservation,
                            document,
                          )
                        : null;
                      return (
                        <button
                          type='button'
                          key={`${suggestion.player_id}-${suggestion.rank}`}
                          disabled={
                            !action
                            || saving
                            || finishing
                            || !canCreateConfirmation
                          }
                          onClick={() => {
                            if (action) void applyAction(action);
                          }}
                        >
                          {suggestion.rank}. {suggestion.player_name}
                        </button>
                      );
                    })}
                  </div>
                )}

                {selectedObservationKey && decisions[selectedObservationKey] && (
                  <button
                    type='button'
                    disabled={saving || finishing}
                    onClick={() => void clearSelectedDecision()}
                  >
                    Usun potwierdzenie
                  </button>
                )}

                <button
                  type='button'
                  disabled={!selectedObservation || saving || finishing}
                  onClick={() => setShowRoster((current) => !current)}
                >
                  {showRoster ? 'Ukryj sklad' : 'Inny zawodnik'}
                </button>

                {showRoster && (
                  <div className='initial-identity-audit-roster'>
                    {document.roster.map((team) => (
                      <div key={`${team.team_label}-${team.team_name}`}>
                        <h3>{team.team_name}</h3>
                        <div className='initial-identity-audit-action-grid'>
                          {team.players.map((player) => {
                            const action = initialIdentityAuditPlayerAction(player, team);
                            return (
                              <button
                                type='button'
                                key={player.player_id}
                                disabled={saving || finishing || !canCreateConfirmation}
                                onClick={() => void applyAction(action)}
                              >
                                {initialIdentityAuditActionLabel(action)}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                <div className='initial-identity-audit-generic-actions'>
                  <button
                    type='button'
                    disabled={!selectedObservation || saving || finishing || !canCreateConfirmation}
                    onClick={() => void applyAction({ kind: 'false_detection' })}
                  >
                    Cien / falszywa detekcja
                  </button>
                  {selectedObservation?.team_label !== 'A' && (
                    <button
                      type='button'
                      disabled={saving || finishing || !canCreateConfirmation}
                      onClick={() => void applyAction({
                        kind: 'team_unknown',
                        teamLabel: 'A',
                      })}
                    >
                      Team A
                    </button>
                  )}
                  {selectedObservation?.team_label !== 'B' && (
                    <button
                      type='button'
                      disabled={saving || finishing || !canCreateConfirmation}
                      onClick={() => void applyAction({
                        kind: 'team_unknown',
                        teamLabel: 'B',
                      })}
                    >
                      Team B
                    </button>
                  )}
                  <button
                    type='button'
                    disabled={!selectedObservation || saving || finishing}
                    onClick={() => void applyAction({ kind: 'skip' })}
                  >
                    Pomin / nie wiem
                  </button>
                </div>
              </aside>
            </main>
          </div>
        </div>
      )}
    </section>
  );
}
