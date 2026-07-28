import { useEffect, useMemo, useRef, useState } from 'react';

import {
  artifactUrl,
  getSecondHalfIdentityReanchor,
  getSecondHalfIdentityReanchorSeeds,
  saveSecondHalfIdentityReanchorSeeds,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type {
  InitialIdentityAuditObservation,
  InitialIdentityAuditSeedStoreDocument,
  InitialIdentityAuditTelemetryEvent,
  Match,
  SecondHalfIdentityReanchorDocument,
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

interface SecondHalfIdentityReanchorPanelProps {
  match: Match;
  onStatus: (message: string) => void;
}

function statusLabel(document: SecondHalfIdentityReanchorDocument): string {
  if (document.status === 'not_applicable') {
    return 'Nie dotyczy: mecz nie ma jawnie skonfigurowanej drugiej polowy.';
  }
  if (document.status === 'skipped_already_resolved') {
    return 'Pominiety: pierwsze potwierdzenia bezpiecznie pokrywaja druga polowe.';
  }
  return `Gotowy: ${document.frames.length} klatki, tylko szybkie potwierdzenia.`;
}

function suggestedAction(
  observation: InitialIdentityAuditObservation,
  document: SecondHalfIdentityReanchorDocument,
): InitialIdentityAuditAction | null {
  const suggestion = observation.suggested_player;
  if (!suggestion) return null;
  for (const team of document.roster) {
    const player = team.players.find(
      (candidate) => candidate.player_id === suggestion.player_id,
    );
    if (player) return initialIdentityAuditPlayerAction(player, team);
  }
  return null;
}

export function SecondHalfIdentityReanchorPanel({
  match,
  onStatus,
}: SecondHalfIdentityReanchorPanelProps) {
  const [document, setDocument] = useState<SecondHalfIdentityReanchorDocument | null>(null);
  const [seedStore, setSeedStore] = useState<InitialIdentityAuditSeedStoreDocument | null>(null);
  const [decisions, setDecisions] = useState<Record<string, InitialIdentityAuditDecision>>({});
  const [frameIndex, setFrameIndex] = useState(0);
  const [selectedObservationKey, setSelectedObservationKey] = useState<string | null>(null);
  const [showRoster, setShowRoster] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [open, setOpen] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const sessionIdRef = useRef('');

  const frame = document?.frames[frameIndex] ?? null;
  const selectedObservation = useMemo(
    () => frame?.observations.find(
      (observation) => observation.observation_key === selectedObservationKey,
    ) ?? null,
    [frame, selectedObservationKey],
  );
  const currentSuggestion = document && selectedObservation
    ? suggestedAction(selectedObservation, document)
    : null;

  useEffect(() => {
    let active = true;
    setLoading(true);
    setDocument(null);
    setSeedStore(null);
    setDecisions({});
    setOpen(false);
    setFailure(null);
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
      (observation) => Boolean(observation.suggested_player),
    );
    setSelectedObservationKey(
      suggestion?.observation_key
      ?? nextFrame?.observations[0]?.observation_key
      ?? null,
    );
    setShowRoster(false);
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
    if (!selectedObservation) return;
    const decision: InitialIdentityAuditDecision = {
      ...action,
      observationKey: selectedObservation.observation_key,
    };
    setSaving(true);
    setFailure(null);
    try {
      const nextStore = await saveSecondHalfIdentityReanchorSeeds(
        match.id,
        [initialIdentityAuditSeedUpdate(decision)],
        [event('action', selectedObservation.observation_key)],
      );
      setSeedStore(nextStore);
      setDecisions(initialIdentityAuditDecisionMap(nextStore.decisions));
    } catch (error) {
      const message = errorMessage(error);
      setFailure(message);
      onStatus(message);
    } finally {
      setSaving(false);
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
        <p className='muted'>{statusLabel(document)}</p>
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
              <button type='button' onClick={() => setOpen(false)} disabled={saving}>
                Zakoncz
              </button>
            </header>

            {failure && <p className='error'>{failure}</p>}

            <div className='initial-identity-audit-progress'>
              <progress value={frameIndex + 1} max={document.frames.length} />
              <span>
                Klatka {frameIndex + 1}/{document.frames.length}
                {' · '}
                Potwierdzenia {seedStore?.decisions.length ?? 0}
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
                    alt={`Druga polowa, klatka ${frameIndex + 1}`}
                  />
                  {frame.observations.map((observation, index) => {
                    const selected = observation.observation_key === selectedObservationKey;
                    const decided = decisions[observation.observation_key];
                    return (
                      <button
                        type='button'
                        key={observation.observation_key}
                        className={[
                          'initial-identity-observation-box',
                          selected ? 'selected' : '',
                          decided ? 'decided' : '',
                        ].filter(Boolean).join(' ')}
                        style={observationBoxStyle(observation, document.video!)}
                        onClick={() => {
                          setSelectedObservationKey(observation.observation_key);
                          setShowRoster(false);
                        }}
                        aria-label={`Zawodnik ${index + 1}`}
                        aria-pressed={selected}
                      >
                        <span>{decided ? 'OK' : index + 1}</span>
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
                    const crop = observationCropLayout(selectedObservation, document.video!);
                    return (
                      <div style={{ aspectRatio: crop.aspectRatio }}>
                        <img
                          src={artifactUrl(match.id, frame.full_frame_artifact)}
                          alt='Powiekszenie zawodnika'
                          style={crop.imageStyle}
                        />
                      </div>
                    );
                  })() : <p className='muted'>Kliknij zawodnika.</p>}
                </div>

                {currentSuggestion && (
                  <button
                    type='button'
                    className='primary'
                    disabled={saving}
                    onClick={() => void applyAction(currentSuggestion)}
                  >
                    Potwierdz: {initialIdentityAuditActionLabel(currentSuggestion)}
                  </button>
                )}

                <button
                  type='button'
                  disabled={!selectedObservation || saving}
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
                                disabled={saving}
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
                    disabled={!selectedObservation || saving}
                    onClick={() => void applyAction({ kind: 'team_unknown', teamLabel: 'B' })}
                  >
                    Team B
                  </button>
                  <button
                    type='button'
                    disabled={!selectedObservation || saving}
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
