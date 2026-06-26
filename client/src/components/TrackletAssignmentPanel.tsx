import { useEffect, useState } from 'react';
import type {
  Match,
  PlayerAssignment,
  TrackletReviewState,
  TrackletAssignmentStatus,
} from '../types';
import { getTrackletReview, savePlayerAssignments } from '../api';
import { errorMessage, pretty } from '../lib/helpers';

const assignmentStatuses: Array<{
  value: TrackletAssignmentStatus;
  label: string;
}> = [
  { value: 'unassigned', label: 'Do decyzji' },
  { value: 'assigned', label: 'Przypisany zawodnik' },
  { value: 'unknown', label: 'Nie wiem / później' },
  { value: 'false_positive', label: 'Fałszywa detekcja' },
  { value: 'opponent', label: 'Poza rosterem / inny mecz' },
  { value: 'referee', label: 'Sędzia / osoba techniczna' },
];

interface TrackletAssignmentPanelProps {
  match: Match;
  onStatus: (message: string) => void;
  onSaved: () => void;
}

export function TrackletAssignmentPanel({
  match,
  onStatus,
  onSaved,
}: TrackletAssignmentPanelProps) {
  const [review, setReview] = useState<TrackletReviewState | null>(null);
  const [assignments, setAssignments] = useState<PlayerAssignment[]>([]);
  const [selectedTrackletId, setSelectedTrackletId] = useState<number | null>(
    null,
  );

  async function load() {
    try {
      const data = await getTrackletReview(match.id);
      setReview(data);
      setAssignments(data.assignments);
      setSelectedTrackletId(data.tracklets[0]?.tracklet_id ?? null);
      onStatus('Załadowano tracklety do akceptacji.');
    } catch (error) {
      onStatus(`Nie mogę załadować trackletów: ${errorMessage(error)}`);
    }
  }

  useEffect(() => {
    if (match.analysis_report?.status === 'completed') {
      load().catch((error) => onStatus(errorMessage(error)));
    } else {
      setReview(null);
      setAssignments([]);
      setSelectedTrackletId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id, match.analysis_report?.status]);

  function assignmentFor(trackletId: number): PlayerAssignment {
    return (
      assignments.find(
        (assignment) => assignment.tracklet_id === trackletId,
      ) || {
        tracklet_id: trackletId,
        status: 'unassigned',
        team_id: null,
        player_id: null,
        notes: '',
      }
    );
  }

  function updateAssignment(
    trackletId: number,
    patch: Partial<PlayerAssignment>,
  ) {
    const current = assignmentFor(trackletId);
    const next = { ...current, ...patch };
    if (next.status !== 'assigned') {
      next.team_id = patch.team_id ?? next.team_id;
      next.player_id = null;
    }
    setAssignments((items) => {
      const exists = items.some((item) => item.tracklet_id === trackletId);
      return exists
        ? items.map((item) => (item.tracklet_id === trackletId ? next : item))
        : [...items, next];
    });
  }

  async function save() {
    if (!review) return;
    const saved = await savePlayerAssignments(match.id, assignments);
    onStatus(
      `Zapisano przypisania: ${saved.summary.assigned_tracklets} trackletów przypisanych, ${saved.summary.unique_players_total} unikalnych zawodników.`,
    );
    const fresh = await getTrackletReview(match.id);
    setReview(fresh);
    setAssignments(fresh.assignments);
    await onSaved();
  }

  if (match.analysis_report?.status !== 'completed') {
    return (
      <section className='card'>
        <h2>5. Akceptacja trackletów i player_id</h2>
        <p className='muted'>
          Uruchom analizę, żeby dostać listę surowych trackletów do przypisania
          zawodnikom.
        </p>
      </section>
    );
  }

  if (!review) {
    return (
      <section className='card'>
        <h2>5. Akceptacja trackletów i player_id</h2>
        <button type='button' onClick={load}>
          Załaduj tracklety
        </button>
      </section>
    );
  }

  const selectedTracklet =
    review.tracklets.find(
      (tracklet) => tracklet.tracklet_id === selectedTrackletId,
    ) || review.tracklets[0];
  const selectedAssignment = selectedTracklet
    ? assignmentFor(selectedTracklet.tracklet_id)
    : null;
  const selectedTeam = match?.teams?.find(
    (team) => team.id === selectedAssignment?.team_id,
  );

  return (
    <section className='card'>
      <div className='row between'>
        <div>
          <h2>5. Akceptacja trackletów i player_id</h2>
          <p className='muted'>
            YOLO/BoT-SORT daje surowe tracklety. Tutaj akceptujesz, czy to
            prawdziwy zawodnik i łączysz tracklet z graczem z rosteru.
          </p>
        </div>
        <div className='row'>
          <button type='button' onClick={load}>
            Odśwież tracklety
          </button>
          <button type='button' onClick={save}>
            Zapisz przypisania
          </button>
        </div>
      </div>

      <div className='chips'>
        <span>Raw tracklety: {review.summary.raw_tracklets}</span>
        <span>Przypisane tracklety: {review.summary.assigned_tracklets}</span>
        <span>Nieprzypisane: {review.summary.unassigned_tracklets}</span>
        <span>Ignored: {review.summary.ignored_tracklets}</span>
        <span>Unikalni zawodnicy: {review.summary.unique_players_total}</span>
      </div>

      <div className='grid two resolver-grid'>
        <div className='tracklet-list'>
          {review.tracklets.map((tracklet) => {
            const assignment = assignmentFor(tracklet.tracklet_id);
            return (
              <button
                type='button'
                className={
                  tracklet.tracklet_id === selectedTracklet?.tracklet_id
                    ? 'match-item active'
                    : 'match-item'
                }
                key={tracklet.tracklet_id}
                onClick={() => setSelectedTrackletId(tracklet.tracklet_id)}
              >
                <strong>
                  T{tracklet.tracklet_id} · {assignment.status}
                </strong>
                <span>
                  {Number(tracklet.duration_sec || 0).toFixed(1)}s ·{' '}
                  {tracklet.positions_count || 0} punktów · conf{' '}
                  {tracklet.avg_confidence ?? 'n/a'}
                </span>
              </button>
            );
          })}
        </div>

        <div className='team-card'>
          {selectedTracklet && selectedAssignment ? (
            <div className='stack'>
              <h3>Tracklet T{selectedTracklet.tracklet_id}</h3>
              <div className='chips'>
                <span>
                  Czas: {selectedTracklet.start_time_sec ?? '?'}s →{' '}
                  {selectedTracklet.end_time_sec ?? '?'}s
                </span>
                <span>
                  Długość:{' '}
                  {Number(selectedTracklet.duration_sec || 0).toFixed(1)}s
                </span>
                <span>Pozycje: {selectedTracklet.positions_count || 0}</span>
                <span>Conf: {selectedTracklet.avg_confidence ?? 'n/a'}</span>
              </div>
              <label>
                Status
                <select
                  value={selectedAssignment.status}
                  onChange={(event) =>
                    updateAssignment(selectedTracklet.tracklet_id, {
                      status: event.target.value as TrackletAssignmentStatus,
                    })
                  }
                >
                  {assignmentStatuses.map((status) => (
                    <option key={status.value} value={status.value}>
                      {status.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Drużyna
                <select
                  value={selectedAssignment.team_id || ''}
                  onChange={(event) =>
                    updateAssignment(selectedTracklet.tracklet_id, {
                      team_id: event.target.value || null,
                      player_id: null,
                      status: 'assigned',
                    })
                  }
                >
                  <option value=''>-- wybierz drużynę --</option>
                  {(match.teams || []).map((team) => (
                    <option
                      key={team.id || team.name}
                      value={team.id || team.name}
                    >
                      {team.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Zawodnik
                <select
                  value={selectedAssignment.player_id || ''}
                  onChange={(event) =>
                    updateAssignment(selectedTracklet.tracklet_id, {
                      player_id: event.target.value || null,
                      status: event.target.value
                        ? 'assigned'
                        : selectedAssignment.status,
                    })
                  }
                  disabled={!selectedTeam}
                >
                  <option value=''>-- wybierz zawodnika --</option>
                  {(selectedTeam?.players || []).map((player) => (
                    <option
                      key={player.id || player.name}
                      value={player.id || player.name}
                    >
                      {player.number ? `#${player.number} ` : ''}
                      {player.name} · {player.role}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Notatka
                <textarea
                  rows={3}
                  value={selectedAssignment.notes || ''}
                  onChange={(event) =>
                    updateAssignment(selectedTracklet.tracklet_id, {
                      notes: event.target.value,
                    })
                  }
                />
              </label>
              <div className='row'>
                <button
                  type='button'
                  onClick={() =>
                    updateAssignment(selectedTracklet.tracklet_id, {
                      status: 'false_positive',
                      team_id: null,
                      player_id: null,
                    })
                  }
                >
                  Oznacz false positive
                </button>
                <button
                  type='button'
                  className='secondary'
                  onClick={() =>
                    updateAssignment(selectedTracklet.tracklet_id, {
                      status: 'unknown',
                      team_id: null,
                      player_id: null,
                    })
                  }
                >
                  Zostaw unknown
                </button>
              </div>
              <pre>{pretty(selectedTracklet)}</pre>
            </div>
          ) : (
            <p className='muted'>Brak trackletów.</p>
          )}
        </div>
      </div>
    </section>
  );
}
