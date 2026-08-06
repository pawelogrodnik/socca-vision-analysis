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
  REVIEWED_CORRECTION_ACTION_LABELS,
} from '../utils/reviewedIdentityCorrection';

type Props = {
  matchId: string;
  entity: ReviewedIdentityAtEntity;
  onCancel: () => void;
  onSaved: (result: ReviewedCorrectionResponse) => void;
};

function initialAction(entity: ReviewedIdentityAtEntity): ReviewedCorrectionAction {
  if (entity.canonical_player_id) return 'assign_roster_player';
  if (entity.stable_anonymous_slot_id) return 'assign_existing_slot';
  if (entity.identity_status === 'referee') return 'referee';
  if (entity.identity_status === 'team_unknown') return 'team_unknown';
  return 'unresolved';
}

export function ReviewedIdentityCorrectionForm({
  matchId,
  entity,
  onCancel,
  onSaved,
}: Props) {
  const subjectId = entity.candidate_subject_id;
  const [context, setContext] = useState<Awaited<ReturnType<typeof getReviewedCorrectionContext>> | null>(null);
  const [action, setAction] = useState<ReviewedCorrectionAction>(initialAction(entity));
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

  async function save() {
    if (!subjectId) return;
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
      <p>Ta obserwacja nie ma jednoznacznego candidate subjectu i nie może być poprawiona.</p>
      <button type='button' onClick={onCancel}>Zamknij</button>
    </div>;
  }

  return <div className='reviewed-correction-form'>
    <h4>Popraw cały fragment</h4>
    <p>
      <strong>{subjectId}</strong> · Team {context?.team_label ?? entity.team_label} · tracklety:{' '}
      {(context?.tracklet_ids ?? entity.candidate_subject_ids).join(', ') || entity.tracklet_id}
    </p>
    <p className='muted'>
      Aktualnie: {entity.identity_status} · slot: {entity.stable_anonymous_slot_id ?? 'brak'} · player ID: {entity.canonical_player_id ?? 'brak'}
    </p>
    <label>Decyzja
      <select value={action} onChange={(event) => setAction(event.target.value as ReviewedCorrectionAction)} disabled={busy}>
        {Object.entries(REVIEWED_CORRECTION_ACTION_LABELS).map(([value, label]) => <option
          key={value}
          value={value}
          disabled={
            (value === 'assign_roster_player' && (!context?.review_card_key || !options.roster.length))
            || (value === 'assign_existing_slot' && !options.slots.length)
          }
        >{label}</option>)}
      </select>
    </label>
    {action === 'assign_roster_player' && <label>Zawodnik
      <select value={playerId} onChange={(event) => setPlayerId(event.target.value)} disabled={busy}>
        <option value=''>Wybierz zawodnika</option>
        {options.roster.map((player) => <option key={player.player_id} value={player.player_id}>
          {player.player_name}{player.roster_number ? ` #${player.roster_number}` : ''} · Team {player.team_label}
        </option>)}
      </select>
    </label>}
    {action === 'assign_existing_slot' && <label>Stable slot
      <select value={stableSlotId} onChange={(event) => setStableSlotId(event.target.value)} disabled={busy}>
        <option value=''>Wybierz slot</option>
        {options.slots.map((slot) => <option key={slot.stable_slot_id} value={slot.stable_slot_id}>
          {slot.stable_slot_id} · {slot.source} · {slot.status}
        </option>)}
      </select>
    </label>}
    {action === 'create_new_stable_player' && <label>Drużyna
      <select
        value={teamLabel}
        onChange={(event) => setTeamLabel(event.target.value)}
        disabled={busy || ['A', 'B'].includes(context?.team_label ?? '')}
      >
        <option value=''>Wybierz drużynę</option>
        <option value='A'>Team A</option>
        <option value='B'>Team B</option>
      </select>
    </label>}
    <label>Dodatkowy komentarz (opcjonalnie)
      <textarea value={comment} onChange={(event) => setComment(event.target.value)} disabled={busy} />
    </label>
    {error && <p className='status error'>{error}</p>}
    <div className='row'>
      <button type='button' onClick={() => void save()} disabled={busy || !context}>Zapisz poprawkę</button>
      <button type='button' className='secondary' onClick={onCancel} disabled={busy}>Anuluj</button>
    </div>
  </div>;
}
