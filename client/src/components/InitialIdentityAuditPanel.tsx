import { useEffect, useMemo, useRef, useState } from 'react';

import {
  artifactUrl,
  getInitialIdentityAudit,
  getInitialIdentityAuditSeeds,
  saveInitialIdentityAuditSeeds,
} from '../api';
import { errorMessage } from '../lib/helpers';
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
  initialIdentityAuditDecisionMap,
  initialIdentityAuditPlayerAction,
  initialIdentityAuditSeedUpdate,
  observationBoxStyle,
  observationCropLayout,
  type InitialIdentityAuditAction,
  type InitialIdentityAuditDecision,
} from '../utils/initialIdentityAudit';

interface InitialIdentityAuditPanelProps {
  match: Match;
  onStatus: (message: string) => void;
}

export function InitialIdentityAuditPanel({
  match,
  onStatus,
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
  const [saveError, setSaveError] = useState<string | null>(null);
  const saveQueueRef = useRef<Promise<void>>(Promise.resolve());
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
    setSaveError(null);
    saveQueueRef.current = Promise.resolve();
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

  function enqueueSave(
    updates: InitialIdentityAuditSeedUpdate[],
    telemetryEvents: InitialIdentityAuditTelemetryEvent[],
  ): Promise<void> {
    const requestedMatchId = match.id;
    pendingSavesRef.current += 1;
    setSaving(true);
    setSaveError(null);

    const operation = saveQueueRef.current
      .catch(() => undefined)
      .then(async () => {
        const nextStore = await saveInitialIdentityAuditSeeds(
          requestedMatchId,
          updates,
          telemetryEvents,
        );
        if (activeMatchIdRef.current === requestedMatchId) {
          setSeedStore(nextStore);
          setDecisions(initialIdentityAuditDecisionMap(nextStore.decisions));
        }
      })
      .catch(async (error: unknown) => {
        if (activeMatchIdRef.current !== requestedMatchId) return;
        const message = errorMessage(error);
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
      })
      .finally(() => {
        pendingSavesRef.current = Math.max(0, pendingSavesRef.current - 1);
        if (
          activeMatchIdRef.current === requestedMatchId
          && pendingSavesRef.current === 0
        ) {
          setSaving(false);
        }
      });
    saveQueueRef.current = operation;
    return operation;
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
      setSaveError(
        nextStore.status === 'stale'
          ? 'Zapisane decyzje sa nieaktualne. Audyt zostal otwarty bez nich.'
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
      void enqueueSave([], [
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
    const decision: InitialIdentityAuditDecision = {
      ...action,
      observationKey: observation.observation_key,
    };
    setDecisions((current) => ({
      ...current,
      [observation.observation_key]: decision,
    }));
    setSelectedObservationKey(observation.observation_key);
    void enqueueSave(
      [initialIdentityAuditSeedUpdate(decision)],
      [telemetryEvent('action', {
        audit_frame_key: frame?.audit_frame_key,
        observation_key: observation.observation_key,
      })],
    );
  }

  function chooseAction(action: InitialIdentityAuditAction) {
    if (selectedObservation) {
      applyAction(selectedObservation, action);
      setArmedAction(null);
      return;
    }
    setArmedAction(action);
  }

  function chooseObservation(observation: InitialIdentityAuditObservation) {
    setSelectedObservationKey(observation.observation_key);
    void enqueueSave([], [
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
    if (!document) return;
    const boundedIndex = Math.min(
      document.frames.length - 1,
      Math.max(0, nextIndex),
    );
    setFrameIndex(boundedIndex);
    setSelectedObservationKey(null);
    setArmedAction(null);
    const nextFrame = document.frames[boundedIndex];
    if (nextFrame) {
      void enqueueSave([], [
        telemetryEvent('frame_shown', {
          audit_frame_key: nextFrame.audit_frame_key,
        }),
      ]);
    }
  }

  async function finishAudit() {
    await enqueueSave([], [telemetryEvent('session_finished')]);
    setOpen(false);
    onStatus(
      `IA2 zapisany: ${Object.keys(decisions).length} decyzji operatora.`,
    );
  }

  const selectedDecision = selectedObservationKey
    ? decisions[selectedObservationKey]
    : undefined;

  return (
    <section className='initial-identity-audit-panel'>
      <div>
        <h3>Szybki audyt tozsamosci</h3>
        <p className='muted'>
          Kilka najlepszych klatek przed pelnym review. Wybierz tylko pewne osoby;
          pozostale mozesz pominac.
        </p>
      </div>
      <button type='button' onClick={openAudit} disabled={loading}>
        {loading ? 'Przygotowuje...' : 'Otworz szybki audyt'}
      </button>

      {open && document && frame && (
        <div className='initial-identity-audit-modal' role='dialog' aria-modal='true'>
          <div className='initial-identity-audit-shell'>
            <header className='initial-identity-audit-header'>
              <div>
                <h2>Szybki audyt tozsamosci</h2>
                <span className='chip'>
                  {saving
                    ? 'Zapisuje...'
                    : saveError
                      ? 'Blad zapisu'
                      : seedStore?.status === 'fresh'
                        ? 'Zapis automatyczny'
                        : 'Nowy audyt'}
                </span>
              </div>
              <button
                type='button'
                onClick={() => void finishAudit()}
                disabled={saving}
              >
                Zakoncz audyt
              </button>
            </header>

            {saveError && <p className='error'>{saveError}</p>}

            <div className='initial-identity-audit-progress'>
              <progress value={frameIndex + 1} max={document.frames.length} />
              <span>
                Klatka {frameIndex + 1}/{document.frames.length}
                {' · '}
                Decyzje {Object.keys(decisions).length}
              </span>
            </div>

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
                  {selectedObservation ? (() => {
                    const crop = observationCropLayout(selectedObservation, document.video);
                    return (
                      <div style={{ aspectRatio: crop.aspectRatio }}>
                        <img
                          src={artifactUrl(match.id, frame.full_frame_artifact)}
                          alt='Powiekszenie wybranego zawodnika'
                          style={crop.imageStyle}
                        />
                      </div>
                    );
                  })() : (
                    <p className='muted'>Kliknij zawodnika na pelnej klatce.</p>
                  )}
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
                    onClick={() => chooseAction({ kind: 'team_unknown', teamLabel: 'A' })}
                  >
                    Team A - nieznany
                  </button>
                  <button
                    type='button'
                    onClick={() => chooseAction({ kind: 'team_unknown', teamLabel: 'B' })}
                  >
                    Team B - nieznany
                  </button>
                  <button type='button' onClick={() => chooseAction({ kind: 'referee' })}>
                    Sedzia
                  </button>
                  <button type='button' onClick={() => chooseAction({ kind: 'false_detection' })}>
                    Falszywa detekcja
                  </button>
                  <button type='button' onClick={() => chooseAction({ kind: 'skip' })}>
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
