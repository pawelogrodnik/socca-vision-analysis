import { useEffect, useMemo, useRef, useState } from 'react';

import {
  artifactUrl,
  getInitialIdentityAudit,
  getInitialIdentityAuditSeeds,
  saveInitialIdentityAuditSeeds,
} from '../api';
import { errorMessage } from '../lib/helpers';
import { IdentityAuditObservationPreview } from './IdentityAuditObservationPreview';
import type {
  InitialIdentityAuditDocument,
  InitialIdentityAuditObservation,
  InitialIdentityAuditSeedStoreDocument,
  InitialIdentityAuditSeedUpdate,
  InitialIdentityAuditTelemetryEvent,
  Match,
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
import { initialAuditIdentityWorkIsComplete } from '../utils/initialIdentityAuditWorkflow';

interface InitialIdentityAuditPanelProps {
  match: Match;
  onStatus: (message: string) => void;
  maximumActions?: number;
  benchmarkState?: string;
  onFinished?: () => Promise<void>;
}

type AuditSaveRequest = {
  updates: InitialIdentityAuditSeedUpdate[];
  telemetryEvents: InitialIdentityAuditTelemetryEvent[];
};

export function InitialIdentityAuditPanel({
  match,
  onStatus,
  maximumActions,
  benchmarkState,
  onFinished,
}: InitialIdentityAuditPanelProps) {
  const [document, setDocument] = useState<InitialIdentityAuditDocument | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [frameIndex, setFrameIndex] = useState(0);
  const [selectedObservationKey, setSelectedObservationKey] = useState<string | null>(null);
  const [armedAction, setArmedAction] = useState<InitialIdentityAuditAction | null>(null);
  const [decisions, setDecisions] = useState<Record<string, InitialIdentityAuditDecision>>({});
  const [seedStore, setSeedStore] = useState<InitialIdentityAuditSeedStoreDocument | null>(null);
  const [saving, setSaving] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [auditIdentityWorkComplete, setAuditIdentityWorkComplete] = useState(false);
  const saveQueueRef = useRef(
    new RequiredFinalSaveQueue<InitialIdentityAuditSeedStoreDocument>(),
  );
  const failedSaveRequestRef = useRef<AuditSaveRequest | null>(null);
  const finishingRef = useRef(false);
  const pendingSavesRef = useRef(0);
  const sessionIdRef = useRef('');
  const lastActivityAtRef = useRef(0);
  const activeMatchIdRef = useRef(match.id);

  const frame = document?.frames[frameIndex] ?? null;
  const selectedObservation = useMemo(
    () => frame?.observations.find(
      (observation) => observation.observation_key === selectedObservationKey,
    ) ?? null,
    [frame, selectedObservationKey],
  );

  useEffect(() => {
    activeMatchIdRef.current = match.id;
    setDocument(null);
    setOpen(false);
    setFrameIndex(0);
    setSelectedObservationKey(null);
    setArmedAction(null);
    setDecisions({});
    setSeedStore(null);
    setSaving(false);
    setFinishing(false);
    setSaveError(null);
    setAuditIdentityWorkComplete(false);
    saveQueueRef.current = (
      new RequiredFinalSaveQueue<InitialIdentityAuditSeedStoreDocument>()
    );
    failedSaveRequestRef.current = null;
    finishingRef.current = false;
    pendingSavesRef.current = 0;
    sessionIdRef.current = '';
    lastActivityAtRef.current = 0;
  }, [match.id]);

  function telemetryEvent(
    eventType: InitialIdentityAuditTelemetryEvent['event_type'],
    values: Pick<
      InitialIdentityAuditTelemetryEvent,
      'audit_frame_key' | 'observation_key'
    > = {},
  ): InitialIdentityAuditTelemetryEvent {
    const now = Date.now();
    const activeDeltaSeconds = lastActivityAtRef.current > 0
      ? Math.min(30, Math.max(0, (now - lastActivityAtRef.current) / 1000))
      : 0;
    lastActivityAtRef.current = now;
    return {
      event_id: createInitialIdentityAuditEventId(),
      session_id: sessionIdRef.current,
      event_type: eventType,
      active_delta_seconds: activeDeltaSeconds,
      occurred_at: new Date(now).toISOString(),
      ...values,
    };
  }

  async function performSave(
    request: AuditSaveRequest,
  ): Promise<InitialIdentityAuditSeedStoreDocument> {
    const requestedMatchId = match.id;
    pendingSavesRef.current += 1;
    setSaving(true);
    try {
      const nextStore = await saveInitialIdentityAuditSeeds(
        requestedMatchId,
        request.updates,
        request.telemetryEvents,
      );
      if (activeMatchIdRef.current === requestedMatchId) {
        setSeedStore(nextStore);
        setDecisions(initialIdentityAuditDecisionMap(nextStore.decisions));
        setAuditIdentityWorkComplete(
          initialAuditIdentityWorkIsComplete(nextStore.workflow),
        );
        if (
          failedSaveRequestRef.current === null
          || failedSaveRequestRef.current === request
        ) {
          failedSaveRequestRef.current = null;
          setSaveError(null);
        }
      }
      return nextStore;
    } catch (error) {
      if (activeMatchIdRef.current === requestedMatchId) {
        const message = errorMessage(error);
        failedSaveRequestRef.current ??= request;
        setSaveError(message);
        onStatus(message);
        try {
          const currentStore = await getInitialIdentityAuditSeeds(requestedMatchId);
          if (activeMatchIdRef.current === requestedMatchId) {
            setSeedStore(currentStore);
            setDecisions(initialIdentityAuditDecisionMap(currentStore.decisions));
          }
        } catch {
          // Keep the original save error visible. A later reopen retries loading.
        }
      }
      throw error;
    } finally {
      pendingSavesRef.current = Math.max(0, pendingSavesRef.current - 1);
      if (
        activeMatchIdRef.current === requestedMatchId
        && pendingSavesRef.current === 0
      ) {
        setSaving(false);
      }
    }
  }

  function enqueueBackgroundSave(
    updates: InitialIdentityAuditSeedUpdate[],
    telemetryEvents: InitialIdentityAuditTelemetryEvent[],
  ): void {
    const request = { updates, telemetryEvents };
    void saveQueueRef.current
      .enqueue(() => performSave(request))
      .catch(() => undefined);
  }

  async function retryFailedSave() {
    const request = failedSaveRequestRef.current;
    if (!request || finishingRef.current) return;
    try {
      await saveQueueRef.current.enqueue(() => performSave(request));
      onStatus('Zapis został ponowiony poprawnie. Możesz zakończyć audyt.');
    } catch {
      // performSave keeps the actionable error visible in the modal.
    }
  }

  async function openAudit() {
    setLoading(true);
    try {
      const [nextDocument, nextStore] = await Promise.all([
        document ?? getInitialIdentityAudit(match.id),
        getInitialIdentityAuditSeeds(match.id),
      ]);
      sessionIdRef.current = createInitialIdentityAuditEventId();
      lastActivityAtRef.current = Date.now();
      setDocument(nextDocument);
      setSeedStore(nextStore);
      setDecisions(initialIdentityAuditDecisionMap(nextStore.decisions));
      setAuditIdentityWorkComplete(
        initialAuditIdentityWorkIsComplete(nextStore.workflow),
      );
      setSaveError(
        nextStore.status === 'stale'
          ? 'Zapisane decyzje są nieaktualne. Audyt został otwarty bez nich.'
          : null,
      );
      setFrameIndex(0);
      setSelectedObservationKey(null);
      setOpen(true);
      onStatus(
        `Szybki audyt gotowy: ${nextDocument.frames.length} klatek, `
        + `${nextStore.decisions.length} zapisanych decyzji.`,
      );
      const firstFrame = nextDocument.frames[0];
      enqueueBackgroundSave([], [
        telemetryEvent('session_started'),
        ...(firstFrame
          ? [telemetryEvent('frame_shown', {
              audit_frame_key: firstFrame.audit_frame_key,
            })]
          : []),
      ]);
    } catch (error) {
      onStatus(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }

  function applyAction(
    observation: InitialIdentityAuditObservation,
    action: InitialIdentityAuditAction,
  ) {
    if (finishingRef.current || auditIdentityWorkComplete) return;
    const existingDecision = decisions[observation.observation_key];
    const budgetReached = seedStore?.operator_budget?.reached
      ?? (
        maximumActions !== undefined
        && Object.values(decisions).filter((decision) => decision.kind !== 'skip').length
          >= maximumActions
      );
    if (budgetReached && !existingDecision && action.kind !== 'skip') {
      onStatus('Osiągnięto limit aktywnych decyzji w szybkim audycie. Możesz zmienić lub usunąć istniejącą decyzję.');
      return;
    }
    const decision: InitialIdentityAuditDecision = {
      ...action,
      observationKey: observation.observation_key,
    };
    setDecisions((current) => ({
      ...current,
      [observation.observation_key]: decision,
    }));
    setSelectedObservationKey(observation.observation_key);
    enqueueBackgroundSave(
      [initialIdentityAuditSeedUpdate(decision)],
      [telemetryEvent('action', {
        audit_frame_key: frame?.audit_frame_key,
        observation_key: observation.observation_key,
      })],
    );
  }

  function chooseAction(action: InitialIdentityAuditAction) {
    if (auditIdentityWorkComplete) return;
    if (selectedObservation) {
      applyAction(selectedObservation, action);
      setArmedAction(null);
      return;
    }
    setArmedAction(action);
  }

  function chooseObservation(observation: InitialIdentityAuditObservation) {
    if (finishingRef.current) return;
    setSelectedObservationKey(observation.observation_key);
    enqueueBackgroundSave([], [
      telemetryEvent('crop_clicked', {
        audit_frame_key: frame?.audit_frame_key,
        observation_key: observation.observation_key,
      }),
    ]);
    if (armedAction) {
      applyAction(observation, armedAction);
      setArmedAction(null);
    }
  }

  function moveFrame(nextIndex: number) {
    if (!document || finishingRef.current) return;
    const boundedIndex = Math.min(
      document.frames.length - 1,
      Math.max(0, nextIndex),
    );
    setFrameIndex(boundedIndex);
    setSelectedObservationKey(null);
    setArmedAction(null);
    const nextFrame = document.frames[boundedIndex];
    if (nextFrame) {
      enqueueBackgroundSave([], [
        telemetryEvent('frame_shown', {
          audit_frame_key: nextFrame.audit_frame_key,
        }),
      ]);
    }
  }

  async function finishAudit() {
    if (finishingRef.current) return;
    if (failedSaveRequestRef.current || saveError) {
      const message = 'Nie można zakończyć audytu — poprzedni zapis nie powiódł się. Ponów zapis i spróbuj ponownie.';
      setSaveError(message);
      onStatus(message);
      return;
    }
    finishingRef.current = true;
    setFinishing(true);
    const finalRequest: AuditSaveRequest = {
      updates: [],
      telemetryEvents: [telemetryEvent('session_finished')],
    };
    try {
      await saveQueueRef.current.finalize(
        async () => {
          if (failedSaveRequestRef.current) {
            throw new Error(
              'Nie można zakończyć audytu — oczekujący zapis nie powiódł się.',
            );
          }
          return performSave(finalRequest);
        },
        async (finalStore) => {
      setSeedStore(finalStore);
      setDecisions(initialIdentityAuditDecisionMap(finalStore.decisions));
      setAuditIdentityWorkComplete(
        initialAuditIdentityWorkIsComplete(finalStore.workflow),
      );
          if (onFinished) {
            onStatus('Audyt zakończony. Sprawdzam, czy pozostały przypadki wymagające decyzji…');
            await onFinished();
          } else {
            onStatus(
              `Zapisano audyt: ${finalStore.decisions.length} decyzji.`,
            );
          }
        },
      );
      setOpen(false);
    } catch (error) {
      const message = errorMessage(error);
      setSaveError(message);
      onStatus(message);
    } finally {
      finishingRef.current = false;
      setFinishing(false);
    }
  }

  function clearSelectedDecision() {
    if (
      finishingRef.current
      || auditIdentityWorkComplete
      || !selectedObservationKey
      || !decisions[selectedObservationKey]
    ) return;
    setDecisions((current) => {
      const next = { ...current };
      delete next[selectedObservationKey];
      return next;
    });
    enqueueBackgroundSave(
      [initialIdentityAuditClearUpdate(selectedObservationKey)],
      [telemetryEvent('action', {
        audit_frame_key: frame?.audit_frame_key,
        observation_key: selectedObservationKey,
      })],
    );
  }

  const selectedDecision = selectedObservationKey
    ? decisions[selectedObservationKey]
    : undefined;
  const activeDecisionCount = Object.values(decisions).filter(
    (decision) => decision.kind !== 'skip',
  ).length;
  const actionBudgetReached = seedStore?.operator_budget?.reached
    ?? (
      maximumActions !== undefined
      && activeDecisionCount >= maximumActions
    );
  const canCreateActiveDecision = !actionBudgetReached || Boolean(selectedDecision);

  return (
    <section className='initial-identity-audit-panel'>
      <div>
        <h3>Szybki audyt tożsamości</h3>
        <p className='muted'>
          Kilka najlepszych klatek przed pełnym Review. Wybierz tylko pewne osoby;
          pozostałe możesz pominąć.
          {benchmarkState ? ` Stan benchmarku: ${benchmarkState}.` : ''}
        </p>
      </div>
      <button type='button' onClick={openAudit} disabled={loading}>
        {loading ? 'Przygotowuję…' : 'Otwórz szybki audyt'}
      </button>

      {open && document && frame && (
        <div className='initial-identity-audit-modal' role='dialog' aria-modal='true'>
          <div className='initial-identity-audit-shell'>
            <header className='initial-identity-audit-header'>
              <div>
                <h2>Szybki audyt tożsamości</h2>
                <span className='chip'>
                  {saving
                    ? 'Zapisuję…'
                    : saveError
                      ? 'Błąd zapisu'
                      : seedStore?.status === 'fresh'
                        ? 'Zapis automatyczny'
                        : 'Nowy audyt'}
                </span>
              </div>
              <button
                type='button'
                onClick={() => void finishAudit()}
                disabled={saving || finishing || Boolean(saveError)}
              >
                {finishing ? 'Kończę…' : 'Zakończ audyt'}
              </button>
            </header>

            {saveError && (
              <div>
                <p className='error'>{saveError}</p>
                <button
                  type='button'
                  disabled={saving || finishing}
                  onClick={() => void retryFailedSave()}
                >
                  Ponów zapis
                </button>
              </div>
            )}

            <div className='initial-identity-audit-progress'>
              <progress value={frameIndex + 1} max={document.frames.length} />
              <span>
                Klatka {frameIndex + 1}/{document.frames.length}
                {' · '}
                Decyzje {Object.keys(decisions).length}
                {seedStore?.operator_budget
                  ? ` · Aktywne ${seedStore.operator_budget.active_decisions}/${seedStore.operator_budget.limit}`
                  : maximumActions !== undefined
                    ? ` · Limit aktywnych ${maximumActions}`
                    : ''}
              </span>
            </div>

            {auditIdentityWorkComplete && (
              <p className='status'>Wymagany audyt jest zakończony. Możesz zamknąć sesję.</p>
            )}

            <main className='initial-identity-audit-main'>
              <div className='initial-identity-audit-context'>
                <div
                  className='initial-identity-audit-frame'
                  style={{ aspectRatio: `${document.video.width} / ${document.video.height}` }}
                >
                  <img
                    src={artifactUrl(match.id, frame.full_frame_artifact)}
                    alt={`Kontekst klatki ${frameIndex + 1}`}
                  />
                  {frame.observations.map((observation, index) => {
                    const decision = decisions[observation.observation_key];
                    const selected = observation.observation_key === selectedObservationKey;
                    return (
                      <button
                        type='button'
                        key={observation.observation_key}
                        className={[
                          'initial-identity-observation-box',
                          selected ? 'selected' : '',
                          decision ? 'decided' : '',
                        ].filter(Boolean).join(' ')}
                        style={observationBoxStyle(observation, document.video)}
                        onClick={() => chooseObservation(observation)}
                        aria-label={`Zawodnik ${index + 1}${decision ? `: ${initialIdentityAuditActionLabel(decision)}` : ''}`}
                        aria-pressed={selected}
                      >
                        <span>{decision ? 'OK' : index + 1}</span>
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
                    video={document.video}
                    frameArtifactUrl={artifactUrl(match.id, frame.full_frame_artifact)}
                    emptyLabel='Kliknij zawodnika na pelnej klatce.'
                  />
                </div>

                <div className='initial-identity-audit-current'>
                  <strong>
                    {selectedDecision
                      ? initialIdentityAuditActionLabel(selectedDecision)
                      : armedAction
                        ? `Wybrana akcja: ${initialIdentityAuditActionLabel(armedAction)}`
                        : 'Wybierz zawodnika lub akcje'}
                  </strong>
                </div>

                {selectedDecision && (
                  <button
                    type='button'
                    disabled={saving || finishing || auditIdentityWorkComplete}
                    onClick={clearSelectedDecision}
                  >
                    Usun decyzje dla tego bboxa
                  </button>
                )}

                <div className='initial-identity-audit-roster'>
                  {document.roster.map((team) => (
                    <div key={`${team.team_label}-${team.team_id ?? team.team_name}`}>
                      <h3>{team.team_name}</h3>
                      <div className='initial-identity-audit-action-grid'>
                        {team.players.map((player) => {
                          const action = initialIdentityAuditPlayerAction(player, team);
                          const active = armedAction?.kind === 'player'
                            && armedAction.playerId === player.player_id;
                          return (
                            <button
                              type='button'
                              key={player.player_id}
                              className={active ? 'active' : ''}
                              disabled={!canCreateActiveDecision || finishing || auditIdentityWorkComplete}
                              onClick={() => chooseAction(action)}
                              aria-pressed={active}
                            >
                              {initialIdentityAuditActionLabel(action)}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>

                <div className='initial-identity-audit-generic-actions'>
                  <button
                    type='button'
                    disabled={!canCreateActiveDecision || finishing || auditIdentityWorkComplete}
                    onClick={() => chooseAction({ kind: 'team_unknown', teamLabel: 'A' })}
                  >
                    Team A - nieznany
                  </button>
                  <button
                    type='button'
                    disabled={!canCreateActiveDecision || finishing || auditIdentityWorkComplete}
                    onClick={() => chooseAction({ kind: 'team_unknown', teamLabel: 'B' })}
                  >
                    Team B - nieznany
                  </button>
                  <button type='button' disabled={!canCreateActiveDecision || finishing || auditIdentityWorkComplete} onClick={() => chooseAction({ kind: 'referee' })}>
                    Sedzia
                  </button>
                  <button type='button' disabled={!canCreateActiveDecision || finishing || auditIdentityWorkComplete} onClick={() => chooseAction({ kind: 'false_detection' })}>
                    Falszywa detekcja
                  </button>
                  <button type='button' disabled={finishing || auditIdentityWorkComplete} onClick={() => chooseAction({ kind: 'skip' })}>
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
