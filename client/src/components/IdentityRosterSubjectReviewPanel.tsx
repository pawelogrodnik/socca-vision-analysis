import { useEffect, useMemo, useRef, useState } from 'react';
import {
  artifactUrl,
  getIdentityRosterSubjectReview,
  saveIdentityRosterSubjectReview,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type {
  IdentityRosterSubjectDecision,
  IdentityRosterSubjectJerseyNumberAnnotation,
  IdentityRosterSubjectReviewCard,
  IdentityRosterSubjectReviewDocument,
  IdentityRosterSubjectTelemetryEvent,
  Match,
} from '../types';
import {
  isActionableSubjectReviewCard,
  nearestPendingCardIndex,
  subjectRosterOptions,
  subjectDecisionLabel,
  subjectReviewStatusLabel,
  visibleSubjectReviewCards,
  type SubjectReviewFilter,
  type SubjectTeamFilter,
} from '../utils/identityRosterSubjectReview';

interface IdentityRosterSubjectReviewPanelProps {
  match: Match;
  onStatus: (message: string) => void;
}

type Availability = 'loading' | 'available' | 'missing' | 'error';

function frameRange(card: IdentityRosterSubjectReviewCard): string {
  if (typeof card.start_frame !== 'number' || typeof card.end_frame !== 'number') return 'frames n/a';
  return `f${card.start_frame}-${card.end_frame}`;
}

function confidenceLabel(value: number | null | undefined): string {
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : 'n/a';
}

function playerOptionLabel(name: string | null | undefined, team: string | null | undefined): string {
  return `${name || 'Bez nazwy'}${team ? ` - Team ${team}` : ''}`;
}

export function IdentityRosterSubjectReviewPanel({
  match,
  onStatus,
}: IdentityRosterSubjectReviewPanelProps) {
  const [document, setDocument] = useState<IdentityRosterSubjectReviewDocument | null>(null);
  const [availability, setAvailability] = useState<Availability>('loading');
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [reviewFilter, setReviewFilter] = useState<SubjectReviewFilter>('pending');
  const [teamFilter, setTeamFilter] = useState<SubjectTeamFilter>('A');
  const [cardIndex, setCardIndex] = useState(0);
  const [selectedPlayerId, setSelectedPlayerId] = useState('');
  const [comment, setComment] = useState('');
  const [cropAnnotations, setCropAnnotations] = useState<Record<string, IdentityRosterSubjectJerseyNumberAnnotation>>({});
  const sessionIdRef = useRef('');
  const lastActivityAtRef = useRef<number | null>(null);
  const saveQueueRef = useRef<Promise<void>>(Promise.resolve());

  function queuedSave(
    updates: Parameters<typeof saveIdentityRosterSubjectReview>[1],
    telemetryEvents: Parameters<typeof saveIdentityRosterSubjectReview>[2] = [],
  ): Promise<IdentityRosterSubjectReviewDocument> {
    const request = saveQueueRef.current
      .catch(() => undefined)
      .then(() => saveIdentityRosterSubjectReview(match.id, updates, telemetryEvents));
    saveQueueRef.current = request.then(() => undefined, () => undefined);
    return request;
  }

  function telemetryEvent(
    eventType: IdentityRosterSubjectTelemetryEvent['event_type'],
    reviewCardKey?: string | null,
  ): IdentityRosterSubjectTelemetryEvent {
    const now = Date.now();
    const previous = lastActivityAtRef.current;
    const activeDelta = previous === null ? 0 : Math.min(30, Math.max(0, (now - previous) / 1000));
    lastActivityAtRef.current = now;
    return {
      event_id: crypto.randomUUID(),
      session_id: sessionIdRef.current || 'unknown-session',
      event_type: eventType,
      occurred_at: new Date(now).toISOString(),
      active_delta_seconds: activeDelta,
      review_card_key: reviewCardKey || null,
    };
  }

  function startReview() {
    sessionIdRef.current = crypto.randomUUID();
    lastActivityAtRef.current = Date.now();
    setOpen(true);
    void queuedSave([], [telemetryEvent('session_started')]);
  }

  function closeReview() {
    const event = telemetryEvent('session_completed', currentCard?.review_card_key);
    setOpen(false);
    void queuedSave([], [event]);
  }

  const cards = useMemo(
    () => document ? visibleSubjectReviewCards(document, reviewFilter, teamFilter) : [],
    [document, reviewFilter, teamFilter],
  );
  const currentCard = cards[cardIndex] || cards[0] || null;
  const currentRosterOptions = currentCard ? subjectRosterOptions(currentCard) : [];
  const reviewCounts = useMemo(() => {
    const all = document?.cards || [];
    const actionable = all.filter(isActionableSubjectReviewCard);
    return {
      actionableTotal: actionable.length,
      actionableReviewed: actionable.filter((card) => Boolean(card.operator_decision)).length,
      pending: actionable.filter((card) => !card.operator_decision).length,
      reviewed: all.filter((card) => Boolean(card.operator_decision)).length,
      all: all.length,
    };
  }, [document]);
  const counts = useMemo(() => {
    const all = document?.cards || [];
    return {
      all: all.length,
      A: all.filter((card) => card.team_label === 'A').length,
      B: all.filter((card) => card.team_label === 'B').length,
      U: all.filter((card) => !card.team_label || card.team_label === 'U').length,
    };
  }, [document]);
  const reviewedCardsInSelectedTeam = useMemo(() => {
    if (!document || teamFilter === 'all') return 0;
    return document.cards.filter((card) => (
      (card.team_label || 'U') === teamFilter && Boolean(card.operator_decision)
    )).length;
  }, [document, teamFilter]);
  const hasOnlyReviewedCardsInSelectedTeam = reviewFilter === 'pending'
    && teamFilter !== 'all'
    && cards.length === 0
    && reviewedCardsInSelectedTeam > 0;

  async function load(showStatus: boolean) {
    setAvailability('loading');
    try {
      const next = await getIdentityRosterSubjectReview(match.id);
      setDocument(next);
      setAvailability('available');
      if (showStatus) onStatus(`Zaladowano ${next.cards.length} kart whole-subject review.`);
    } catch (error) {
      const message = errorMessage(error);
      setDocument(null);
      setAvailability(message.startsWith('404:') ? 'missing' : 'error');
      if (showStatus && !message.startsWith('404:')) {
        onStatus(`Nie udalo sie zaladowac whole-subject review: ${message}`);
      }
    }
  }

  async function saveDecision(decision: IdentityRosterSubjectDecision | 'clear_decision') {
    if (!currentCard) return;
    const playerId = decision === 'confirm_recommended_player'
      ? currentCard.recommended_player?.player_id || null
      : decision === 'assign_roster_player'
        ? selectedPlayerId || null
        : null;
    if (decision === 'assign_roster_player' && !playerId) {
      onStatus('Wybierz zawodnika z rosteru.');
      return;
    }
    setSaving(true);
    try {
      const next = await queuedSave([
        {
          update_id: crypto.randomUUID(),
          review_card_key: currentCard.review_card_key,
          decision,
          player_id: playerId,
          comment: comment.trim() || null,
        },
        ...currentCard.visual_evidence.anchor_crops.map((crop) => ({
          update_id: crypto.randomUUID(),
          review_card_key: currentCard.review_card_key,
          anchor_crop_id: crop.anchor_crop_id,
          jersey_number_annotation: cropAnnotations[crop.anchor_crop_id] || {},
        })),
      ], [telemetryEvent('activity', currentCard.review_card_key)]);
      const nextCards = visibleSubjectReviewCards(next, reviewFilter, teamFilter);
      const currentInNext = nextCards.findIndex((card) => card.review_card_key === currentCard.review_card_key);
      setDocument(next);
      setCardIndex(
        reviewFilter === 'pending'
          ? Math.min(cardIndex, Math.max(0, nextCards.length - 1))
          : nearestPendingCardIndex(nextCards, currentInNext >= 0 ? currentInNext : cardIndex),
      );
      onStatus(decision === 'clear_decision' ? 'Usunieto decyzje shadow.' : 'Zapisano decyzje shadow.');
    } catch (error) {
      onStatus(`Nie udalo sie zapisac decyzji: ${errorMessage(error)}`);
    } finally {
      setSaving(false);
    }
  }

  async function saveCropAnnotations() {
    if (!currentCard || currentCard.visual_evidence.anchor_crops.length === 0) return;
    setSaving(true);
    try {
      const next = await queuedSave(
        currentCard.visual_evidence.anchor_crops.map((crop) => ({
          update_id: crypto.randomUUID(),
          review_card_key: currentCard.review_card_key,
          anchor_crop_id: crop.anchor_crop_id,
          jersey_number_annotation: cropAnnotations[crop.anchor_crop_id] || {},
        })),
        [telemetryEvent('activity', currentCard.review_card_key)],
      );
      setDocument(next);
      onStatus('Zapisano shadow ocene cropow. Decyzja rosteru bez zmian.');
    } catch (error) {
      onStatus(`Nie udalo sie zapisac ocen cropow: ${errorMessage(error)}`);
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => {
    if (match.analysis_report?.status === 'completed') {
      void load(false);
    } else {
      setDocument(null);
      setAvailability('missing');
      setOpen(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id, match.analysis_report?.status]);

  useEffect(() => {
    setCardIndex(0);
  }, [reviewFilter, teamFilter]);

  useEffect(() => {
    const decision = currentCard?.operator_decision;
    setSelectedPlayerId(
      decision?.player_id || currentCard?.recommended_player?.player_id || currentRosterOptions[0]?.player_id || '',
    );
    setComment(decision?.comment || '');
  }, [currentCard?.review_card_key, currentCard?.operator_decision, currentRosterOptions]);

  useEffect(() => {
    setCropAnnotations(
      Object.fromEntries(
        (currentCard?.visual_evidence.anchor_crops || []).map((crop) => [
          crop.anchor_crop_id,
          crop.jersey_number_annotation || {},
        ]),
      ),
    );
  }, [currentCard?.review_card_key]);

  function updateCropAnnotation(
    anchorCropId: string,
    field: keyof IdentityRosterSubjectJerseyNumberAnnotation,
    value: string | number | null,
  ) {
    setCropAnnotations((current) => ({
      ...current,
      [anchorCropId]: {
        ...current[anchorCropId],
        [field]: value,
      },
    }));
  }

  useEffect(() => {
    if (!open || !currentCard) return;
    void queuedSave(
      [],
      [telemetryEvent('card_opened', currentCard.review_card_key)],
    );
    // Telemetry must follow card navigation, not unrelated card object changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, currentCard?.review_card_key, match.id]);

  useEffect(() => {
    if (!open) return undefined;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeReview();
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [open, currentCard?.review_card_key]);

  if (match.analysis_report?.status !== 'completed') return null;

  return (
    <div className='identity-subject-review-panel'>
      <div className='row between'>
        <div>
          <h3>Whole-subject review</h3>
          <div className='chips'>
            <span>Tryb: shadow</span>
            {document && <span>Review: {reviewCounts.actionableReviewed}/{reviewCounts.actionableTotal}</span>}
            {document && <span>Pozostalo: {reviewCounts.pending}</span>}
            {document && document.summary.stale_decisions > 0 && (
              <span>Stale: {document.summary.stale_decisions}</span>
            )}
          </div>
        </div>
        <div className='row'>
          <button type='button' className='secondary' onClick={() => void load(true)} disabled={availability === 'loading'}>
            Odswiez
          </button>
          <button
            type='button'
            onClick={startReview}
            disabled={!document || document.cards.length === 0}
          >
            Otworz review
          </button>
        </div>
      </div>
      {availability === 'missing' && (
        <p className='muted'>Brak artefaktu whole-subject review dla tego meczu.</p>
      )}
      {availability === 'error' && (
        <p className='warning-text'>Whole-subject review jest chwilowo niedostepny.</p>
      )}
      {document && !document.decisions_fresh && (
        <p className='warning-text'>Kontrakt ulegl zmianie. Poprzednie decyzje nie sa stosowane.</p>
      )}

      {open && document && (
        <div className='identity-subject-review-modal' role='dialog' aria-modal='true' aria-label='Whole-subject review'>
          <div className='identity-subject-review-shell'>
            <div className='row between identity-subject-review-header'>
              <div>
                <h2>Whole-subject review</h2>
                <span className='confidence-pill'>shadow</span>
              </div>
              <button type='button' className='secondary' onClick={closeReview}>Zamknij</button>
            </div>

            <div className='identity-subject-review-toolbar'>
              <label className='compact-field'>
                Stan
                <select value={reviewFilter} onChange={(event) => setReviewFilter(event.target.value as SubjectReviewFilter)}>
                  <option value='pending'>Do review ({reviewCounts.pending})</option>
                  <option value='reviewed'>Oznaczone ({reviewCounts.reviewed})</option>
                  <option value='all'>Wszystkie ({reviewCounts.all})</option>
                </select>
              </label>
              <label className='compact-field'>
                Druzyna
                <select value={teamFilter} onChange={(event) => setTeamFilter(event.target.value as SubjectTeamFilter)}>
                  <option value='A'>Team A ({counts.A})</option>
                  <option value='B'>Team B ({counts.B})</option>
                  <option value='U'>Unknown ({counts.U})</option>
                  <option value='all'>Wszystkie ({counts.all})</option>
                </select>
              </label>
              <div className='identity-subject-review-progress'>
                <progress value={reviewCounts.actionableReviewed} max={Math.max(1, reviewCounts.actionableTotal)} />
                <span>{reviewCounts.actionableReviewed}/{reviewCounts.actionableTotal}</span>
              </div>
            </div>

            {currentCard ? (
              <div className='identity-subject-review-layout'>
                <div className='identity-subject-review-list'>
                  {cards.map((card, index) => (
                    <button
                      type='button'
                      className={index === cardIndex ? 'match-item active' : 'match-item'}
                      key={card.review_card_key}
                      onClick={() => setCardIndex(index)}
                    >
                      <strong>{card.candidate_subject_id}</strong>
                      <span>Team {card.team_label || 'U'} - {subjectDecisionLabel(card)}</span>
                    </button>
                  ))}
                </div>

                <div className='identity-subject-review-main'>
                  <div className='row between'>
                    <div>
                      <h3>{currentCard.candidate_subject_id}</h3>
                      <p className='muted'>
                        Team {currentCard.team_label || 'U'} - {currentCard.role || 'rola n/a'} - {frameRange(currentCard)}
                      </p>
                    </div>
                    <span className={`confidence-pill ${currentCard.review_status === 'ready_for_operator_review' ? 'high' : 'low'}`}>
                      {subjectReviewStatusLabel(currentCard.review_status)}
                    </span>
                  </div>

                  <div className='identity-subject-crop-grid'>
                    {currentCard.visual_evidence.anchor_crops.map((crop) => {
                      const annotation = cropAnnotations[crop.anchor_crop_id] || {};
                      const visualDiagnostics = crop.jersey_number_visual_diagnostics;
                      return (
                        <div className='identity-subject-crop' key={crop.anchor_crop_id}>
                          <a href={artifactUrl(match.id, crop.artifact)} target='_blank' rel='noreferrer'>
                            <img src={artifactUrl(match.id, crop.artifact)} alt={`${currentCard.candidate_subject_id} frame ${crop.frame}`} />
                          </a>
                          <span>f{crop.frame} - {typeof crop.time_sec === 'number' ? `${crop.time_sec.toFixed(1)}s` : 'time n/a'}</span>
                          <span>det {confidenceLabel(crop.detection_confidence)}</span>
                          {visualDiagnostics && (
                            <fieldset>
                              <legend>Diagnostyka obrazu (maszyna)</legend>
                              <div>Stan: {visualDiagnostics.status || 'nieokreslony'}</div>
                              {visualDiagnostics.observations?.map((observation) => (
                                <div key={observation}>{observation}</div>
                              ))}
                              {visualDiagnostics.reason_codes?.map((reason) => (
                                <div key={reason}>powod: {reason}</div>
                              ))}
                              <div>Nieokreslone nie oznacza braku numeru ani etykiety operatora.</div>
                            </fieldset>
                          )}
                          <fieldset disabled={saving}>
                            <legend>Ocena reczna numeru</legend>
                            <label>
                              Cyfry
                              <select value={annotation.digit_visibility || 'unknown'} onChange={(event) => updateCropAnnotation(crop.anchor_crop_id, 'digit_visibility', event.target.value)}>
                                <option value='full'>Czytelne</option>
                                <option value='partial'>Czesciowo</option>
                                <option value='none'>Niewidoczne</option>
                                <option value='unknown'>Nie oceniono</option>
                              </select>
                            </label>
                            <label>
                              Zaslonicie
                              <select value={annotation.occlusion_state || 'unknown'} onChange={(event) => updateCropAnnotation(crop.anchor_crop_id, 'occlusion_state', event.target.value)}>
                                <option value='none'>Brak</option>
                                <option value='partial'>Czesciowe</option>
                                <option value='heavy'>Duże</option>
                                <option value='unknown'>Nie oceniono</option>
                              </select>
                            </label>
                            <label>
                              Ostrosc
                              <select value={annotation.blur_level || 'unknown'} onChange={(event) => updateCropAnnotation(crop.anchor_crop_id, 'blur_level', event.target.value)}>
                                <option value='none'>Brak</option>
                                <option value='mild'>Lekka</option>
                                <option value='heavy'>Duże</option>
                                <option value='unknown'>Nie oceniono</option>
                              </select>
                            </label>
                            <label>
                              Perspektywa
                              <select value={annotation.perspective_state || 'unknown'} onChange={(event) => updateCropAnnotation(crop.anchor_crop_id, 'perspective_state', event.target.value)}>
                                <option value='frontal'>Przod</option>
                                <option value='angled'>Skos</option>
                                <option value='severe'>Silny skos</option>
                                <option value='unknown'>Nie oceniono</option>
                              </select>
                            </label>
                            <label>
                              Panel % wysokosci
                              <input type='number' min='0' max='1' step='0.01' value={annotation.panel_height_ratio ?? ''} onChange={(event) => updateCropAnnotation(crop.anchor_crop_id, 'panel_height_ratio', event.target.value === '' ? null : Number(event.target.value))} />
                            </label>
                            <label>
                              Profil stroju
                              <input value={annotation.kit_profile ?? ''} onChange={(event) => updateCropAnnotation(crop.anchor_crop_id, 'kit_profile', event.target.value || null)} placeholder='np. biale pasy' />
                            </label>
                          </fieldset>
                        </div>
                      );
                    })}
                    {currentCard.visual_evidence.anchor_crops.length === 0 && (
                      <div className='player-heatmap-placeholder'>Brak wiarygodnych cropow</div>
                    )}
                  </div>

                  {(currentCard.blockers.length > 0 || currentCard.quality_flags.length > 0) && (
                    <div className='identity-subject-evidence'>
                      {currentCard.blockers.map((value) => <span key={`blocker-${value}`}>blocker: {value}</span>)}
                      {currentCard.quality_flags.map((value) => <span key={`flag-${value}`}>flag: {value}</span>)}
                    </div>
                  )}

                  <div className='identity-subject-decision'>
                    <label>
                      Zawodnik
                      <select
                        value={selectedPlayerId}
                        onChange={(event) => setSelectedPlayerId(event.target.value)}
                        disabled={!currentCard.allowed_actions.includes('assign_roster_player') || saving}
                      >
                        <option value=''>Wybierz zawodnika</option>
                        {currentRosterOptions.map((candidate) => (
                          <option value={candidate.player_id} key={candidate.player_id}>
                            {playerOptionLabel(candidate.player_name, candidate.team_label)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Notatka
                      <input value={comment} onChange={(event) => setComment(event.target.value)} disabled={saving} />
                    </label>
                  </div>

                  <div className='row between identity-subject-review-footer'>
                    <div className='row'>
                      <button
                        type='button'
                        className='secondary'
                        onClick={() => setCardIndex(Math.max(0, cardIndex - 1))}
                        disabled={cardIndex === 0 || saving}
                      >
                        Poprzedni
                      </button>
                      <button
                        type='button'
                        className='secondary'
                        onClick={() => setCardIndex(Math.min(cards.length - 1, cardIndex + 1))}
                        disabled={cardIndex >= cards.length - 1 || saving}
                      >
                        Nastepny
                      </button>
                    </div>
                    <div className='row'>
                      <button
                        type='button'
                        className='secondary'
                        onClick={() => void saveCropAnnotations()}
                        disabled={currentCard.visual_evidence.anchor_crops.length === 0 || saving}
                      >
                        Zapisz ocene cropow (shadow)
                      </button>
                      {currentCard.operator_decision && (
                        <button type='button' className='secondary' onClick={() => void saveDecision('clear_decision')} disabled={saving}>
                          Wyczysc
                        </button>
                      )}
                      <button
                        type='button'
                        className='secondary'
                        onClick={() => void saveDecision('mark_unresolved')}
                        disabled={!currentCard.allowed_actions.includes('mark_unresolved') || saving}
                      >
                        Nierozstrzygniety
                      </button>
                      {currentCard.recommended_player && currentCard.allowed_actions.includes('confirm_recommended_player') && (
                        <button type='button' onClick={() => void saveDecision('confirm_recommended_player')} disabled={saving}>
                          Potwierdz {currentCard.recommended_player.player_name || 'rekomendacje'}
                        </button>
                      )}
                      <button
                        type='button'
                        onClick={() => void saveDecision('assign_roster_player')}
                        disabled={!currentCard.allowed_actions.includes('assign_roster_player') || !selectedPlayerId || saving}
                      >
                        Przypisz
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className='identity-subject-review-empty'>
                {hasOnlyReviewedCardsInSelectedTeam
                  ? `Team ${teamFilter}: brak kart do review. ${reviewedCardsInSelectedTeam} kart${reviewedCardsInSelectedTeam === 1 ? 'a jest juz oznaczona' : ' jest juz oznaczonych'}. Zmien Stan na Oznaczone lub Wszystkie, aby je zobaczyc.`
                  : 'Brak kart dla wybranych filtrow.'}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
