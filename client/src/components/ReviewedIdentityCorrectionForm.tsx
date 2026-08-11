import { useEffect, useMemo, useState } from 'react';

import {
  getReviewedCorrectionContext,
  saveReviewedIdentityCorrection,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type {
  ReviewedCorrectionAction,
  ReviewedCorrectionResponse,
  ReviewedIdentityAtEntity,
} from '../types';
import {
  buildReviewedCorrectionPayload,
  correctionOptionsForSubject,
  defaultCorrectionTeam,
} from '../utils/reviewedIdentityCorrection';
import { formatReviewTime, teamLabelForOperator } from '../utils/reviewedOutputPresentation';

type Props = {
  matchId: string;
  entity: ReviewedIdentityAtEntity;
  onCancel: () => void;
  onSaved: (result: ReviewedCorrectionResponse) => void;
  deferRecompute?: boolean;
};

type ActionCard = { action: ReviewedCorrectionAction; label: string; description?: string };

const STANDARD_ACTION_CARDS: ActionCard[] = [
  { action: 'assign_roster_player', label: 'Zawodnik z kadry' },
  { action: 'assign_existing_slot', label: 'Ten sam gracz co Axx/Bxx' },
  { action: 'create_new_stable_player', label: 'Nowy zawodnik' },
  { action: 'referee', label: 'Sędzia' },
  { action: 'false_detection', label: 'Fałszywa detekcja' },
  { action: 'team_unknown', label: 'Nieznana drużyna' },
  { action: 'unresolved', label: 'Nie wiem' },
];

const SEGMENT_ACTION_CARDS: ActionCard[] = [
  { action: 'assign_roster_player', label: 'Zawodnik z kadry' },
  { action: 'referee', label: 'Sędzia' },
  { action: 'false_detection', label: 'Fałszywa detekcja' },
  { action: 'team_unknown', label: 'Nieznana drużyna' },
  { action: 'unresolved', label: 'Nie wiem' },
];

const UNKNOWN_TEAM_SPECIAL_ACTIONS: ActionCard[] = [
  { action: 'referee', label: 'Sędzia' },
  { action: 'false_detection', label: 'Fałszywa detekcja' },
  { action: 'unresolved', label: 'Nie wiem' },
];

function correctionRange(entity: ReviewedIdentityAtEntity): string | null {
  if (!entity.time_sec || !entity.frame || entity.frame_end < entity.frame_start) return null;
  const fps = entity.frame / entity.time_sec;
  if (!Number.isFinite(fps) || fps <= 0) return null;
  return `${formatReviewTime(entity.frame_start / fps)}–${formatReviewTime(entity.frame_end / fps)}`;
}

function actionCardsForUnknownTeam(teamLabel: string): ActionCard[] {
  const teamName = teamLabelForOperator(teamLabel);
  const slotPrefix = `${teamLabel}xx`;
  return [
    { action: 'assign_team', label: `Tylko ${teamName} — pozostaw ${teamLabel}?` },
    { action: 'assign_roster_player', label: `Zawodnik z kadry ${teamName}` },
    { action: 'assign_existing_slot', label: `Ten sam zawodnik co ${slotPrefix}` },
    {
      action: 'create_new_stable_player',
      label: `Nowy zawodnik ${teamName}`,
      description: 'Utworzy nowego, odrębnego anonimowego zawodnika.',
    },
  ];
}

export function ReviewedIdentityCorrectionForm({ matchId, entity, onCancel, onSaved, deferRecompute = false }: Props) {
  const subjectId = entity.candidate_subject_id;
  const [context, setContext] = useState<Awaited<ReturnType<typeof getReviewedCorrectionContext>> | null>(null);
  const [action, setAction] = useState<ReviewedCorrectionAction | null>(null);
  const [selectedTeamLabel, setSelectedTeamLabel] = useState('');
  const [playerId, setPlayerId] = useState('');
  const [stableSlotId, setStableSlotId] = useState('');
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;

    setContext(null);
    setAction(null);
    setSelectedTeamLabel('');
    setPlayerId('');
    setStableSlotId('');
    setComment('');
    setError('');

    if (!subjectId) {
      setBusy(false);
      return () => {
        cancelled = true;
      };
    }

    setBusy(true);
    getReviewedCorrectionContext(matchId, subjectId, entity.review_target_id)
      .then((value) => {
        if (cancelled) return;
        setContext(value);
        setSelectedTeamLabel(defaultCorrectionTeam(value));
        if (value.legacy_suggestion?.requires_confirmation) {
          setAction('assign_roster_player');
          setPlayerId(value.legacy_suggestion.player_id);
        }
      })
      .catch((reason) => {
        if (!cancelled) setError(errorMessage(reason));
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });

    return () => {
      cancelled = true;
    };
  }, [entity.review_target_id, matchId, subjectId]);

  const sourceTeamLabel = context?.source_team_label ?? entity.team_label;
  const sourceTeamUnknown = sourceTeamLabel === 'U';
  const segmentScope = context?.scope_kind === 'canonical_segment';
  const options = useMemo(
    () => context ? correctionOptionsForSubject(context, selectedTeamLabel) : { roster: [], slots: [] },
    [context, selectedTeamLabel],
  );
  const range = correctionRange(entity);
  const actionCards = segmentScope
    ? SEGMENT_ACTION_CARDS
    : sourceTeamUnknown
    ? actionCardsForUnknownTeam(selectedTeamLabel)
    : STANDARD_ACTION_CARDS;
  const choiceComplete = Boolean(action)
    && (action !== 'assign_team' || ['A', 'B'].includes(selectedTeamLabel))
    && (action !== 'assign_roster_player' || Boolean(playerId))
    && (action !== 'assign_existing_slot' || Boolean(stableSlotId))
    && (action !== 'create_new_stable_player' || ['A', 'B'].includes(selectedTeamLabel));

  function chooseTeam(teamLabel: string) {
    setSelectedTeamLabel(teamLabel);
    setAction(null);
    setPlayerId('');
    setStableSlotId('');
    setError('');
  }

  function selectAction(nextAction: ReviewedCorrectionAction) {
    if (segmentScope && nextAction === 'assign_roster_player' && context) {
      setSelectedTeamLabel(context.source_team_label);
    }
    if (
      sourceTeamUnknown
      && ['referee', 'false_detection', 'unresolved'].includes(nextAction)
    ) {
      setSelectedTeamLabel('');
      setPlayerId('');
      setStableSlotId('');
    }
    setAction(nextAction);
    setError('');
  }

  function actionIsAvailable(card: ActionCard): boolean {
    if (card.action === 'assign_roster_player') {
      return segmentScope
        ? Boolean(context?.roster_options.length)
        : options.roster.length > 0;
    }
    if (card.action === 'assign_existing_slot') return options.slots.length > 0;
    return true;
  }

  async function save() {
    if (!subjectId || !action || !choiceComplete) return;
    setError('');
    setBusy(true);
    try {
      const payload = buildReviewedCorrectionPayload(subjectId, {
        action,
        playerId,
        stableSlotId,
        teamLabel: selectedTeamLabel,
        comment,
      }, context);
      if (deferRecompute) payload.defer_recompute = true;
      onSaved(await saveReviewedIdentityCorrection(matchId, payload));
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  if (!subjectId) {
    return <div className='reviewed-correction-form'>
      <p>Ta obserwacja nie ma jednoznacznego fragmentu i nie może być poprawiona.</p>
      <button type='button' onClick={onCancel}>Zamknij</button>
    </div>;
  }

  return <div className='reviewed-correction-form'>
    <h4>Popraw przypisanie</h4>
    <p>{segmentScope
      ? 'Ta poprawka zostanie zastosowana tylko do pokazanych, bezpiecznie wydzielonych obserwacji.'
      : 'Ta poprawka zostanie zastosowana do całego fragmentu zawodnika.'}</p>
    {context?.legacy_suggestion && <p className='reviewed-correction-suggestion'>
      Poprzednia decyzja sugeruje: <strong>{context.legacy_suggestion.player_name}</strong>.
      Sprawdź pokazany fragment i zapisz, aby potwierdzić zakres.
    </p>}
    {range && <p className='reviewed-correction-range'>Zakres: {range}<br />{entity.detected_evidence_count} wykryte obserwacje</p>}

    {segmentScope ? <>
      <section className='reviewed-correction-step'>
        <h5>Jeśli znasz tylko drużynę</h5>
        <div className='reviewed-action-cards reviewed-team-cards' role='radiogroup' aria-label='Potwierdzenie drużyny segmentu'>
          {['A', 'B'].map((teamLabel) => <button
            type='button'
            key={teamLabel}
            role='radio'
            aria-checked={action === 'assign_team' && selectedTeamLabel === teamLabel}
            className={`reviewed-action-card${action === 'assign_team' && selectedTeamLabel === teamLabel ? ' selected' : ''}`}
            onClick={() => {
              setSelectedTeamLabel(teamLabel);
              setAction('assign_team');
              setPlayerId('');
              setStableSlotId('');
              setError('');
            }}
            disabled={busy}
          >{teamLabelForOperator(teamLabel)} — zawodnik nieznany</button>)}
        </div>
      </section>
      <section className='reviewed-correction-step'>
        <h5>Albo wybierz dokładniejszą odpowiedź</h5>
        <div className='reviewed-action-cards' role='radiogroup' aria-label='Rodzaj poprawki segmentu'>
          {actionCards.map((card) => <button
            type='button'
            key={card.action}
            role='radio'
            aria-checked={action === card.action}
            className={`reviewed-action-card${action === card.action ? ' selected' : ''}`}
            onClick={() => selectAction(card.action)}
            disabled={busy || !actionIsAvailable(card)}
          >{card.label}</button>)}
        </div>
      </section>
    </> : sourceTeamUnknown ? <>
      <section className='reviewed-correction-step'>
        <h5>Do której drużyny należy ta osoba?</h5>
        <div className='reviewed-action-cards reviewed-team-cards' role='radiogroup' aria-label='Wybór drużyny'>
          {['A', 'B'].map((teamLabel) => <button
            type='button'
            key={teamLabel}
            role='radio'
            aria-checked={selectedTeamLabel === teamLabel}
            className={`reviewed-action-card${selectedTeamLabel === teamLabel ? ' selected' : ''}`}
            onClick={() => chooseTeam(teamLabel)}
            disabled={busy || !context?.available_team_labels.includes(teamLabel)}
          >{teamLabelForOperator(teamLabel)}</button>)}
          {UNKNOWN_TEAM_SPECIAL_ACTIONS.map((card) => <button
            type='button'
            key={card.action}
            role='radio'
            aria-checked={action === card.action}
            className={`reviewed-action-card${action === card.action ? ' selected' : ''}`}
            onClick={() => selectAction(card.action)}
            disabled={busy}
          >{card.label}</button>)}
        </div>
      </section>
      {selectedTeamLabel && <section className='reviewed-correction-step'>
        <h5>Co wiesz o tym zawodniku?</h5>
        <div className='reviewed-action-cards' role='radiogroup' aria-label='Zakres wiedzy o zawodniku'>
          {actionCards.map((card) => <button
            type='button'
            key={card.action}
            role='radio'
            aria-checked={action === card.action}
            className={`reviewed-action-card${action === card.action ? ' selected' : ''}`}
            onClick={() => selectAction(card.action)}
            disabled={busy || !actionIsAvailable(card)}
          >{card.label}</button>)}
        </div>
      </section>}
    </> : <div className='reviewed-action-cards' role='radiogroup' aria-label='Rodzaj poprawki'>
      {actionCards.map((card) => <button
        type='button'
        key={card.action}
        role='radio'
        aria-checked={action === card.action}
        className={`reviewed-action-card${action === card.action ? ' selected' : ''}`}
        onClick={() => selectAction(card.action)}
        disabled={busy || !actionIsAvailable(card)}
      >{card.label}</button>)}
    </div>}

    {action === 'assign_team' && <p className='reviewed-correction-range'>Przypiszę tylko {teamLabelForOperator(selectedTeamLabel)}. Nie powstanie nowy slot ani indywidualne statystyki zawodnika.</p>}
    {action === 'assign_roster_player' && <label>Zawodnik z kadry
      <select value={playerId} onChange={(event) => setPlayerId(event.target.value)} disabled={busy}>
        <option value=''>Wybierz zawodnika</option>
        {options.roster.map((player) => <option key={player.player_id} value={player.player_id}>
          {player.player_name}{player.roster_number ? ` #${player.roster_number}` : ''} · {teamLabelForOperator(player.team_label)}
        </option>)}
      </select>
    </label>}
    {action === 'assign_existing_slot' && <label>Wybierz istniejącego zawodnika
      <select value={stableSlotId} onChange={(event) => setStableSlotId(event.target.value)} disabled={busy}>
        <option value=''>Wybierz gracza</option>
        {options.slots.map((slot) => <option key={slot.stable_slot_id} value={slot.stable_slot_id}>{slot.stable_slot_id}</option>)}
      </select>
    </label>}
    {action === 'create_new_stable_player' && <p className='reviewed-correction-range'>Utworzy nowego, odrębnego anonimowego zawodnika w {teamLabelForOperator(selectedTeamLabel)}.</p>}
    <label>Dodatkowy komentarz (opcjonalnie)
      <textarea value={comment} onChange={(event) => setComment(event.target.value)} disabled={busy} />
    </label>
    <details className='reviewed-correction-technical-details'>
      <summary>Szczegóły techniczne</summary>
      <p>candidate_subject_id: {subjectId}</p>
      {context?.review_target_id && <p>review_target_id: {context.review_target_id}</p>}
      <p>tracklet_ids: {(context?.tracklet_ids ?? entity.candidate_subject_ids).join(', ') || entity.tracklet_id}</p>
      <p>
        source team: {sourceTeamLabel}
        {' · '}current effective team: {context?.effective_team_label ?? 'brak'}
        {' · '}selected correction team: {selectedTeamLabel || 'brak'}
        {' · '}status: {entity.identity_status}
      </p>
    </details>
    {error && <p className='status error'>{error}</p>}
    <div className='row'>
      <button type='button' onClick={() => void save()} disabled={busy || !context || !choiceComplete}>Zapisz poprawkę</button>
      <button type='button' className='secondary' onClick={onCancel} disabled={busy}>Anuluj</button>
    </div>
  </div>;
}
