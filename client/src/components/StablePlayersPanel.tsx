import { useEffect, useMemo, useState } from 'react';
import { getStablePlayers, reviewStablePlayers } from '../api';
import type {
  Match,
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

function confidenceLabel(player: StablePlayer): string {
  const score = player.confidence_score;
  return score == null ? player.confidence : `${player.confidence} ${(score * 100).toFixed(0)}%`;
}

function movementLabel(player: StablePlayer): string {
  const stats = player.movement_stats;
  if (!stats) return 'stats n/a';
  return `dist ${stats.total_distance_m.toFixed(1)}m - avg ${stats.avg_speed_kmh.toFixed(1)} km/h - top ${stats.top_speed_kmh.toFixed(1)} km/h`;
}

function numberFrom(record: Record<string, unknown> | undefined, key: string): number | null {
  const value = record?.[key];
  return typeof value === 'number' ? value : null;
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

export function StablePlayersPanel({
  match,
  onStatus,
  onSaved,
}: StablePlayersPanelProps) {
  const [review, setReview] = useState<StablePlayersReviewState | null>(null);
  const [selectedId, setSelectedId] = useState('');
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const data = await getStablePlayers(match.id);
      setReview(data);
      setSelectedId(data.stable_players.players[0]?.stable_subject_id || '');
      onStatus(`Zaladowano ${data.stable_players.summary.stable_players} stabilnych slotow.`);
    } catch (error) {
      setReview(null);
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
  const teams = match.teams || [];

  return (
    <section className='card'>
      <div className='row between'>
        <div>
          <h2>5. Stabilni zawodnicy</h2>
          <p className='muted'>
            Widok pokazuje anonimowe sloty/stinty z global identity resolvera.
            Raw tracker_id zostaje tylko jako debug, a realny player_id bedzie
            przypisywany pozniej przez roster/review.
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

      {movementSummary && (
        <div className='chips'>
          <span>Total dist: {numberFrom(movementSummary, 'total_distance_m') ?? 'n/a'} m</span>
          <span>Observed dist: {numberFrom(movementSummary, 'observed_distance_m') ?? 'n/a'} m</span>
          <span>Estimated gaps: {numberFrom(movementSummary, 'estimated_gap_distance_m') ?? 'n/a'} m</span>
          <span>Players estimated: {numberFrom(movementSummary, 'players_with_estimated_distance') ?? 'n/a'}</span>
          <span>Top speed: {numberFrom(movementSummary, 'top_speed_kmh') ?? 'n/a'} km/h</span>
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

              <PlayerTrajectory
                player={selected}
                widthM={pitch.width_m}
                lengthM={pitch.length_m}
              />

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

              <div className='chips'>
                <span>Team conf: {((selected.team_confidence || 0) * 100).toFixed(0)}%</span>
                <span>Det conf: {selected.mean_detection_confidence ?? 'n/a'}</span>
                <span>Kolor: {selected.jersey_color_hex || 'n/a'}</span>
                <span>Team switch blocks: {selected.blocked_team_switches ?? 0}</span>
                <span>Identity blocks: {selected.blocked_identity_switches ?? 0}</span>
                <span>Ambiguous: {selected.ambiguous_frames ?? 0}</span>
              </div>

              {selected.movement_stats && (
                <div className='chips'>
                  <span>Dystans: {selected.movement_stats.total_distance_m.toFixed(1)} m</span>
                  <span>Observed: {selected.movement_stats.observed_distance_m.toFixed(1)} m</span>
                  <span>Estimated gaps: {selected.movement_stats.estimated_gap_distance_m.toFixed(1)} m</span>
                  <span>Avg: {selected.movement_stats.avg_speed_kmh.toFixed(1)} km/h</span>
                  <span>Top: {selected.movement_stats.top_speed_kmh.toFixed(1)} km/h</span>
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
