import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  artifactUrl,
  getPlayerIdentityReview,
  getStablePlayers,
  reviewStablePlayers,
  savePlayerIdentityAssignments,
} from '../api';
import type {
  Match,
  PlayerIdentityAssignment,
  PlayerIdentityAssignmentStatus,
  PlayerIdentityReviewState,
  StablePlayer,
  StablePlayerStatus,
  StablePlayersReviewState,
  Team,
} from '../types';
import { errorMessage } from '../lib/helpers';

interface StablePlayersPanelProps {
  match: Match;
  onStatus: (message: string) => void;
  onSaved: () => Promise<void> | void;
}

const playerStatuses: Array<{ value: StablePlayerStatus; label: string }> = [
  { value: 'active', label: 'Aktywny slot' },
  { value: 'unknown', label: 'Niepewny' },
  { value: 'ignore', label: 'Ignoruj' },
  { value: 'referee', label: 'Sedzia / techniczny' },
  { value: 'false_positive', label: 'Falszywa detekcja' },
];

const identityStatuses: Array<{ value: PlayerIdentityAssignmentStatus; label: string }> = [
  { value: 'unassigned', label: 'Nieprzypisany' },
  { value: 'assigned', label: 'Przypisany do zawodnika' },
  { value: 'unknown', label: 'Niepewny' },
  { value: 'ignore', label: 'Ignoruj' },
  { value: 'referee', label: 'Sedzia / techniczny' },
  { value: 'false_positive', label: 'Falszywa detekcja' },
];

type RosterPlayerOption = {
  player_id: string;
  player_name: string;
  player_number?: string | null;
  player_role?: string | null;
  team_id?: string | null;
  team_name: string;
  team_label: 'A' | 'B' | 'U';
};

function teamLabelForIndex(index: number): 'A' | 'B' | 'U' {
  if (index === 0) return 'A';
  if (index === 1) return 'B';
  return 'U';
}

function teamOptionValue(team: Team, index: number): string {
  return `${teamLabelForIndex(index)}|${team.id || ''}|${team.name}`;
}

function parseTeamOption(value: string) {
  const [team_label, team_id, team_name] = value.split('|');
  return {
    team_label: team_label === 'A' || team_label === 'B' ? team_label : 'U',
    team_id: team_id || null,
    team_name: team_name || 'Unknown',
  } as const;
}

function currentTeamOptionValue(player: StablePlayer, teams: Team[]): string {
  const index = teams.findIndex(
    (team, teamIndex) =>
      team.id === player.team_id && teamLabelForIndex(teamIndex) === player.team_label,
  );
  return index >= 0 ? teamOptionValue(teams[index], index) : '';
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

function confidenceLabel(player: StablePlayer): string {
  const score = player.confidence_score;
  return score == null ? player.confidence : `${player.confidence} ${(score * 100).toFixed(0)}%`;
}

function movementLabel(player: StablePlayer): string {
  const stats = player.movement_stats;
  if (!stats) return 'stats n/a';
  const peakSpeed = stats.peak_sustained_speed_kmh ?? stats.top_speed_kmh;
  return `dist ${stats.total_distance_m.toFixed(1)}m - avg ${stats.avg_speed_kmh.toFixed(1)} km/h - peak ${peakSpeed.toFixed(1)} km/h`;
}

function numberFrom(record: Record<string, unknown> | undefined, key: string): number | null {
  const value = record?.[key];
  return typeof value === 'number' ? value : null;
}

function nestedValue(record: Record<string, unknown> | undefined | null, group: string, key: string): string {
  const groupValue = record?.[group];
  if (!groupValue || typeof groupValue !== 'object' || Array.isArray(groupValue)) return 'n/a';
  const value = (groupValue as Record<string, unknown>)[key];
  if (value == null || value === '') return 'n/a';
  return String(value);
}

function nestedArrayLength(record: Record<string, unknown> | undefined | null, key: string): number {
  const value = record?.[key];
  return Array.isArray(value) ? value.length : 0;
}

function peakSpeedFrom(record: Record<string, unknown> | undefined): number | null {
  return numberFrom(record, 'peak_sustained_speed_kmh') ?? numberFrom(record, 'top_speed_kmh');
}

function PlayerTrajectory({
  player,
  widthM,
  lengthM,
}: {
  player: StablePlayer;
  widthM: number;
  lengthM: number;
}) {
  const points = player.trajectory_m
    .map((point) => point.pitch_m)
    .filter((point): point is number[] => Boolean(point && point.length >= 2))
    .map(([x, y]) => `${(x / widthM) * 100},${(y / lengthM) * 160}`)
    .join(' ');

  return (
    <svg className='trajectory-map' viewBox='0 0 100 160' role='img'>
      <rect x='1' y='1' width='98' height='158' rx='2' />
      <line x1='1' y1='80' x2='99' y2='80' />
      {points && <polyline points={points} />}
    </svg>
  );
}

function PlayerHeatmap({
  matchId,
  player,
}: {
  matchId: string;
  player: StablePlayer;
}) {
  if (!player.heatmap_path) {
    return (
      <div className='player-heatmap-placeholder'>
        <span>Heatmapa będzie dostępna po ponownej analizie meczu.</span>
      </div>
    );
  }
  return (
    <figure className='player-heatmap'>
      <img
        src={artifactUrl(matchId, player.heatmap_path)}
        alt={`Heatmapa ${player.stable_player_id}`}
      />
      <figcaption>
        Heatmapa - {player.heatmap_samples ?? 0} próbek -{' '}
        {player.heatmap_quality || 'unknown'}
      </figcaption>
    </figure>
  );
}

export function StablePlayersPanel({
  match,
  onStatus,
  onSaved,
}: StablePlayersPanelProps) {
  const [review, setReview] = useState<StablePlayersReviewState | null>(null);
  const [identityReview, setIdentityReview] = useState<PlayerIdentityReviewState | null>(null);
  const [selectedId, setSelectedId] = useState('');
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const [data, identityData] = await Promise.all([
        getStablePlayers(match.id),
        getPlayerIdentityReview(match.id).catch(() => null),
      ]);
      setReview(data);
      setIdentityReview(identityData);
      setSelectedId(data.stable_players.players[0]?.stable_subject_id || '');
      onStatus(`Zaladowano ${data.stable_players.summary.stable_players} stabilnych slotow.`);
    } catch (error) {
      setReview(null);
      setIdentityReview(null);
      onStatus(`Brak stabilnych slotow: ${errorMessage(error)}`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (match.analysis_report?.status === 'completed') {
      load().catch((error) => onStatus(errorMessage(error)));
    } else {
      setReview(null);
      setIdentityReview(null);
      setSelectedId('');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id, match.analysis_report?.status]);

  const players = review?.stable_players.players || [];
  const selected = useMemo(
    () =>
      players.find((player) => player.stable_subject_id === selectedId) ||
      players[0],
    [players, selectedId],
  );
  const teams = match.teams || [];
  const rosterOptions = useMemo(() => rosterPlayerOptions(teams), [teams]);
  const selectedIdentity = selected
    ? identityReview?.player_identity_assignments.assignments.find(
        (assignment) =>
          assignment.stable_subject_id === selected.stable_subject_id &&
          !assignment.stint_id,
      ) || null
    : null;
  const resolvedStats = identityReview?.resolved_player_stats || match.resolved_player_stats || null;
  const selectedResolvedStats = selectedIdentity?.player_id
    ? resolvedStats?.players.find((player) => String(player.player_id || '') === selectedIdentity.player_id)
    : null;
  const identitySummary = identityReview?.player_identity_assignments.summary;
  const assignedIdentitySlots = numberFrom(identitySummary, 'assigned_slots') ?? 0;
  const assignedIdentityStints = numberFrom(identitySummary, 'assigned_stints') ?? 0;
  const stableIdentitySlots = numberFrom(identitySummary, 'stable_slots') ?? players.length;
  const anonymousIdentitySlots = Math.max(0, stableIdentitySlots - assignedIdentitySlots);
  const identityConflicts = numberFrom(identitySummary, 'conflicts_total') ?? 0;
  const resolvedPlayerCount = numberFrom(resolvedStats?.summary, 'players') ?? 0;
  const resolvedDistance = numberFrom(resolvedStats?.summary, 'total_distance_m') ?? 0;

  async function saveUpdate(player: StablePlayer, patch: Record<string, unknown>) {
    const updated = await reviewStablePlayers(match.id, {
      updates: [{ stable_subject_id: player.stable_subject_id, ...patch }],
    });
    setReview(updated);
    setSelectedId(player.stable_subject_id);
    await onSaved();
  }

  async function swapTeams() {
    const updated = await reviewStablePlayers(match.id, { swap_teams: true });
    setReview(updated);
    await onSaved();
    onStatus('Zamieniono Team A i Team B dla stabilnych slotow.');
  }

  async function saveIdentityUpdate(player: StablePlayer, patch: Partial<PlayerIdentityAssignment>) {
    if (!identityReview) return;
    const current =
      identityReview.player_identity_assignments.assignments.find(
        (assignment) =>
          assignment.stable_subject_id === player.stable_subject_id &&
          !assignment.stint_id,
      ) || {
        stable_subject_id: player.stable_subject_id,
        stable_player_id: player.stable_player_id,
        slot_id: player.slot_id,
        stint_id: null,
        status: 'unassigned' as PlayerIdentityAssignmentStatus,
        team_label: player.team_label,
        team_id: player.team_id,
        team_name: player.team_name,
      };
    const nextAssignment: PlayerIdentityAssignment = {
      ...current,
      ...patch,
      stable_subject_id: player.stable_subject_id,
      stable_player_id: player.stable_player_id,
      slot_id: player.slot_id,
      stint_id: null,
    };
    const nextAssignments = [
      ...identityReview.player_identity_assignments.assignments.filter(
        (assignment) =>
          assignment.stable_subject_id !== player.stable_subject_id ||
          Boolean(assignment.stint_id),
      ),
      nextAssignment,
    ];
    const updated = await savePlayerIdentityAssignments(match.id, nextAssignments);
    setIdentityReview(updated);
    await onSaved();
    onStatus(`Zapisano przypisanie ${player.stable_player_id} do rosteru.`);
  }

  function assignRosterPlayer(player: StablePlayer, playerId: string) {
    const rosterPlayer = rosterOptions.find((item) => item.player_id === playerId);
    if (!rosterPlayer) {
      void saveIdentityUpdate(player, {
        status: 'unassigned',
        player_id: null,
        player_name: null,
        player_number: null,
        player_role: null,
      });
      return;
    }
    void saveIdentityUpdate(player, {
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

  if (match.analysis_report?.status !== 'completed') {
    return (
      <section className='card'>
        <h2>5. Stabilni zawodnicy</h2>
        <p className='muted'>
          Uruchom analize, zeby system zbudowal anonimowe sloty i stinty zawodnikow.
        </p>
      </section>
    );
  }

  if (!review) {
    return (
      <section className='card'>
        <div className='row between'>
          <div>
            <h2>5. Stabilni zawodnicy</h2>
            <p className='muted'>
              Ten mecz nie ma jeszcze `stable_players.json`. Uruchom ponownie analize.
            </p>
          </div>
          <button type='button' onClick={load} disabled={loading}>
            Odswiez
          </button>
        </div>
      </section>
    );
  }

  const pitch = review.stable_players.pitch_dimensions_m;
  const frameSummary =
    review.global_identity_report?.frame_detection_summary ||
    review.stable_players.frame_detection_summary;
  const movementSummary =
    review.movement_stats?.summary ||
    review.stable_players.movement_stats_summary;
  const problemFrames = review.global_identity_report?.problem_frames || [];
  const lowVisibleFrames = review.global_identity_report?.low_visible_frames || [];
  const ambiguousRanges = review.global_identity_report?.ambiguous_frame_ranges || [];
  const blockedSwitches = review.global_identity_report?.blocked_switches || [];
  const rejectedStartCandidates = review.global_identity_report?.rejected_start_candidates || [];
  const teamClusters = review.team_clusters;

  return (
    <section className='card'>
      <div className='row between'>
        <div>
          <h2>5. Stabilni zawodnicy</h2>
          <p className='muted'>
            Widok pokazuje anonimowe sloty/stinty z global identity resolvera.
            Raw tracker_id zostaje tylko jako debug. Tutaj mozesz przypisac
            stabilny slot do realnego zawodnika z rosteru meczu. Nie musisz
            przypisywac przeciwnika: anonimowe sloty nadal zostaja w analizie,
            ale nie trafiaja do personalnych statystyk po player_id.
          </p>
        </div>
        <div className='row'>
          <button type='button' onClick={load} disabled={loading}>
            Odswiez
          </button>
          <button type='button' className='secondary' onClick={swapTeams}>
            Zamien Team A/B
          </button>
        </div>
      </div>

      <div className='chips'>
        <span>Resolver: {review.stable_players.source || 'legacy'}</span>
        <span>Stable slots: {review.stable_players.summary.stable_players}</span>
        <span>Stinty: {review.stable_players.summary.stints_total ?? 0}</span>
        <span>Predicted: {review.stable_players.summary.predicted_frames ?? 0}</span>
        <span>Missing: {review.stable_players.summary.missing_frames ?? 0}</span>
        <span>Ambiguous: {review.stable_players.summary.ambiguous_frames ?? 0}</span>
        <span>Team switch blocks: {review.stable_players.summary.blocked_team_switches ?? 0}</span>
        <span>Identity blocks: {review.stable_players.summary.blocked_identity_switches ?? 0}</span>
      </div>

      {identityReview && (
        <>
          <p className='muted'>
            Przypisz tylko tych zawodnikow, dla ktorych chcesz miec profil i
            agregacje miedzy meczami. Nieprzypisany przeciwnik zostaje jako
            anonimowy stable slot.
          </p>
          <div className='chips'>
            <span>Assigned real slots: {assignedIdentitySlots}</span>
            <span>Anonymous slots: {anonymousIdentitySlots}</span>
            <span>Assigned stints: {assignedIdentityStints}</span>
            <span>Identity warnings: {identityConflicts}</span>
            <span>Resolved players: {resolvedPlayerCount}</span>
            <span>Resolved dist: {resolvedDistance} m</span>
          </div>
        </>
      )}

      {movementSummary && (
        <div className='chips'>
          <span>Total dist: {numberFrom(movementSummary, 'total_distance_m') ?? 'n/a'} m</span>
          <span>Observed dist: {numberFrom(movementSummary, 'observed_distance_m') ?? 'n/a'} m</span>
          <span>Estimated gaps: {numberFrom(movementSummary, 'estimated_gap_distance_m') ?? 'n/a'} m</span>
          <span>Players estimated: {numberFrom(movementSummary, 'players_with_estimated_distance') ?? 'n/a'}</span>
          <span>Peak sustained: {peakSpeedFrom(movementSummary) ?? 'n/a'} km/h</span>
        </div>
      )}

      {frameSummary && (
        <div className='chips'>
          <span>Raw avg: {numberFrom(frameSummary, 'raw_avg') ?? 'n/a'}</span>
          <span>Visible avg: {numberFrom(frameSummary, 'stable_avg') ?? 'n/a'}</span>
          <span>Active avg: {numberFrom(frameSummary, 'active_slots_avg') ?? 'n/a'}</span>
          <span>Frames ambiguous: {numberFrom(frameSummary, 'frames_with_ambiguous_slots') ?? 'n/a'}</span>
          <span>Frames with missing slots: {numberFrom(frameSummary, 'frames_with_missing_slots') ?? 'n/a'}</span>
          <span>Ghost boxes: {numberFrom(frameSummary, 'ghost_bbox_count') ?? 'n/a'}</span>
        </div>
      )}

      {teamClusters && (
        <div className='chips'>
          <span>Team method: {teamClusters.method}</span>
          <span>Team refs: {teamClusters.reference_tracklets_count ?? 'n/a'}</span>
          <span>White refs: {teamClusters.white_reference_tracklets_count ?? 'n/a'}</span>
          <span>Bib refs: {teamClusters.bib_reference_tracklets_count ?? 'n/a'}</span>
          <span>GK color outliers: {teamClusters.goalkeeper_color_outliers_count ?? 'n/a'}</span>
          <span>Team candidates: {teamClusters.candidate_tracklets_count ?? 'n/a'}</span>
          <span>Unknown team tracklets: {teamClusters.unknown_tracklets.length}</span>
        </div>
      )}

      <div className='grid two stable-grid'>
        <div className='stable-player-list'>
          {players.map((player) => (
            <button
              type='button'
              className={
                player.stable_subject_id === selected?.stable_subject_id
                  ? 'match-item active stable-player-item'
                  : 'match-item stable-player-item'
              }
              key={player.stable_subject_id}
              onClick={() => setSelectedId(player.stable_subject_id)}
            >
              <strong>{player.stable_player_id}</strong>
              <span>
                {player.team_name || `Team ${player.team_label}`} -{' '}
                {player.duration_sec.toFixed(1)}s - {player.stint_count ?? 0} stintow -{' '}
                {movementLabel(player)} -{' '}
                det {player.detected_frames ?? player.positions_count} - pred {player.predicted_frames ?? 0} -{' '}
                miss {player.missing_frames ?? 0} - amb {player.ambiguous_frames ?? 0} - {confidenceLabel(player)}
              </span>
            </button>
          ))}
        </div>

        <div className='team-card'>
          {selected ? (
            <div className='stack'>
              <div className='row between'>
                <div>
                  <h3>{selected.stable_player_id}</h3>
                  <p className='muted'>
                    {selected.stable_subject_id} - {selected.identity_semantics || 'legacy'} -{' '}
                    {selected.tracklet_ids.join(', ') || 'brak trackletow'}
                  </p>
                </div>
                <span className={`confidence-pill ${selected.confidence}`}>
                  {confidenceLabel(selected)}
                </span>
              </div>

              <div className='player-detail-media'>
                <PlayerTrajectory
                  player={selected}
                  widthM={pitch.width_m}
                  lengthM={pitch.length_m}
                />
                <PlayerHeatmap matchId={match.id} player={selected} />
              </div>

              <div className='grid two compact'>
                <label>
                  Team
                  <select
                    value={currentTeamOptionValue(selected, teams)}
                    onChange={(event) => {
                      const value = event.target.value;
                      if (!value) {
                        void saveUpdate(selected, {
                          team_label: 'U',
                          team_id: null,
                          team_name: 'Unknown',
                        });
                        return;
                      }
                      void saveUpdate(selected, parseTeamOption(value));
                    }}
                  >
                    <option value=''>Unknown</option>
                    {teams.slice(0, 2).map((team, index) => (
                      <option
                        value={teamOptionValue(team, index)}
                        key={team.id || team.name}
                      >
                        Team {teamLabelForIndex(index)} - {team.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Status
                  <select
                    value={selected.status}
                    onChange={(event) =>
                      void saveUpdate(selected, {
                        status: event.target.value as StablePlayerStatus,
                      })
                    }
                  >
                    {playerStatuses.map((status) => (
                      <option value={status.value} key={status.value}>
                        {status.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div className='identity-review'>
                <div>
                  <h4>Realny zawodnik</h4>
                  <p className='muted'>
                    To zapisuje `player_identity_assignments.json`: stable slot/stint do player_id.
                    Przypisanie jest opcjonalne; slot moze zostac anonimowy.
                  </p>
                </div>
                <div className='grid two compact'>
                  <label>
                    Zawodnik z rosteru
                    <select
                      value={selectedIdentity?.player_id || ''}
                      onChange={(event) => assignRosterPlayer(selected, event.target.value)}
                      disabled={!identityReview || rosterOptions.length === 0}
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
                    Identity status
                    <select
                      value={selectedIdentity?.status || 'unassigned'}
                      disabled={!identityReview}
                      onChange={(event) => {
                        const status = event.target.value as PlayerIdentityAssignmentStatus;
                        if (status === 'assigned' && !selectedIdentity?.player_id) {
                          onStatus('Najpierw wybierz zawodnika z rosteru.');
                          return;
                        }
                        void saveIdentityUpdate(selected, {
                          status,
                          player_id: status === 'assigned' ? selectedIdentity?.player_id || null : null,
                          player_name: status === 'assigned' ? selectedIdentity?.player_name || null : null,
                          player_number: status === 'assigned' ? selectedIdentity?.player_number || null : null,
                          player_role: status === 'assigned' ? selectedIdentity?.player_role || null : null,
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
                  <span>Status: {selectedIdentity?.status || 'unassigned'}</span>
                  <span>Player ID: {selectedIdentity?.player_id || 'n/a'}</span>
                  <span>Player: {selectedIdentity?.player_name || 'n/a'}</span>
                  <span>Scope: {selectedIdentity?.assignment_scope || 'stable_slot'}</span>
                  <span>Stints: {selectedIdentity?.stint_ids?.length ?? selected.stint_count ?? 0}</span>
                </div>
                {selectedIdentity?.player_id && (
                  <div className='row'>
                    <Link to={`/players/${encodeURIComponent(selectedIdentity.player_id)}`}>
                      Otworz profil zawodnika
                    </Link>
                  </div>
                )}
                {(selectedIdentity?.review_warnings?.length || 0) > 0 && (
                  <div className='quality-alert'>
                    <strong>Identity warning</strong>
                    <span>{selectedIdentity?.review_warnings?.join(', ')}</span>
                  </div>
                )}
                {selectedResolvedStats && (
                  <div className='chips'>
                    <span>Resolved dist: {nestedValue(selectedResolvedStats, 'distance', 'total_distance_m')} m</span>
                    <span>Resolved playing: {nestedValue(selectedResolvedStats, 'time', 'playing_time_sec')}s</span>
                    <span>Resolved peak: {nestedValue(selectedResolvedStats, 'speed', 'peak_sustained_speed_kmh')} km/h</span>
                    <span>Resolved sprints: {nestedValue(selectedResolvedStats, 'intensity', 'sprint_count')}</span>
                    <span>Resolved sprint dist: {nestedValue(selectedResolvedStats, 'intensity', 'sprint_distance_m')} m</span>
                    <span>Stable sources: {nestedArrayLength(selectedResolvedStats, 'source_stable_slots')}</span>
                  </div>
                )}
                {!identityReview && (
                  <p className='muted'>
                    Brak dokumentu player identity. Uruchom ponownie analize albo odswiez panel.
                  </p>
                )}
              </div>

              <div className='chips'>
                <span>Team conf: {((selected.team_confidence || 0) * 100).toFixed(0)}%</span>
                <span>Det conf: {selected.mean_detection_confidence ?? 'n/a'}</span>
                <span>Kolor: {selected.jersey_color_hex || 'n/a'}</span>
                <span>Team switch blocks: {selected.blocked_team_switches ?? 0}</span>
                <span>Identity blocks: {selected.blocked_identity_switches ?? 0}</span>
                <span>Ambiguous: {selected.ambiguous_frames ?? 0}</span>
                <span>Heatmap: {selected.heatmap_quality || 'n/a'}</span>
              </div>

              {selected.movement_stats && (
                <div className='chips'>
                  <span>Dystans: {selected.movement_stats.total_distance_m.toFixed(1)} m</span>
                  <span>Observed: {selected.movement_stats.observed_distance_m.toFixed(1)} m</span>
                  <span>Estimated gaps: {selected.movement_stats.estimated_gap_distance_m.toFixed(1)} m</span>
                  <span>Avg: {selected.movement_stats.avg_speed_kmh.toFixed(1)} km/h</span>
                  <span>
                    Peak: {(selected.movement_stats.peak_sustained_speed_kmh ?? selected.movement_stats.top_speed_kmh).toFixed(1)} km/h
                  </span>
                  <span>
                    Speed quality: {selected.movement_stats.speed_quality || 'unknown'}
                  </span>
                  <span>Sprints: {selected.movement_stats.intensity?.sprint_count ?? 0}</span>
                  <span>Sprint dist: {(selected.movement_stats.intensity?.sprint_distance_m ?? 0).toFixed(1)} m</span>
                  <span>HI dist: {(selected.movement_stats.intensity?.high_intensity_distance_m ?? 0).toFixed(1)} m</span>
                  <span>Playing: {selected.movement_stats.playing_time_sec.toFixed(1)}s</span>
                  <span>Quality: {selected.movement_stats.distance_quality}</span>
                </div>
              )}

              {(selected.stints?.length || 0) > 0 && (
                <details className='debug-details'>
                  <summary>Stinty slotu</summary>
                  <pre>{JSON.stringify(selected.stints, null, 2)}</pre>
                </details>
              )}

              {(selected.rejected_candidates?.length || selected.suspicious_assignments?.length || selected.risky_links.length) > 0 && (
                <details className='debug-details' open>
                  <summary>Niepewne / zablokowane przypisania</summary>
                  <pre>
                    {JSON.stringify(
                      selected.rejected_candidates || selected.suspicious_assignments || selected.risky_links,
                      null,
                      2,
                    )}
                  </pre>
                </details>
              )}
            </div>
          ) : (
            <p className='muted'>Brak stabilnych slotow.</p>
          )}
        </div>
      </div>

      {problemFrames.length > 0 && (
        <details className='debug-details'>
          <summary>Problem frames z global identity report</summary>
          <pre>{JSON.stringify(problemFrames.slice(0, 40), null, 2)}</pre>
        </details>
      )}

      {(lowVisibleFrames.length > 0 || ambiguousRanges.length > 0 || blockedSwitches.length > 0 || rejectedStartCandidates.length > 0) && (
        <details className='debug-details' open>
          <summary>Diagnostyka konserwatywnego resolvera</summary>
          <pre>
            {JSON.stringify(
              {
                low_visible_frames: lowVisibleFrames.slice(0, 40),
                ambiguous_frame_ranges: ambiguousRanges.slice(0, 40),
                blocked_switches: blockedSwitches.slice(0, 40),
                rejected_start_candidates: rejectedStartCandidates.slice(0, 40),
              },
              null,
              2,
            )}
          </pre>
        </details>
      )}

      {teamClusters && (
        <details className='debug-details'>
          <summary>Team color clusters</summary>
          <pre>{JSON.stringify(teamClusters.clusters, null, 2)}</pre>
        </details>
      )}
    </section>
  );
}
