import { useEffect, useMemo, useState } from 'react';
import {
  artifactUrl,
  generateIdentityReviewGallery,
  getIdentityReviewGallery,
  getPlayerIdentityReview,
  savePlayerIdentityAssignments,
  splitIdentityReviewGallery,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type {
  IdentityReviewGalleryDocument,
  IdentityReviewGalleryPlayer,
  IdentityReviewGalleryStint,
  Match,
  PlayerIdentityAssignment,
  PlayerIdentityAssignmentStatus,
  PlayerIdentityReviewState,
  Team,
} from '../types';
import {
  selectedCropFrames,
  splitFramesForSelection,
  type CropFrameRange,
} from '../utils/identityReview';

interface IdentityReviewGalleryPanelProps {
  match: Match;
  onStatus: (message: string) => void;
  onSaved: () => Promise<void> | void;
}

type ReviewTarget = {
  key: string;
  player: IdentityReviewGalleryPlayer;
  stint: IdentityReviewGalleryStint;
};

type TeamFilter = 'A' | 'B' | 'U' | 'all';

type AssignmentDraft = {
  status: PlayerIdentityAssignmentStatus;
  player_id: string;
};

type RosterPlayerOption = {
  player_id: string;
  player_name: string;
  player_number?: string | null;
  player_role?: string | null;
  team_id?: string | null;
  team_name: string;
  team_label: 'A' | 'B' | 'U';
};

const identityStatuses: Array<{ value: PlayerIdentityAssignmentStatus; label: string }> = [
  { value: 'unassigned', label: 'Nieprzypisany' },
  { value: 'assigned', label: 'Przypisany do zawodnika' },
  { value: 'unknown', label: 'Niepewny' },
  { value: 'ignore', label: 'Ignoruj' },
  { value: 'referee', label: 'Sedzia / techniczny' },
  { value: 'false_positive', label: 'Falszywa detekcja' },
  { value: 'wrong_target', label: 'Bledny bbox / inna osoba' },
];

function teamLabelForIndex(index: number): 'A' | 'B' | 'U' {
  if (index === 0) return 'A';
  if (index === 1) return 'B';
  return 'U';
}

function rosterPlayerOptions(teams: Team[]): RosterPlayerOption[] {
  return teams.flatMap((team, teamIndex) =>
    (team.players || [])
      .filter((player) => Boolean(player.id))
      .map((player) => ({
        player_id: String(player.id),
        player_name: player.name,
        player_number: player.number,
        player_role: player.role,
        team_id: team.id || null,
        team_name: team.name,
        team_label: teamLabelForIndex(teamIndex),
      })),
  );
}

function rosterPlayerLabel(player: RosterPlayerOption): string {
  const number = player.player_number ? `#${player.player_number} ` : '';
  const role = player.player_role && player.player_role !== 'player' ? ` - ${player.player_role}` : '';
  return `Team ${player.team_label} - ${number}${player.player_name}${role}`;
}

function targetKey(player: IdentityReviewGalleryPlayer, stint: IdentityReviewGalleryStint): string {
  return `${player.stable_subject_id}::${stint.stint_id}`;
}

function secondsLabel(value: number | null | undefined): string {
  return typeof value === 'number' ? `${value.toFixed(1)}s` : 'n/a';
}

function frameRangeLabel(stint: IdentityReviewGalleryStint): string {
  if (typeof stint.start_frame === 'number' && typeof stint.end_frame === 'number') {
    return `f${stint.start_frame}-${stint.end_frame}`;
  }
  return 'frames n/a';
}

function explicitAssignment(
  identityReview: PlayerIdentityReviewState | null,
  target: ReviewTarget | null,
): PlayerIdentityAssignment | null {
  if (!identityReview || !target) return null;
  return identityReview.player_identity_assignments.assignments.find(
    (assignment) =>
      assignment.stable_subject_id === target.player.stable_subject_id &&
      assignment.stint_id === target.stint.stint_id,
  ) || null;
}

function slotAssignment(
  identityReview: PlayerIdentityReviewState | null,
  target: ReviewTarget | null,
): PlayerIdentityAssignment | null {
  if (!identityReview || !target) return null;
  return identityReview.player_identity_assignments.assignments.find(
    (assignment) =>
      assignment.stable_subject_id === target.player.stable_subject_id &&
      !assignment.stint_id,
  ) || null;
}

function targetAssignment(
  identityReview: PlayerIdentityReviewState | null,
  target: ReviewTarget | null,
): PlayerIdentityAssignment | null {
  return explicitAssignment(identityReview, target) || slotAssignment(identityReview, target);
}

function draftFromAssignment(assignment: PlayerIdentityAssignment | null): AssignmentDraft {
  return {
    status: assignment?.status || 'unassigned',
    player_id: assignment?.player_id || '',
  };
}

function teamMatchesFilter(target: ReviewTarget, filter: TeamFilter): boolean {
  return filter === 'all' || target.player.team_label === filter;
}

function teamFilterLabel(filter: TeamFilter): string {
  if (filter === 'all') return 'wszystkie';
  return `Team ${filter}`;
}

export function IdentityReviewGalleryPanel({
  match,
  onStatus,
  onSaved,
}: IdentityReviewGalleryPanelProps) {
  const [gallery, setGallery] = useState<IdentityReviewGalleryDocument | null>(null);
  const [identityReview, setIdentityReview] = useState<PlayerIdentityReviewState | null>(null);
  const [selectedKey, setSelectedKey] = useState('');
  const [teamFilter, setTeamFilter] = useState<TeamFilter>('A');
  const [samplesPerStint, setSamplesPerStint] = useState(8);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchIndex, setBatchIndex] = useState(0);
  const [drafts, setDrafts] = useState<Record<string, AssignmentDraft>>({});
  const [cropRange, setCropRange] = useState<CropFrameRange | null>(null);

  const targets = useMemo<ReviewTarget[]>(() => {
    if (!gallery) return [];
    return gallery.players.flatMap((player) =>
      player.stints.map((stint) => ({
        key: targetKey(player, stint),
        player,
        stint,
      })),
    );
  }, [gallery]);

  const visibleTargets = useMemo(
    () => targets.filter((target) => teamMatchesFilter(target, teamFilter)),
    [targets, teamFilter],
  );

  const selectedTarget = useMemo(
    () => visibleTargets.find((target) => target.key === selectedKey) || visibleTargets[0] || null,
    [selectedKey, visibleTargets],
  );

  const teams = match.teams || [];
  const rosterOptions = useMemo(() => rosterPlayerOptions(teams), [teams]);
  const filteredRosterOptions = useMemo(
    () =>
      teamFilter === 'all'
        ? rosterOptions
        : rosterOptions.filter((player) => player.team_label === teamFilter),
    [rosterOptions, teamFilter],
  );
  const selectedAssignment = explicitAssignment(identityReview, selectedTarget);
  const inheritedAssignment = slotAssignment(identityReview, selectedTarget);
  const targetCounts = useMemo(
    () => ({
      A: targets.filter((target) => target.player.team_label === 'A').length,
      B: targets.filter((target) => target.player.team_label === 'B').length,
      U: targets.filter((target) => target.player.team_label === 'U').length,
      all: targets.length,
    }),
    [targets],
  );
  const currentBatchTarget = visibleTargets[batchIndex] || visibleTargets[0] || null;
  const currentBatchDraft = currentBatchTarget
    ? drafts[currentBatchTarget.key] || draftFromAssignment(targetAssignment(identityReview, currentBatchTarget))
    : null;
  const reviewedDrafts = visibleTargets.filter((target) => {
    const draft = drafts[target.key] || draftFromAssignment(targetAssignment(identityReview, target));
    return draft.status !== 'unassigned' || Boolean(draft.player_id);
  }).length;
  const selectedFrames = useMemo(
    () => selectedCropFrames(selectedTarget?.stint.crops || [], cropRange),
    [cropRange, selectedTarget],
  );
  const batchSelectedFrames = useMemo(
    () => selectedCropFrames(currentBatchTarget?.stint.crops || [], cropRange),
    [cropRange, currentBatchTarget],
  );

  function selectCrop(frame: number) {
    setCropRange((current) => {
      if (!current || current.anchorFrame !== current.focusFrame) {
        return { anchorFrame: frame, focusFrame: frame };
      }
      if (current.anchorFrame === frame) return null;
      return { ...current, focusFrame: frame };
    });
  }

  async function splitCropRange(target: ReviewTarget) {
    const targetSelectedFrames = selectedCropFrames(target.stint.crops, cropRange);
    const frames = splitFramesForSelection(target.stint, target.stint.crops, cropRange);
    if (frames.length === 0) {
      onStatus('Zaznacz crop albo zakres, ktory nie obejmuje calego stintu.');
      return;
    }
    setGenerating(true);
    try {
      const parentStintId = target.stint.parent_stint_id || target.stint.stint_id;
      const updatedGallery = await splitIdentityReviewGallery(
        match.id,
        frames.map((frame) => ({
          stable_subject_id: target.player.stable_subject_id,
          parent_stint_id: parentStintId,
          frame,
          reason: 'manual_crop_range',
        })),
        samplesPerStint,
      );
      const updatedIdentity = await getPlayerIdentityReview(match.id).catch(() => null);
      setGallery(updatedGallery);
      setIdentityReview(updatedIdentity);
      const midpoint = targetSelectedFrames[Math.floor(targetSelectedFrames.length / 2)] || frames[0];
      const updatedPlayer = updatedGallery.players.find(
        (player) => player.stable_subject_id === target.player.stable_subject_id,
      );
      const updatedStint = updatedPlayer?.stints.find(
        (stint) =>
          typeof stint.start_frame === 'number' &&
          typeof stint.end_frame === 'number' &&
          stint.start_frame <= midpoint &&
          stint.end_frame >= midpoint,
      );
      if (updatedPlayer && updatedStint) setSelectedKey(targetKey(updatedPlayer, updatedStint));
      setCropRange(null);
      setBatchOpen(false);
      onStatus(`Wydzielono zakres w ${parentStintId}. Galeria zostala przebudowana.`);
    } catch (error) {
      onStatus(`Nie udalo sie przeciac stintu: ${errorMessage(error)}`);
    } finally {
      setGenerating(false);
    }
  }

  async function load() {
    setLoading(true);
    try {
      const [galleryData, identityData] = await Promise.all([
        getIdentityReviewGallery(match.id).catch(() => null),
        getPlayerIdentityReview(match.id).catch(() => null),
      ]);
      setGallery(galleryData);
      setIdentityReview(identityData);
      const targetKeys = galleryData?.players.flatMap((player) =>
        player.stints.map((stint) => targetKey(player, stint)),
      ) || [];
      setSelectedKey((current) => (current && targetKeys.includes(current) ? current : targetKeys[0] || ''));
      if (galleryData) {
        onStatus(`Zaladowano ${galleryData.summary.crops} cropow identity review.`);
      }
    } catch (error) {
      setGallery(null);
      setIdentityReview(null);
      onStatus(`Nie mozna zaladowac identity review gallery: ${errorMessage(error)}`);
    } finally {
      setLoading(false);
    }
  }

  async function generate(force: boolean) {
    setGenerating(true);
    try {
      const requestedSamples = Math.max(1, Math.min(24, Math.trunc(samplesPerStint || 8)));
      setSamplesPerStint(requestedSamples);
      onStatus(`Generuje cropy identity review (${requestedSamples} na stint)...`);
      const galleryData = await generateIdentityReviewGallery(match.id, requestedSamples, force);
      const identityData = await getPlayerIdentityReview(match.id).catch(() => null);
      setGallery(galleryData);
      setIdentityReview(identityData);
      const firstTarget = galleryData.players.flatMap((player) =>
        player.stints.map((stint) => targetKey(player, stint)),
      )[0];
      setSelectedKey(firstTarget || '');
      setCropRange(null);
      onStatus(
        `Gotowe: ${galleryData.summary.crops} cropow dla ${galleryData.summary.stints_with_crops}/${galleryData.summary.stints} stintow.`,
      );
    } catch (error) {
      onStatus(`Nie udalo sie wygenerowac cropow: ${errorMessage(error)}`);
    } finally {
      setGenerating(false);
    }
  }

  async function saveStintAssignment(patch: Partial<PlayerIdentityAssignment>) {
    if (!selectedTarget) return;
    setSaving(true);
    try {
      const base: PlayerIdentityAssignment = {
        stable_subject_id: selectedTarget.player.stable_subject_id,
        stable_player_id: selectedTarget.player.stable_player_id,
        slot_id: selectedTarget.player.slot_id,
        stint_id: selectedTarget.stint.stint_id,
        assignment_scope: 'stint',
        status: 'unassigned',
        team_label: selectedTarget.player.team_label,
        team_id: selectedTarget.player.team_id,
        team_name: selectedTarget.player.team_name,
      };
      const updated = await savePlayerIdentityAssignments(match.id, [{ ...base, ...selectedAssignment, ...patch }]);
      setIdentityReview(updated);
      await onSaved();
      onStatus(`Zapisano przypisanie stintu ${selectedTarget.stint.stint_id}.`);
    } catch (error) {
      onStatus(`Nie udalo sie zapisac przypisania stintu: ${errorMessage(error)}`);
    } finally {
      setSaving(false);
    }
  }

  function assignRosterPlayer(playerId: string) {
    const rosterPlayer = rosterOptions.find((item) => item.player_id === playerId);
    if (!rosterPlayer) {
      void saveStintAssignment({
        status: 'unassigned',
        player_id: null,
        player_name: null,
        player_number: null,
        player_role: null,
      });
      return;
    }
    void saveStintAssignment({
      status: 'assigned',
      player_id: rosterPlayer.player_id,
      player_name: rosterPlayer.player_name,
      player_number: rosterPlayer.player_number,
      player_role: rosterPlayer.player_role,
      team_id: rosterPlayer.team_id,
      team_name: rosterPlayer.team_name,
      team_label: rosterPlayer.team_label,
    });
  }

  function openBatchReview() {
    const nextDrafts: Record<string, AssignmentDraft> = {};
    visibleTargets.forEach((target) => {
      nextDrafts[target.key] = draftFromAssignment(targetAssignment(identityReview, target));
    });
    setDrafts(nextDrafts);
    setBatchIndex(0);
    setBatchOpen(true);
  }

  function updateBatchDraft(target: ReviewTarget, patch: Partial<AssignmentDraft>) {
    setDrafts((current) => {
      const base = current[target.key] || draftFromAssignment(targetAssignment(identityReview, target));
      return {
        ...current,
        [target.key]: {
          ...base,
          ...patch,
        },
      };
    });
  }

  function assignmentFromDraft(target: ReviewTarget, draft: AssignmentDraft): PlayerIdentityAssignment {
    const rosterPlayer = draft.status === 'assigned'
      ? rosterOptions.find((player) => player.player_id === draft.player_id) || null
      : null;
    return {
      stable_subject_id: target.player.stable_subject_id,
      stable_player_id: target.player.stable_player_id,
      slot_id: target.player.slot_id,
      stint_id: target.stint.stint_id,
      assignment_scope: 'stint',
      status: rosterPlayer ? 'assigned' : draft.status === 'assigned' ? 'unassigned' : draft.status,
      team_label: rosterPlayer?.team_label || target.player.team_label,
      team_id: rosterPlayer?.team_id || target.player.team_id,
      team_name: rosterPlayer?.team_name || target.player.team_name,
      player_id: rosterPlayer?.player_id || null,
      player_name: rosterPlayer?.player_name || null,
      player_number: rosterPlayer?.player_number || null,
      player_role: rosterPlayer?.player_role || null,
    };
  }

  async function saveBatchReview() {
    const invalidTarget = visibleTargets.find((target) => {
      const draft = drafts[target.key] || draftFromAssignment(targetAssignment(identityReview, target));
      return draft.status === 'assigned' && !draft.player_id;
    });
    if (invalidTarget) {
      setBatchIndex(Math.max(0, visibleTargets.findIndex((target) => target.key === invalidTarget.key)));
      onStatus(`Wybierz zawodnika dla ${invalidTarget.stint.stint_id} albo zmien status.`);
      return;
    }
    setSaving(true);
    try {
      const assignments = visibleTargets.map((target) =>
        assignmentFromDraft(
          target,
          drafts[target.key] || draftFromAssignment(targetAssignment(identityReview, target)),
        ),
      );
      const updated = await savePlayerIdentityAssignments(match.id, assignments);
      setIdentityReview(updated);
      setBatchOpen(false);
      await onSaved();
      onStatus(`Zapisano ${assignments.length} przypisan stintow dla ${teamFilterLabel(teamFilter)}.`);
    } catch (error) {
      onStatus(`Nie udalo sie zapisac galerii stintow: ${errorMessage(error)}`);
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => {
    if (match.analysis_report?.status === 'completed') {
      load().catch((error) => onStatus(errorMessage(error)));
    } else {
      setGallery(null);
      setIdentityReview(null);
      setSelectedKey('');
      setBatchOpen(false);
      setDrafts({});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id, match.analysis_report?.status]);

  if (match.analysis_report?.status !== 'completed') {
    return null;
  }

  return (
    <div className='identity-gallery-panel'>
      <div className='row between'>
        <div>
          <h3>Identity crop review</h3>
          <p className='muted'>
            Sparse cropy per stint do szybkiego sprawdzenia realnej osoby bez renderowania calego overlayu.
          </p>
        </div>
        <div className='row'>
          <label className='compact-field'>
            Druzyna
            <select
              value={teamFilter}
              onChange={(event) => {
                setTeamFilter(event.target.value as TeamFilter);
                setSelectedKey('');
                setCropRange(null);
                setBatchIndex(0);
              }}
            >
              <option value='A'>Team A ({targetCounts.A})</option>
              <option value='B'>Team B ({targetCounts.B})</option>
              <option value='U'>Unknown ({targetCounts.U})</option>
              <option value='all'>Wszystkie ({targetCounts.all})</option>
            </select>
          </label>
          <label className='compact-field'>
            Cropy / stint
            <input
              type='number'
              min={1}
              max={24}
              value={samplesPerStint}
              onChange={(event) => setSamplesPerStint(Number(event.target.value || 8))}
            />
          </label>
          <button type='button' className='secondary' onClick={load} disabled={loading || generating}>
            Odswiez
          </button>
          <button type='button' onClick={() => generate(Boolean(gallery))} disabled={generating}>
            {gallery ? 'Przebuduj cropy' : 'Wygeneruj cropy'}
          </button>
          <button
            type='button'
            className='secondary'
            onClick={openBatchReview}
            disabled={!gallery || visibleTargets.length === 0}
          >
            Review stintow ({visibleTargets.length})
          </button>
        </div>
      </div>

      {!gallery ? (
        <p className='muted'>
          {loading ? 'Laduje crop gallery...' : 'Brak wygenerowanych cropow identity review dla tego meczu.'}
        </p>
      ) : (
        <>
          <div className='chips'>
            <span>Players: {gallery.summary.stable_players}</span>
            <span>Stints: {gallery.summary.stints}</span>
            <span>With crops: {gallery.summary.stints_with_crops}</span>
            <span>Crops: {gallery.summary.crops}</span>
            <span>Auto split: {gallery.summary.automatic_splits || 0}</span>
            <span>Manual split: {gallery.summary.manual_splits || 0}</span>
            <span>Mixed: {gallery.summary.mixed_segments || 0}</span>
            <span>Widoczne: {visibleTargets.length}</span>
            <span>Generated: {new Date(gallery.generated_at).toLocaleString()}</span>
          </div>

          <div className='grid two identity-gallery-grid'>
            <div className='stable-player-list identity-stint-list'>
              {visibleTargets.map((target) => {
                const assignment = explicitAssignment(identityReview, target);
                return (
                  <button
                    type='button'
                    className={
                      target.key === selectedTarget?.key
                        ? 'match-item active stable-player-item'
                        : 'match-item stable-player-item'
                    }
                    key={target.key}
                    onClick={() => {
                      setSelectedKey(target.key);
                      setCropRange(null);
                    }}
                  >
                    <strong>
                      {target.player.stable_player_id} / {target.stint.stint_id}
                    </strong>
                    <span>
                      Team {target.player.team_label || 'U'} - {secondsLabel(target.stint.start_time_sec)}-
                      {secondsLabel(target.stint.end_time_sec)} - {frameRangeLabel(target.stint)} - cropy{' '}
                      {target.stint.crops.length}/{target.stint.candidate_positions} -{' '}
                      {assignment?.player_name || assignment?.status || 'no stint assignment'}
                    </span>
                  </button>
                );
              })}
            </div>

            <div className='team-card identity-stint-detail'>
              {selectedTarget ? (
                <div className='stack'>
                  <div className='row between'>
                    <div>
                      <h4>
                        {selectedTarget.player.stable_player_id} / {selectedTarget.stint.stint_id}
                      </h4>
                      <p className='muted'>
                        {secondsLabel(selectedTarget.stint.start_time_sec)}-
                        {secondsLabel(selectedTarget.stint.end_time_sec)} - {frameRangeLabel(selectedTarget.stint)}
                      </p>
                    </div>
                    <span className='confidence-pill medium'>
                      {selectedTarget.stint.crops.length} crops
                    </span>
                  </div>

                  {selectedTarget.stint.appearance_purity !== 'consistent' && (
                    <p className='identity-review-warning'>
                      Wyglad celu zmienia sie w tym segmencie ({selectedTarget.stint.appearance_purity}).
                      Sprawdz zaznaczony bbox i w razie potrzeby wydziel zakres cropow.
                    </p>
                  )}

                  <div className='grid two compact'>
                    <label>
                      Zawodnik dla tego stintu
                      <select
                        value={selectedAssignment?.player_id || ''}
                        disabled={saving || rosterOptions.length === 0}
                        onChange={(event) => assignRosterPlayer(event.target.value)}
                      >
                        <option value=''>Nie przypisano</option>
                        {rosterOptions.map((player) => (
                          <option value={player.player_id} key={player.player_id}>
                            {rosterPlayerLabel(player)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Status stintu
                      <select
                        value={selectedAssignment?.status || 'unassigned'}
                        disabled={saving}
                        onChange={(event) => {
                          const status = event.target.value as PlayerIdentityAssignmentStatus;
                          if (status === 'assigned' && !selectedAssignment?.player_id) {
                            onStatus('Najpierw wybierz zawodnika dla tego stintu.');
                            return;
                          }
                          void saveStintAssignment({
                            status,
                            player_id: status === 'assigned' ? selectedAssignment?.player_id || null : null,
                            player_name: status === 'assigned' ? selectedAssignment?.player_name || null : null,
                            player_number: status === 'assigned' ? selectedAssignment?.player_number || null : null,
                            player_role: status === 'assigned' ? selectedAssignment?.player_role || null : null,
                          });
                        }}
                      >
                        {identityStatuses.map((status) => (
                          <option value={status.value} key={status.value}>
                            {status.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  <div className='chips'>
                    <span>Explicit: {selectedAssignment?.player_name || selectedAssignment?.status || 'none'}</span>
                    <span>Slot default: {inheritedAssignment?.player_name || inheritedAssignment?.status || 'none'}</span>
                    <span>Raw IDs: {selectedTarget.stint.raw_track_ids.join(', ') || 'n/a'}</span>
                    <span>Tracklets: {selectedTarget.stint.tracklet_ids.join(', ') || 'n/a'}</span>
                    <span>Parent: {selectedTarget.stint.parent_stint_id || selectedTarget.stint.stint_id}</span>
                  </div>

                  <div className='row between identity-crop-actions'>
                    <span className='muted'>Kliknij pierwszy i ostatni crop osoby, ktora chcesz wydzielic.</span>
                    <button
                      type='button'
                      className='secondary'
                      disabled={generating || selectedFrames.length === 0}
                      onClick={() => void splitCropRange(selectedTarget)}
                    >
                      Wydziel zakres ({selectedFrames.length})
                    </button>
                  </div>

                  {selectedTarget.stint.crops.length > 0 ? (
                    <div className='identity-crop-grid'>
                      {selectedTarget.stint.crops.map((crop) => (
                        <figure
                          className={selectedFrames.includes(crop.frame) ? 'identity-crop selected' : 'identity-crop'}
                          key={crop.artifact}
                          onClick={() => selectCrop(crop.frame)}
                        >
                          <img
                            src={artifactUrl(match.id, crop.artifact)}
                            alt={`${selectedTarget.stint.stint_id} frame ${crop.frame}`}
                          />
                          <figcaption>
                            f{crop.frame} / {secondsLabel(crop.time_sec)} / conf{' '}
                            {typeof crop.confidence === 'number' ? crop.confidence.toFixed(2) : 'n/a'}
                          </figcaption>
                        </figure>
                      ))}
                    </div>
                  ) : (
                    <p className='muted'>Brak cropow dla tego stintu.</p>
                  )}
                </div>
              ) : (
                <p className='muted'>Brak stintow w galerii.</p>
              )}
            </div>
          </div>
        </>
      )}

      {batchOpen && currentBatchTarget && currentBatchDraft && (
        <div className='identity-review-modal' role='dialog' aria-modal='true'>
          <div className='identity-review-modal-card'>
            <div className='row between'>
              <div>
                <h3>Galeria stintow - {teamFilterLabel(teamFilter)}</h3>
                <p className='muted'>
                  {batchIndex + 1}/{visibleTargets.length} - zapis zbiorczy na koncu sesji
                </p>
              </div>
              <div className='row'>
                <button
                  type='button'
                  className='secondary'
                  onClick={() => setBatchOpen(false)}
                  disabled={saving}
                >
                  Zamknij
                </button>
                <button type='button' onClick={saveBatchReview} disabled={saving || visibleTargets.length === 0}>
                  {saving ? 'Zapisywanie...' : 'Zapisz wszystko'}
                </button>
              </div>
            </div>

            <div className='identity-review-progress'>
              <progress value={batchIndex + 1} max={Math.max(1, visibleTargets.length)} />
              <span>
                Oznaczone: {reviewedDrafts}/{visibleTargets.length}
              </span>
            </div>

            <div className='identity-review-modal-layout'>
              <div className='identity-modal-stint-list'>
                {visibleTargets.map((target, index) => {
                  const draft = drafts[target.key] || draftFromAssignment(targetAssignment(identityReview, target));
                  const rosterPlayer = rosterOptions.find((player) => player.player_id === draft.player_id);
                  return (
                    <button
                      type='button'
                      key={target.key}
                      className={index === batchIndex ? 'match-item active stable-player-item' : 'match-item stable-player-item'}
                      onClick={() => {
                        setBatchIndex(index);
                        setCropRange(null);
                      }}
                    >
                      <strong>
                        {index + 1}. {target.player.stable_player_id} / {target.stint.stint_id}
                      </strong>
                      <span>
                        {secondsLabel(target.stint.start_time_sec)}-{secondsLabel(target.stint.end_time_sec)} -{' '}
                        {rosterPlayer?.player_name || draft.status}
                      </span>
                    </button>
                  );
                })}
              </div>

              <div className='identity-modal-main'>
                <div className='row between'>
                  <div>
                    <h4>
                      {currentBatchTarget.player.stable_player_id} / {currentBatchTarget.stint.stint_id}
                    </h4>
                    <p className='muted'>
                      {secondsLabel(currentBatchTarget.stint.start_time_sec)}-
                      {secondsLabel(currentBatchTarget.stint.end_time_sec)} -{' '}
                      {frameRangeLabel(currentBatchTarget.stint)}
                    </p>
                  </div>
                  <span className='confidence-pill medium'>
                    {currentBatchTarget.stint.crops.length} crops
                  </span>
                </div>

                <div className='grid two compact'>
                  <label>
                    Zawodnik
                    <select
                      value={currentBatchDraft.player_id}
                      disabled={saving || filteredRosterOptions.length === 0}
                      onChange={(event) => {
                        const playerId = event.target.value;
                        updateBatchDraft(currentBatchTarget, {
                          player_id: playerId,
                          status: playerId ? 'assigned' : 'unassigned',
                        });
                      }}
                    >
                      <option value=''>Nie przypisano</option>
                      {filteredRosterOptions.map((player) => (
                        <option value={player.player_id} key={player.player_id}>
                          {rosterPlayerLabel(player)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Status
                    <select
                      value={currentBatchDraft.status}
                      disabled={saving}
                      onChange={(event) => {
                        const status = event.target.value as PlayerIdentityAssignmentStatus;
                        updateBatchDraft(currentBatchTarget, {
                          status,
                          player_id: status === 'assigned' ? currentBatchDraft.player_id : '',
                        });
                      }}
                    >
                      {identityStatuses.map((status) => (
                        <option value={status.value} key={status.value}>
                          {status.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                <div className='chips'>
                  <span>Team: {currentBatchTarget.player.team_label || 'U'}</span>
                  <span>Raw IDs: {currentBatchTarget.stint.raw_track_ids.join(', ') || 'n/a'}</span>
                  <span>Tracklets: {currentBatchTarget.stint.tracklet_ids.join(', ') || 'n/a'}</span>
                  <span>Det: {currentBatchTarget.stint.detected_frames ?? 'n/a'}</span>
                  <span>Amb: {currentBatchTarget.stint.ambiguous_frames ?? 'n/a'}</span>
                </div>

                <div className='row between identity-crop-actions'>
                  <span className='muted'>Zaznacz crop lub zakres, jesli stint zawiera inna osobe.</span>
                  <button
                    type='button'
                    className='secondary'
                    disabled={generating || batchSelectedFrames.length === 0}
                    onClick={() => void splitCropRange(currentBatchTarget)}
                  >
                    Wydziel zakres ({batchSelectedFrames.length})
                  </button>
                </div>

                {currentBatchTarget.stint.crops.length > 0 ? (
                  <div className='identity-crop-grid identity-modal-crop-grid'>
                    {currentBatchTarget.stint.crops.map((crop) => (
                      <figure
                        className={batchSelectedFrames.includes(crop.frame) ? 'identity-crop selected' : 'identity-crop'}
                        key={crop.artifact}
                        onClick={() => selectCrop(crop.frame)}
                      >
                        <img
                          src={artifactUrl(match.id, crop.artifact)}
                          alt={`${currentBatchTarget.stint.stint_id} frame ${crop.frame}`}
                        />
                        <figcaption>
                          f{crop.frame} / {secondsLabel(crop.time_sec)} / conf{' '}
                          {typeof crop.confidence === 'number' ? crop.confidence.toFixed(2) : 'n/a'}
                        </figcaption>
                      </figure>
                    ))}
                  </div>
                ) : (
                  <p className='muted'>Brak cropow dla tego stintu.</p>
                )}

                <div className='row between identity-modal-footer'>
                  <button
                    type='button'
                    className='secondary'
                    onClick={() => setBatchIndex((index) => Math.max(0, index - 1))}
                    disabled={batchIndex === 0}
                  >
                    Poprzedni
                  </button>
                  <button
                    type='button'
                    onClick={() => setBatchIndex((index) => Math.min(visibleTargets.length - 1, index + 1))}
                    disabled={batchIndex >= visibleTargets.length - 1}
                  >
                    Nastepny
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
