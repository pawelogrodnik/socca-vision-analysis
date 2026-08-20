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
  Team,
} from '../types';
import {
  buildReviewedCorrectionPayload,
  correctionOptionsForSubject,
  defaultCorrectionTeam,
} from '../utils/reviewedIdentityCorrection';
import { persistReviewDecision } from '../utils/identityExceptionWorkspace';
import { formatReviewTime, teamLabelForOperator } from '../utils/reviewedOutputPresentation';
import {
  TEAM_ATTRIBUTION_ONLY_ACTIONS,
  teamAttributionTeamActions,
} from '../utils/reviewedTeamAttributionActions';

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
  };
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
  {
    action: 'mixed_players',
    label: 'Zmieszani gracze',
    description: 'Widoki zawierają więcej niż jedną rzeczywistą osobę.',
  },
  { action: 'unresolved', label: 'Nie wiem' },
];

const MIXED_PLAYERS_ACTION: ActionCard = {
  action: 'mixed_players',
  label: 'Zmieszani gracze',
  description: 'Widoki zawierają więcej niż jedną rzeczywistą osobę.',
};

function withMixedPlayersAction(cards: ActionCard[]): ActionCard[] {
  if (cards.some((card) => card.action === 'mixed_players')) return cards;
  const insertAt = Math.max(0, cards.length - 1);
  return [...cards.slice(0, insertAt), MIXED_PLAYERS_ACTION, ...cards.slice(insertAt)];
}

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

export function ReviewedIdentityCorrectionForm({
  matchId,
  entity,
  onCancel,
  onSaved,
  teams,
  teamAttributionOnly = false,
  deferRecompute = false,
  navigation,
}: Props) {
  const subjectId = entity.candidate_subject_id;
  const [context, setContext] = useState<Awaited<ReturnType<typeof getReviewedCorrectionContext>> | null>(null);
  const [action, setAction] = useState<ReviewedCorrectionAction | null>(null);
  const [selectedTeamLabel, setSelectedTeamLabel] = useState('');
  const [playerId, setPlayerId] = useState('');
  const [stableSlotId, setStableSlotId] = useState('');
  const [comment, setComment] = useState('');
  const [mixedHint, setMixedHint] = useState<NonNullable<import('../types').ReviewedCorrectionRequest['mixed_hint']>>('unknown');
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
    setMixedHint('unknown');
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
        if (!teamAttributionOnly && value.legacy_suggestion?.requires_confirmation) {
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
  }, [entity.review_target_id, matchId, subjectId, teamAttributionOnly]);

  const sourceTeamLabel = context?.source_team_label ?? entity.team_label;
  const sourceTeamUnknown = sourceTeamLabel === 'U';
  const segmentScope = context?.scope_kind === 'canonical_segment';
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
  const baseActionCards = teamAttributionOnly
    ? [...TEAM_ATTRIBUTION_ONLY_ACTIONS]
    : segmentScope
    ? SEGMENT_ACTION_CARDS
    : sourceTeamUnknown
    ? actionCardsForUnknownTeam(selectedTeamLabel)
    : STANDARD_ACTION_CARDS;
  const actionCards = teamAttributionOnly || segmentScope
    ? baseActionCards
    : withMixedPlayersAction(baseActionCards);
  const teamAttributionActions = useMemo(
    () => teamAttributionTeamActions(teams),
    [teams],
  );
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
        mixedHint,
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
    ? `${teamLabelForOperator(selectedTeamLabel)} — zawodnik nieznany`
    : [...STANDARD_ACTION_CARDS, ...actionCards].find((card) => card.action === action)?.label;
  const showActionCategories = !navigation || !action;

  return <div className={`reviewed-correction-form${navigation ? ' workstation-form' : ''}`}>
    <header className='reviewed-correction-heading'>
      <div>
        <h4>{action ? actionLabel : 'Popraw przypisanie'}</h4>
        <p>{segmentScope
          ? 'Decyzja obejmie tylko pokazany fragment.'
          : 'Decyzja obejmie cały fragment zawodnika.'}</p>
      </div>
      {navigation && action && <button type='button' className='secondary compact-button' onClick={returnToCategories} disabled={busy}>
        ← Wróć
      </button>}
    </header>

    <div className='reviewed-correction-body'>
      {context?.legacy_suggestion && !teamAttributionOnly && <p className='reviewed-correction-suggestion'>
        Poprzednia decyzja sugeruje: <strong>{context.legacy_suggestion.player_name}</strong>.
        Sprawdź pokazany fragment i zapisz, aby potwierdzić zakres.
      </p>}
      {range && <p className='reviewed-correction-range'>Zakres: {range}<br />{entity.detected_evidence_count} wykryte obserwacje</p>}

      {showActionCategories && teamAttributionOnly ? <>
        <section className='reviewed-correction-step'>
          <h5>Do której drużyny należy ta osoba?</h5>
          <div className='reviewed-action-cards reviewed-team-cards' role='radiogroup' aria-label='Potwierdzenie drużyny'>
            {teamAttributionActions.map(({ label, teamLabel }) => <button
              type='button'
              key={teamLabel}
              role='radio'
              aria-checked={false}
              className='reviewed-action-card'
              onClick={() => {
                setSelectedTeamLabel(teamLabel);
                setAction('assign_team');
                setPlayerId('');
                setStableSlotId('');
                setError('');
              }}
              disabled={busy || !context?.available_team_labels.includes(teamLabel)}
            >{label}</button>)}
          </div>
        </section>
        <section className='reviewed-correction-step'>
          <h5>Albo wybierz rodzaj detekcji</h5>
          <div className='reviewed-action-cards' role='radiogroup' aria-label='Rodzaj detekcji'>
            {actionCards.map((card) => <button
              type='button'
              key={card.action}
              role='radio'
              aria-checked={false}
              className='reviewed-action-card'
              onClick={() => selectAction(card.action)}
              disabled={busy}
            >{card.label}</button>)}
          </div>
        </section>
      </> : showActionCategories && segmentScope ? <>
        <section className='reviewed-correction-step'>
          <h5>Jeśli znasz tylko drużynę</h5>
          <div className='reviewed-action-cards reviewed-team-cards' role='radiogroup' aria-label='Potwierdzenie drużyny segmentu'>
            {['A', 'B'].map((teamLabel) => <button
              type='button'
              key={teamLabel}
              role='radio'
              aria-checked={false}
              className='reviewed-action-card'
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
              aria-checked={false}
              className='reviewed-action-card'
              onClick={() => selectAction(card.action)}
              disabled={busy || !actionIsAvailable(card)}
            >{card.label}</button>)}
          </div>
        </section>
      </> : showActionCategories && sourceTeamUnknown ? <>
        {(!selectedTeamLabel || !navigation) && <section className='reviewed-correction-step'>
          <h5>Do której drużyny należy ta osoba?</h5>
          <div className='reviewed-action-cards reviewed-team-cards' role='radiogroup' aria-label='Wybór drużyny'>
            {['A', 'B'].map((teamLabel) => <button
              type='button'
              key={teamLabel}
              role='radio'
              aria-checked={false}
              className='reviewed-action-card'
              onClick={() => chooseTeam(teamLabel)}
              disabled={busy || !context?.available_team_labels.includes(teamLabel)}
            >{teamLabelForOperator(teamLabel)}</button>)}
            {UNKNOWN_TEAM_SPECIAL_ACTIONS.map((card) => <button
              type='button'
              key={card.action}
              role='radio'
              aria-checked={false}
              className='reviewed-action-card'
              onClick={() => selectAction(card.action)}
              disabled={busy}
            >{card.label}</button>)}
          </div>
        </section>}
        {selectedTeamLabel && <section className='reviewed-correction-step'>
          <div className='reviewed-correction-step-heading'>
            <h5>Co wiesz o tym zawodniku?</h5>
            <button type='button' className='text-button' onClick={() => chooseTeam('')} disabled={busy}>Zmień drużynę</button>
          </div>
          <div className='reviewed-action-cards' role='radiogroup' aria-label='Zakres wiedzy o zawodniku'>
            {actionCards.map((card) => <button
              type='button'
              key={card.action}
              role='radio'
              aria-checked={false}
              className='reviewed-action-card'
              onClick={() => selectAction(card.action)}
              disabled={busy || !actionIsAvailable(card)}
            >{card.label}{card.description && <small>{card.description}</small>}</button>)}
          </div>
        </section>}
      </> : showActionCategories ? <div className='reviewed-action-cards' role='radiogroup' aria-label='Rodzaj poprawki'>
        {actionCards.map((card) => <button
          type='button'
          key={card.action}
          role='radio'
          aria-checked={false}
          className='reviewed-action-card'
          onClick={() => selectAction(card.action)}
          disabled={busy || !actionIsAvailable(card)}
        >{card.label}{card.description && <small>{card.description}</small>}</button>)}
      </div> : null}

      {action && <section className='reviewed-correction-detail' aria-label={`Wybrana decyzja: ${actionLabel}`}>
        {action === 'mixed_players' && <div className='reviewed-mixed-confirmation'>
          <strong>Zmieszani gracze</strong>
          <p>Ten przypadek zostanie przeniesiony do osobnego kroku, gdzie będzie można rozdzielić jego fragmenty.</p>
          <label>Opcjonalna wskazówka
            <select value={mixedHint} onChange={(event) => setMixedHint(event.target.value as typeof mixedHint)} disabled={busy}>
              <option value='unknown'>Inna mieszanka / nie wiem</option>
              <option value='cross_team'>Team A + Team B</option>
              <option value='same_team_a'>Kilku graczy Team A</option>
              <option value='same_team_b'>Kilku graczy Team B</option>
              <option value='player_referee'>Gracz + sędzia</option>
            </select>
          </label>
        </div>}
        {action === 'assign_team' && <p className='reviewed-correction-range'>Przypiszę tylko {teamLabelForOperator(selectedTeamLabel)}. Nie powstanie nowy slot ani indywidualne statystyki zawodnika.</p>}
        {action === 'assign_roster_player' && <label>Zawodnik z kadry — Team A lub Team B
          <select value={playerId} onChange={(event) => chooseRosterPlayer(event.target.value)} disabled={busy}>
            <option value=''>Wybierz zawodnika</option>
            {(['A', 'B'] as const).map((teamLabel) => rosterOptionsByTeam[teamLabel].length > 0 && <optgroup
              key={teamLabel}
              label={teamLabelForOperator(teamLabel)}
            >
              {rosterOptionsByTeam[teamLabel].map((player) => <option key={player.player_id} value={player.player_id}>
                {player.player_name}{player.roster_number ? ` #${player.roster_number}` : ''}
              </option>)}
            </optgroup>)}
          </select>
          {selectedRosterPlayer && sourceTeamLabel !== 'U' && selectedRosterPlayer.team_label !== sourceTeamLabel
            && <span className='reviewed-correction-team-override'>
              Wybór {selectedRosterPlayer.player_name} poprawi również drużynę z {teamLabelForOperator(sourceTeamLabel)} na {teamLabelForOperator(selectedRosterPlayer.team_label)}.
            </span>}
        </label>}
        {action === 'assign_existing_slot' && <label>Wybierz istniejącego zawodnika
          <select value={stableSlotId} onChange={(event) => setStableSlotId(event.target.value)} disabled={busy}>
            <option value=''>Wybierz gracza</option>
            {options.slots.map((slot) => <option key={slot.stable_slot_id} value={slot.stable_slot_id}>{slot.stable_slot_id}</option>)}
          </select>
        </label>}
        {action === 'create_new_stable_player' && <p className='reviewed-correction-range'>Utworzy nowego, odrębnego anonimowego zawodnika w {teamLabelForOperator(selectedTeamLabel)}.</p>}
        {!['assign_team', 'assign_roster_player', 'assign_existing_slot', 'create_new_stable_player', 'mixed_players'].includes(action)
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

    {navigation ? <footer className='reviewed-correction-navigation'>
      <button type='button' className='secondary' onClick={navigation.onPrevious} disabled={busy || navigation.previousDisabled}>Poprzedni</button>
      <button type='button' onClick={() => void save()} disabled={busy || !context || !choiceComplete}>{navigation.saveLabel}</button>
      <button type='button' className='secondary' onClick={navigation.onNext} disabled={busy || navigation.nextDisabled} title='Przejdź bez zapisywania'>Następny</button>
    </footer> : <div className='row'>
      <button type='button' onClick={() => void save()} disabled={busy || !context || !choiceComplete}>Zapisz poprawkę</button>
      <button type='button' className='secondary' onClick={onCancel} disabled={busy}>Anuluj</button>
    </div>}
  </div>;
}
