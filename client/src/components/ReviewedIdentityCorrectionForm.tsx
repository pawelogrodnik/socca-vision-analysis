import { useEffect, useMemo, useState } from 'react';

import {
  getReviewedCorrectionContext,
  saveReviewedIdentityCorrection,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type {
  ReviewedCorrectionAction,
  ReviewedCorrectionPrimaryAction,
  ReviewedCorrectionResponse,
  ReviewedIdentityAtEntity,
  Team,
} from '../types';
import { ReviewedIdentitySplitEditor } from './ReviewedIdentitySplitEditor';
import {
  buildReviewedCorrectionPayload,
  correctionOptionsForSubject,
  defaultCorrectionTeam,
} from '../utils/reviewedIdentityCorrection';
import {
  REVIEWED_IDENTITY_ADVANCED_ACTIONS,
  REVIEWED_IDENTITY_PRIMARY_ACTIONS,
  type ReviewedIdentityActionCard,
} from '../utils/reviewedIdentityActions';
import { persistReviewDecision } from '../utils/identityExceptionWorkspace';
import { matchTeamName } from '../utils/identityExceptionTeamFilter';
import { formatReviewTime, teamLabelForOperator } from '../utils/reviewedOutputPresentation';

type Props = {
  matchId: string;
  entity: ReviewedIdentityAtEntity;
  onCancel: () => void;
  onSaved: (result: ReviewedCorrectionResponse) => void;
  teams?: Team[];
  teamAttributionOnly?: boolean;
  deferRecompute?: boolean;
  navigation?: {
    onPrevious: () => void;
    onNext: () => void;
    previousDisabled: boolean;
    nextDisabled: boolean;
    saveLabel: string;
    nextLabel?: string;
  };
};

type UiAction = ReviewedCorrectionPrimaryAction;

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
  teams,
  deferRecompute = false,
  navigation,
}: Props) {
  const subjectId = entity.candidate_subject_id;
  const [context, setContext] = useState<Awaited<ReturnType<typeof getReviewedCorrectionContext>> | null>(null);
  const [action, setAction] = useState<ReviewedCorrectionAction | null>(null);
  const [splitOpen, setSplitOpen] = useState(false);
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
    setSplitOpen(false);
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
        if (value.legacy_suggestion?.requires_confirmation && value.source_evidence_kind !== 'team_attribution') {
          setAction('assign_roster_player');
          setPlayerId(value.legacy_suggestion.player_id);
          setSelectedTeamLabel(value.legacy_suggestion.team_label);
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
  const operatorTeamName = (teamLabel: string | null | undefined) => (
    teamLabel === 'A' || teamLabel === 'B'
      ? matchTeamName(teams || [], teamLabel)
      : teamLabelForOperator(teamLabel)
  );
  const options = useMemo(
    () => context ? correctionOptionsForSubject(context, selectedTeamLabel) : { roster: [], slots: [] },
    [context, selectedTeamLabel],
  );
  const rosterOptionsByTeam = useMemo(() => ({
    A: options.roster.filter((player) => player.team_label === 'A'),
    B: options.roster.filter((player) => player.team_label === 'B'),
  }), [options.roster]);
  const selectedRosterPlayer = options.roster.find((player) => player.player_id === playerId);
  const range = correctionRange(entity);
  const actionCards = REVIEWED_IDENTITY_PRIMARY_ACTIONS.filter((card) => context?.action_capabilities[card.action]?.allowed);
  const advancedActionCards = REVIEWED_IDENTITY_ADVANCED_ACTIONS.filter((card) => context?.action_capabilities[card.action]?.allowed);
  const choiceComplete = Boolean(action)
    && (action !== 'assign_team' || ['A', 'B'].includes(selectedTeamLabel))
    && (action !== 'assign_roster_player' || Boolean(playerId))
    && (action !== 'assign_existing_slot' || Boolean(stableSlotId))
    && (action !== 'create_new_stable_player' || ['A', 'B'].includes(selectedTeamLabel));

  function selectAction(nextAction: UiAction) {
    if (nextAction === 'split') {
      setSplitOpen(true);
      setAction(null);
      return;
    }
    if (nextAction === 'assign_team' && !['A', 'B'].includes(selectedTeamLabel)) {
      setSelectedTeamLabel('A');
    }
    setAction(nextAction);
    setSplitOpen(false);
    setError('');
  }

  function chooseRosterPlayer(nextPlayerId: string) {
    setPlayerId(nextPlayerId);
    const player = context?.roster_options.find((option) => option.player_id === nextPlayerId);
    if (player) setSelectedTeamLabel(player.team_label);
    setError('');
  }

  function returnToCategories() {
    setAction(null);
    setPlayerId('');
    setStableSlotId('');
    setSelectedTeamLabel(context ? defaultCorrectionTeam(context) : '');
    setError('');
    setSplitOpen(false);
  }

  function actionIsAvailable(card: ReviewedIdentityActionCard): boolean {
    if (card.action === 'split') return Boolean(context?.action_capabilities.split?.allowed);
    if (card.action === 'assign_roster_player') return Boolean(context?.roster_options.length);
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
        mixedHint: undefined,
      }, context);
      if (deferRecompute) payload.defer_recompute = true;
      await persistReviewDecision(
        () => saveReviewedIdentityCorrection(matchId, payload),
        onSaved,
      );
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

  const actionLabel = action === 'assign_team'
    ? `${operatorTeamName(selectedTeamLabel)} — zawodnik nieznany`
    : [...REVIEWED_IDENTITY_PRIMARY_ACTIONS, ...REVIEWED_IDENTITY_ADVANCED_ACTIONS].find((card) => card.action === action)?.label;
  const showActionCategories = !navigation || (!action && !splitOpen);

  return <div className={`reviewed-correction-form${navigation ? ' workstation-form' : ''}`}>
    <header className='reviewed-correction-heading'>
      <div>
        <h4>{splitOpen ? 'Podziel fragment' : action ? actionLabel : 'Popraw przypisanie'}</h4>
        <p>{context?.scope_copy || 'Decyzja obejmie pokazany fragment zawodnika.'}</p>
      </div>
      {navigation && (action || splitOpen) && <button type='button' className='secondary compact-button' onClick={returnToCategories} disabled={busy}>
        ← Wróć
      </button>}
    </header>

    <div className='reviewed-correction-body'>
      {context?.source_evidence_kind === 'team_attribution' && <p className='reviewed-correction-suggestion'>
        Automatyka potwierdziła jedynie drużynę. Wybór konkretnego zawodnika jest ręczną decyzją operatora.
      </p>}
      {context?.legacy_suggestion && context.source_evidence_kind !== 'team_attribution' && <p className='reviewed-correction-suggestion'>
        Poprzednia decyzja sugeruje: <strong>{context.legacy_suggestion.player_name}</strong>.
        Sprawdź pokazany fragment i zapisz, aby potwierdzić zakres.
      </p>}
      {context?.temporal_split?.resolution_status === 'resolved' && !splitOpen && <div className='reviewed-correction-suggestion'>
        <strong>Aktualna decyzja: Podział na {context.temporal_split.segment_assignments.length} fragmentów.</strong>
        <button type='button' className='secondary compact-button' onClick={() => selectAction('split')} disabled={busy}>Edytuj podział</button>
      </div>}
      {range && <p className='reviewed-correction-range'>Zakres: {range}<br />{entity.detected_evidence_count} wykryte obserwacje</p>}

      {showActionCategories && <>
        <div className='reviewed-action-cards' role='radiogroup' aria-label='Rodzaj poprawki'>
          {actionCards.map((card) => <button type='button' key={card.action} role='radio' aria-checked={false}
            className='reviewed-action-card' onClick={() => selectAction(card.action)}
            disabled={busy || !actionIsAvailable(card)}>{card.label}</button>)}
        </div>
        {advancedActionCards.length > 0 && <details className='reviewed-correction-technical-details'>
          <summary>Zaawansowane</summary>
          <div className='reviewed-action-cards'>
            {advancedActionCards.map((card) => <button type='button' key={card.action}
              className='reviewed-action-card' onClick={() => selectAction(card.action)} disabled={busy || !actionIsAvailable(card)}>{card.label}</button>)}
          </div>
        </details>}
      </>}
      {splitOpen && context && <ReviewedIdentitySplitEditor
        matchId={matchId}
        context={context}
        teams={teams}
        onCancel={returnToCategories}
        onSaved={(result) => onSaved(result as unknown as ReviewedCorrectionResponse)}
      />}

      {action && <section className='reviewed-correction-detail' aria-label={`Wybrana decyzja: ${actionLabel}`}>
        {context?.temporal_split?.resolution_status === 'resolved' && <p className='reviewed-correction-suggestion'>
          Ta decyzja zastąpi zapisany podział oraz decyzje jego fragmentów.
        </p>}
        {action === 'assign_team' && <p className='reviewed-correction-range'>Przypiszę tylko {operatorTeamName(selectedTeamLabel)}. Nie powstanie nowy slot ani indywidualne statystyki zawodnika.</p>}
        {action === 'assign_team' && <div className='reviewed-action-cards' role='radiogroup' aria-label='Wybierz drużynę'>
          {(['A', 'B'] as const).map((teamLabel) => <button
            type='button'
            key={teamLabel}
            role='radio'
            aria-checked={selectedTeamLabel === teamLabel}
            className={selectedTeamLabel === teamLabel ? 'reviewed-action-card selected' : 'reviewed-action-card'}
            onClick={() => setSelectedTeamLabel(teamLabel)}
            disabled={busy}
          >{operatorTeamName(teamLabel)} — zawodnik nieznany</button>)}
        </div>}
        {action === 'assign_roster_player' && <label>Zawodnik z kadry
          <select value={playerId} onChange={(event) => chooseRosterPlayer(event.target.value)} disabled={busy}>
            <option value=''>Wybierz zawodnika</option>
            {(['A', 'B'] as const).map((teamLabel) => rosterOptionsByTeam[teamLabel].length > 0 && <optgroup
              key={teamLabel}
              label={operatorTeamName(teamLabel)}
            >
              {rosterOptionsByTeam[teamLabel].map((player) => <option key={player.player_id} value={player.player_id}>
                {player.player_name}{player.roster_number ? ` #${player.roster_number}` : ''}
              </option>)}
            </optgroup>)}
          </select>
          {selectedRosterPlayer && sourceTeamLabel !== 'U' && selectedRosterPlayer.team_label !== sourceTeamLabel
            && <span className='reviewed-correction-team-override'>
              Wybór {selectedRosterPlayer.player_name} poprawi również drużynę z {operatorTeamName(sourceTeamLabel)} na {operatorTeamName(selectedRosterPlayer.team_label)}.
            </span>}
        </label>}
        {action === 'assign_existing_slot' && <label>Wybierz istniejącego zawodnika
          <select value={stableSlotId} onChange={(event) => setStableSlotId(event.target.value)} disabled={busy}>
            <option value=''>Wybierz gracza</option>
            {options.slots.map((slot) => <option key={slot.stable_slot_id} value={slot.stable_slot_id}>{slot.stable_slot_id}</option>)}
          </select>
        </label>}
        {action === 'create_new_stable_player' && <p className='reviewed-correction-range'>Utworzy nowego, odrębnego anonimowego zawodnika w {operatorTeamName(selectedTeamLabel)}.</p>}
        {!['assign_team', 'assign_roster_player', 'assign_existing_slot', 'create_new_stable_player'].includes(action)
          && <p className='reviewed-correction-range'>Wybrano: <strong>{actionLabel}</strong>. Zapisz decyzję, aby przejść do kolejnego przypadku.</p>}
        <label>Dodatkowy komentarz (opcjonalnie)
          <textarea value={comment} onChange={(event) => setComment(event.target.value)} disabled={busy} rows={3} />
        </label>
      </section>}
      {!navigation && !action && <label>Dodatkowy komentarz (opcjonalnie)
        <textarea value={comment} onChange={(event) => setComment(event.target.value)} disabled={busy} rows={3} />
      </label>}

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
      {error && <p className='status error' role='alert'>{error}</p>}
    </div>

    {splitOpen ? null : navigation ? <footer className='reviewed-correction-navigation'>
      <button type='button' className='secondary' onClick={navigation.onPrevious} disabled={busy || navigation.previousDisabled}>Poprzedni</button>
      <button type='button' onClick={() => void save()} disabled={busy || !context || !choiceComplete}>{navigation.saveLabel}</button>
      <button type='button' className='secondary' onClick={navigation.onNext} disabled={busy || navigation.nextDisabled} title='Przejdź bez zapisywania'>{navigation.nextLabel || 'Następny'}</button>
    </footer> : <div className='row'>
      <button type='button' onClick={() => void save()} disabled={busy || !context || !choiceComplete}>Zapisz poprawkę</button>
      <button type='button' className='secondary' onClick={onCancel} disabled={busy}>Anuluj</button>
    </div>}
  </div>;
}
