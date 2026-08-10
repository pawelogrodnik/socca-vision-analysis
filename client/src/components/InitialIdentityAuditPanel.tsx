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
  canApplyInitialIdentityAuditAction,
  initialIdentityAuditActionLabel,
  initialIdentityAuditDecisionMap,
  initialIdentityAuditObservationBoxClassName,
  initialIdentityAuditPlayerAction,
  initialIdentityAuditPlayerUsedElsewhereInFrame,
  observationBoxStyle,
  type InitialIdentityAuditAction,
  type InitialIdentityAuditDecision,
} from '../utils/initialIdentityAudit';
import { RequiredFinalSaveQueue } from '../utils/requiredFinalSaveQueue';
import {
  InitialIdentityAuditFrameBatcher,
  canStageInitialAuditDecision,
  initialAuditBudgetReached,
} from '../utils/initialIdentityAuditFrameBatch';
import { initialAuditIdentityWorkIsComplete } from '../utils/initialIdentityAuditWorkflow';

interface InitialIdentityAuditPanelProps {
  match: Match;
  onStatus: (message: string) => void;
  maximumActions?: number;
  benchmarkState?: string;
  onFinished?: () => Promise<void>;
}

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
  const [transitionSaving, setTransitionSaving] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [auditIdentityWorkComplete, setAuditIdentityWorkComplete] = useState(false);
  const saveQueueRef = useRef(
    new RequiredFinalSaveQueue<InitialIdentityAuditSeedStoreDocument>(),
  );
  const frameBatcherRef = useRef(new InitialIdentityAuditFrameBatcher());
  const decisionsRef = useRef<Record<string, InitialIdentityAuditDecision>>({});
  const finishingRef = useRef(false);
  const transitionSavingRef = useRef(false);
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
  const currentFrameObservationKeys = useMemo(
    () => frame?.observations.map((observation) => observation.observation_key) ?? [],
    [frame],
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
    setTransitionSaving(false);
    setFinishing(false);
    setSaveError(null);
    setAuditIdentityWorkComplete(false);
    saveQueueRef.current = (
      new RequiredFinalSaveQueue<InitialIdentityAuditSeedStoreDocument>()
    );
    frameBatcherRef.current.reset();
    decisionsRef.current = {};
    finishingRef.current = false;
    transitionSavingRef.current = false;
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

  function setCurrentDecisions(next: Record<string, InitialIdentityAuditDecision>) {
    decisionsRef.current = next;
    setDecisions(next);
  }

  function applyServerStore(nextStore: InitialIdentityAuditSeedStoreDocument) {
    setSeedStore(nextStore);
    setCurrentDecisions(frameBatcherRef.current.mergeServerDecisions(
      initialIdentityAuditDecisionMap(nextStore.decisions),
    ));
    setAuditIdentityWorkComplete(
      initialAuditIdentityWorkIsComplete(nextStore.workflow),
    );
  }

  async function performSave(
    updates: InitialIdentityAuditSeedUpdate[],
    telemetryEvents: InitialIdentityAuditTelemetryEvent[],
    finalize = false,
  ): Promise<InitialIdentityAuditSeedStoreDocument> {
    const requestedMatchId = match.id;
    pendingSavesRef.current += 1;
    setSaving(true);
    try {
      const nextStore = await saveInitialIdentityAuditSeeds(
        requestedMatchId,
        updates,
        telemetryEvents,
        finalize,
      );
      if (activeMatchIdRef.current === requestedMatchId) {
        applyServerStore(nextStore);
        setSaveError(null);
      }
      return nextStore;
    } catch (error) {
      if (activeMatchIdRef.current === requestedMatchId) {
        const message = errorMessage(error);
        setSaveError(message);
        onStatus(message);
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

  async function flushPendingAuditChanges(
    additionalTelemetry: InitialIdentityAuditTelemetryEvent[] = [],
    finalize = false,
  ): Promise<InitialIdentityAuditSeedStoreDocument | null> {
    if (!frameBatcherRef.current.hasPendingChanges(additionalTelemetry)) return null;
    const save = async () => {
      const result = await frameBatcherRef.current.flush((batch) => (
        performSave(batch.updates, batch.telemetryEvents, finalize)
      ), additionalTelemetry);
      if (!result) throw new Error('Brak oczekujących zmian audytu.');
      return result;
    };
    return finalize
      ? saveQueueRef.current.finalize(
        async () => (await save()) as InitialIdentityAuditSeedStoreDocument,
        async () => undefined,
      )
      : saveQueueRef.current.enqueue(save);
  }

  async function retryFailedSave() {
    if (!saveError || finishingRef.current || transitionSavingRef.current) return;
    try {
      await flushPendingAuditChanges();
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
      frameBatcherRef.current.reset();
      setDocument(nextDocument);
      setSeedStore(nextStore);
      setCurrentDecisions(initialIdentityAuditDecisionMap(nextStore.decisions));
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
      void flushPendingAuditChanges([
        telemetryEvent('session_started'),
        ...(firstFrame
          ? [telemetryEvent('frame_shown', {
              audit_frame_key: firstFrame.audit_frame_key,
            })]
          : []),
      ]).catch(() => undefined);
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
    if (finishingRef.current || transitionSavingRef.current || auditIdentityWorkComplete) return;
    const currentDecisions = decisionsRef.current;
    if (!canApplyInitialIdentityAuditAction(
      currentFrameObservationKeys,
      currentDecisions,
      observation.observation_key,
      action,
    )) {
      onStatus('Ten zawodnik jest już przypisany do innego bboxa w tej klatce.');
      return;
    }
    const localBudgetLimit = maximumActions ?? seedStore?.operator_budget?.limit;
    const decision: InitialIdentityAuditDecision = {
      ...action,
      observationKey: observation.observation_key,
    };
    if (!canStageInitialAuditDecision(
      currentDecisions,
      observation.observation_key,
      decision,
      Boolean(seedStore?.operator_budget?.reached),
      localBudgetLimit,
    )) {
      onStatus('Osiągnięto limit aktywnych decyzji w szybkim audycie. Możesz zmienić lub usunąć istniejącą decyzję.');
      return;
    }
    setCurrentDecisions({
      ...currentDecisions,
      [observation.observation_key]: decision,
    });
    frameBatcherRef.current.stageDecision(decision);
    frameBatcherRef.current.recordTelemetry(telemetryEvent('action', {
      audit_frame_key: frame?.audit_frame_key,
      observation_key: observation.observation_key,
    }));
    setSelectedObservationKey(observation.observation_key);
  }

  function chooseAction(action: InitialIdentityAuditAction) {
    if (auditIdentityWorkComplete || transitionSavingRef.current) return;
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
    frameBatcherRef.current.recordTelemetry(telemetryEvent('crop_clicked', {
      audit_frame_key: frame?.audit_frame_key,
      observation_key: observation.observation_key,
    }));
    if (armedAction) {
      applyAction(observation, armedAction);
      setArmedAction(null);
    }
  }

  async function moveFrame(nextIndex: number) {
    if (!document || finishingRef.current || transitionSavingRef.current) return;
    const boundedIndex = Math.min(
      document.frames.length - 1,
      Math.max(0, nextIndex),
    );
    if (boundedIndex === frameIndex) return;
    transitionSavingRef.current = true;
    setTransitionSaving(true);
    try {
      const saved = await flushPendingAuditChanges();
      if (saved && initialAuditIdentityWorkIsComplete(saved.workflow)) return;
      setFrameIndex(boundedIndex);
      setSelectedObservationKey(null);
      setArmedAction(null);
      const nextFrame = document.frames[boundedIndex];
      if (nextFrame) {
        frameBatcherRef.current.recordTelemetry(telemetryEvent('frame_shown', {
          audit_frame_key: nextFrame.audit_frame_key,
        }));
      }
    } catch {
      // performSave keeps local decisions and the actionable error visible.
    } finally {
      transitionSavingRef.current = false;
      setTransitionSaving(false);
    }
  }

  async function finishAudit() {
    if (finishingRef.current || transitionSavingRef.current) return;
    if (saveError) {
      const message = 'Nie można zakończyć audytu — poprzedni zapis nie powiódł się. Ponów zapis i spróbuj ponownie.';
      setSaveError(message);
      onStatus(message);
      return;
    }
    finishingRef.current = true;
    setFinishing(true);
    try {
      const finalStore = await flushPendingAuditChanges(
        [telemetryEvent('session_finished')],
        true,
      );
      if (onFinished) {
        onStatus('Audyt zakończony. Sprawdzam, czy pozostały przypadki wymagające decyzji…');
        await onFinished();
      } else {
        onStatus(
          `Zapisano audyt: ${finalStore?.decisions.length ?? 0} decyzji.`,
        );
      }
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
      || transitionSavingRef.current
      || auditIdentityWorkComplete
      || !selectedObservationKey
      || !decisionsRef.current[selectedObservationKey]
    ) return;
    const next = { ...decisionsRef.current };
    delete next[selectedObservationKey];
    setCurrentDecisions(next);
    frameBatcherRef.current.stageClear(selectedObservationKey);
    frameBatcherRef.current.recordTelemetry(telemetryEvent('action', {
      audit_frame_key: frame?.audit_frame_key,
      observation_key: selectedObservationKey,
    }));
  }

  const selectedDecision = selectedObservationKey
    ? decisions[selectedObservationKey]
    : undefined;
  const activeDecisionCount = Object.values(decisions).filter(
    (decision) => decision.kind !== 'skip',
  ).length;
  const localBudgetLimit = maximumActions ?? seedStore?.operator_budget?.limit;
  const actionBudgetReached = initialAuditBudgetReached(
    decisions,
    Boolean(seedStore?.operator_budget?.reached),
    localBudgetLimit,
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
                    ? transitionSaving
                      ? 'Zapisywanie klatki…'
                      : 'Zapisuję…'
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
                  ? ` · Aktywne ${activeDecisionCount}/${seedStore.operator_budget.limit}`
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
                  className='initial-identity-audit-team-legend'
                  aria-label='Legenda automatycznego przypisania do drużyny'
                >
                  <span className='team-a'>Team A</span>
                  <span className='team-b'>Team B</span>
                  <span className='team-unknown'>Nieznana drużyna</span>
                </div>
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
                        className={initialIdentityAuditObservationBoxClassName(
                          observation,
                          { selected, decided: Boolean(decision) },
                        )}
                        style={observationBoxStyle(observation, document.video)}
                        onClick={() => chooseObservation(observation)}
                        aria-label={[
                          `Zawodnik ${index + 1}`,
                          observation.team_label === 'U'
                            ? 'nieznana drużyna'
                            : `wykryto Team ${observation.team_label}`,
                          decision ? initialIdentityAuditActionLabel(decision) : '',
                        ].filter(Boolean).join(': ')}
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
                    disabled={frameIndex === 0 || transitionSaving || finishing}
                  >
                    Poprzednia
                  </button>
                  <button
                    type='button'
                    onClick={() => moveFrame(frameIndex + 1)}
                    disabled={frameIndex === document.frames.length - 1 || transitionSaving || finishing}
                  >
                    {transitionSaving ? 'Zapisywanie…' : 'Następna'}
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
                    disabled={transitionSaving || finishing || auditIdentityWorkComplete}
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
                          const usedElsewhere = initialIdentityAuditPlayerUsedElsewhereInFrame(
                            currentFrameObservationKeys,
                            decisions,
                            selectedObservationKey,
                            player.player_id,
                          );
                          return (
                            <button
                              type='button'
                              key={player.player_id}
                              className={active ? 'active' : ''}
                              disabled={usedElsewhere || !canCreateActiveDecision || transitionSaving || finishing || auditIdentityWorkComplete}
                              title={usedElsewhere ? 'Już przypisany w tej klatce' : undefined}
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
                    disabled={!canCreateActiveDecision || transitionSaving || finishing || auditIdentityWorkComplete}
                    onClick={() => chooseAction({ kind: 'team_unknown', teamLabel: 'A' })}
                  >
                    Team A - nieznany
                  </button>
                  <button
                    type='button'
                    disabled={!canCreateActiveDecision || transitionSaving || finishing || auditIdentityWorkComplete}
                    onClick={() => chooseAction({ kind: 'team_unknown', teamLabel: 'B' })}
                  >
                    Team B - nieznany
                  </button>
                  <button type='button' disabled={!canCreateActiveDecision || transitionSaving || finishing || auditIdentityWorkComplete} onClick={() => chooseAction({ kind: 'referee' })}>
                    Sędzia
                  </button>
                  <button type='button' disabled={!canCreateActiveDecision || transitionSaving || finishing || auditIdentityWorkComplete} onClick={() => chooseAction({ kind: 'false_detection' })}>
                    Fałszywa detekcja
                  </button>
                  <button type='button' disabled={transitionSaving || finishing || auditIdentityWorkComplete} onClick={() => chooseAction({ kind: 'skip' })}>
                    Pomiń / nie wiem
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
