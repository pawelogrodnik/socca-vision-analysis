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
} from '../utils/reviewedIdentityCorrection';
import { formatReviewTime, teamLabelForOperator } from '../utils/reviewedOutputPresentation';

type Props = {
  matchId: string;
  entity: ReviewedIdentityAtEntity;
  onCancel: () => void;
  onSaved: (result: ReviewedCorrectionResponse) => void;
};

type ActionCard = {
  action: ReviewedCorrectionAction;
  label: string;
};

const ACTION_CARDS: ActionCard[] = [
  { action: 'assign_roster_player', label: 'Zawodnik z kadry' },
  { action: 'assign_existing_slot', label: 'Ten sam gracz co Axx' },
  { action: 'create_new_stable_player', label: 'Nowy zawodnik' },
  { action: 'referee', label: 'Sędzia' },
  { action: 'false_detection', label: 'Fałszywa detekcja' },
  { action: 'team_unknown', label: 'Nieznana drużyna' },
  { action: 'unresolved', label: 'Nie wiem' },
];

function correctionRange(entity: ReviewedIdentityAtEntity): string | null {
  if (!entity.time_sec || !entity.frame || entity.frame_end < entity.frame_start) return null;
  const fps = entity.frame / entity.time_sec;
  if (!Number.isFinite(fps) || fps <= 0) return null;
  return `${formatReviewTime(entity.frame_start / fps)}–${formatReviewTime(entity.frame_end / fps)}`;
}

export function ReviewedIdentityCorrectionForm({
  matchId,
  entity,
  onCancel,
  onSaved,
}: Props) {
  const subjectId = entity.candidate_subject_id;
  const [context, setContext] = useState<Awaited<ReturnType<typeof getReviewedCorrectionContext>> | null>(null);
  const [action, setAction] = useState<ReviewedCorrectionAction | null>(null);
  const [playerId, setPlayerId] = useState(entity.canonical_player_id ?? '');
  const [stableSlotId, setStableSlotId] = useState(entity.stable_anonymous_slot_id ?? '');
  const [teamLabel, setTeamLabel] = useState(entity.team_label);
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!subjectId) return;
    setBusy(true);
    getReviewedCorrectionContext(matchId, subjectId)
      .then((value) => {
        setContext(value);
        setTeamLabel(value.team_label);
      })
      .catch((reason) => setError(errorMessage(reason)))
      .finally(() => setBusy(false));
  }, [matchId, subjectId]);

  const options = useMemo(
    () => context ? correctionOptionsForSubject(context) : { roster: [], slots: [] },
    [context],
  );
  const range = correctionRange(entity);

  function selectAction(nextAction: ReviewedCorrectionAction) {
    setAction(nextAction);
    setError('');
  }

  function actionIsAvailable(card: ActionCard): boolean {
    if (card.action === 'assign_roster_player') return Boolean(context?.review_card_key && options.roster.length);
    if (card.action === 'assign_existing_slot') return Boolean(options.slots.length);
    return true;
  }

  async function save() {
    if (!subjectId || !action) return;
    setError('');
    setBusy(true);
    try {
      const payload = buildReviewedCorrectionPayload(subjectId, {
        action,
        playerId,
        stableSlotId,
        teamLabel,
        comment,
      });
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
    <p>Ta poprawka zostanie zastosowana do całego fragmentu zawodnika.</p>
    {range && <p className='reviewed-correction-range'>Zakres: {range}<br />{entity.detected_evidence_count} wykryte obserwacje</p>}
    <div className='reviewed-action-cards' role='radiogroup' aria-label='Rodzaj poprawki'>
      {ACTION_CARDS.map((card) => <button
        type='button'
        key={card.action}
        role='radio'
        aria-checked={action === card.action}
        className={`reviewed-action-card${action === card.action ? ' selected' : ''}`}
        onClick={() => selectAction(card.action)}
        disabled={busy || !actionIsAvailable(card)}
      >{card.label}</button>)}
    </div>
    {action === 'assign_roster_player' && <label>Zawodnik z kadry
      <select value={playerId} onChange={(event) => setPlayerId(event.target.value)} disabled={busy}>
        <option value=''>Wybierz zawodnika</option>
        {options.roster.map((player) => <option key={player.player_id} value={player.player_id}>
          {player.player_name}{player.roster_number ? ` #${player.roster_number}` : ''} · {teamLabelForOperator(player.team_label)}
        </option>)}
      </select>
    </label>}
    {action === 'assign_existing_slot' && <label>Wybierz istniejący slot
      <select value={stableSlotId} onChange={(event) => setStableSlotId(event.target.value)} disabled={busy}>
        <option value=''>Wybierz gracza</option>
        {options.slots.map((slot) => <option key={slot.stable_slot_id} value={slot.stable_slot_id}>
          {slot.stable_slot_id}
        </option>)}
      </select>
    </label>}
    {action === 'create_new_stable_player' && (['A', 'B'].includes(context?.team_label ?? '')
      ? <p>Nowy zawodnik zostanie dodany do: <strong>{teamLabelForOperator(context?.team_label)}</strong></p>
      : <label>Drużyna
        <select value={teamLabel} onChange={(event) => setTeamLabel(event.target.value)} disabled={busy}>
          <option value=''>Wybierz drużynę</option>
          <option value='A'>Team A</option>
          <option value='B'>Team B</option>
        </select>
      </label>)}
    <label>Dodatkowy komentarz (opcjonalnie)
      <textarea value={comment} onChange={(event) => setComment(event.target.value)} disabled={busy} />
    </label>
    <details className='reviewed-correction-technical-details'>
      <summary>Szczegóły techniczne</summary>
      <p>candidate_subject_id: {subjectId}</p>
      <p>tracklet_ids: {(context?.tracklet_ids ?? entity.candidate_subject_ids).join(', ') || entity.tracklet_id}</p>
      <p>Aktualnie: {entity.identity_status} · slot: {entity.stable_anonymous_slot_id ?? 'brak'} · player ID: {entity.canonical_player_id ?? 'brak'}</p>
    </details>
    {error && <p className='status error'>{error}</p>}
    <div className='row'>
      <button type='button' onClick={() => void save()} disabled={busy || !context || !action}>Zapisz poprawkę</button>
      <button type='button' className='secondary' onClick={onCancel} disabled={busy}>Anuluj</button>
    </div>
  </div>;
}
