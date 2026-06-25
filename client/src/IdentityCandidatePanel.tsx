import { useEffect, useMemo, useState } from 'react';
import { getIdentityReview, saveIdentityAssignments } from './identityApi';
import { emptyIdentityAssignment, identityStatuses, playerLabel } from './identityCandidates';
import type { IdentityAssignment, IdentityCandidate, IdentityCandidateStatus, IdentityReviewState } from './identityCandidates';
import type { Match, Team } from './types';

type Props = {
  match: Match;
  onStatus: (message: string) => void;
  onSaved?: () => Promise<void> | void;
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function teamKey(team: Team): string {
  return team.id || team.name;
}

function CandidateSummary({ review, teams }: { review: IdentityReviewState; teams: Team[] }) {
  return (
    <div className="chips">
      <span>Raw tracklety: {review.raw_tracklets_count}</span>
      <span>Noise ukryte: {review.noise_tracklets_count}</span>
      <span>Kandydaci: {review.summary.identity_candidates}</span>
      <span>Przypisani: {review.summary.assigned_candidates}</span>
      <span>Do decyzji: {review.summary.needs_review_candidates}</span>
      <span>Ignored: {review.summary.ignored_candidates}</span>
      <span>Unikalni gracze: {review.summary.unique_players_total}</span>
      {teams.map((team) => {
        const key = teamKey(team);
        return (
          <span key={key}>
            {team.name}: {review.summary.unique_players_by_team[key] || 0}/{review.summary.roster_players_by_team[key] || team.players?.length || 0}
          </span>
        );
      })}
    </div>
  );
}

export function IdentityCandidatePanel({ match, onStatus, onSaved }: Props) {
  const [review, setReview] = useState<IdentityReviewState | null>(null);
  const [assignments, setAssignments] = useState<IdentityAssignment[]>([]);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string>('');
  const [showAll, setShowAll] = useState(false);

  const teams = match.teams || [];

  async function load() {
    try {
      const data = await getIdentityReview(match.id);
      setReview(data);
      setAssignments(data.candidates.map(emptyIdentityAssignment));
      setSelectedCandidateId(data.candidates[0]?.candidate_id || '');
      onStatus(`Załadowano ${data.summary.identity_candidates} kandydatów identity; ukryto ${data.noise_tracklets_count} krótkich/noisy trackletów.`);
    } catch (error) {
      onStatus(`Nie mogę załadować identity candidates: ${errorMessage(error)}`);
    }
  }

  useEffect(() => {
    if (match.analysis_report?.status === 'completed') {
      load().catch((error) => onStatus(errorMessage(error)));
    } else {
      setReview(null);
      setAssignments([]);
      setSelectedCandidateId('');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id, match.analysis_report?.status]);

  function assignmentFor(candidate: IdentityCandidate): IdentityAssignment {
    return assignments.find((item) => item.candidate_id === candidate.candidate_id) || emptyIdentityAssignment(candidate);
  }

  function updateAssignment(candidate: IdentityCandidate, patch: Partial<IdentityAssignment>) {
    const current = assignmentFor(candidate);
    const next: IdentityAssignment = { ...current, ...patch };
    if (next.status !== 'assigned') next.player_id = null;
    setAssignments((items) => {
      const exists = items.some((item) => item.candidate_id === candidate.candidate_id);
      return exists ? items.map((item) => (item.candidate_id === candidate.candidate_id ? next : item)) : [...items, next];
    });
  }

  async function save() {
    if (!review) return;
    const saved = await saveIdentityAssignments(match.id, assignments);
    onStatus(`Zapisano identity assignments: ${saved.summary.assigned_candidates} kandydatów, ${saved.summary.unique_players_total} unikalnych zawodników.`);
    const refreshed = await getIdentityReview(match.id);
    setReview(refreshed);
    setAssignments(refreshed.candidates.map(emptyIdentityAssignment));
    await onSaved?.();
  }

  const selectedCandidate = review?.candidates.find((candidate) => candidate.candidate_id === selectedCandidateId) || review?.candidates[0];
  const selectedAssignment = selectedCandidate ? assignmentFor(selectedCandidate) : null;
  const selectedTeam = teams.find((team) => teamKey(team) === selectedAssignment?.team_id);

  const visibleCandidates = useMemo(() => {
    if (!review) return [];
    if (showAll) return review.candidates;
    return review.candidates.filter((candidate) => candidate.status !== 'false_positive' && candidate.status !== 'opponent' && candidate.status !== 'referee');
  }, [review, showAll]);

  if (match.analysis_report?.status !== 'completed') {
    return (
      <section className="card">
        <h2>Identity candidates</h2>
        <p className="muted">Uruchom analizę, żeby zbudować kandydatów zawodników z raw tracker IDs.</p>
      </section>
    );
  }

  if (!review) {
    return (
      <section className="card">
        <h2>Identity candidates</h2>
        <button type="button" onClick={load}>Załaduj identity candidates</button>
      </section>
    );
  }

  return (
    <section className="card">
      <div className="row between">
        <div>
          <h2>Identity candidates</h2>
          <p className="muted">Przypisuj grupy trackletów do zawodników. To zastępuje ręczne klikanie tysięcy raw tracker IDs.</p>
        </div>
        <div className="row">
          <button type="button" onClick={() => setShowAll((value) => !value)}>{showAll ? 'Ukryj ignored' : 'Pokaż wszystkie'}</button>
          <button type="button" onClick={load}>Odśwież</button>
          <button type="button" onClick={save}>Zapisz</button>
        </div>
      </div>

      <CandidateSummary review={review} teams={teams} />

      <div className="grid two resolver-grid">
        <div className="tracklet-list">
          {visibleCandidates.map((candidate) => {
            const assignment = assignmentFor(candidate);
            const assignedLabel = playerLabel(teams, assignment.team_id, assignment.player_id);
            return (
              <button
                type="button"
                className={candidate.candidate_id === selectedCandidate?.candidate_id ? 'match-item active' : 'match-item'}
                key={candidate.candidate_id}
                onClick={() => setSelectedCandidateId(candidate.candidate_id)}
              >
                <strong>{candidate.candidate_id} · {assignment.status}</strong>
                <span>{Number(candidate.total_duration_sec || 0).toFixed(1)}s · {candidate.tracklet_count} trackletów · {assignedLabel || 'bez zawodnika'}</span>
              </button>
            );
          })}
        </div>

        <div className="team-card">
          {selectedCandidate && selectedAssignment ? (
            <div className="stack">
              <h3>{selectedCandidate.candidate_id}</h3>
              <div className="chips">
                <span>Tracklety: {selectedCandidate.tracklet_ids.join(', ')}</span>
                <span>Czas: {selectedCandidate.start_time_sec ?? '?'}s → {selectedCandidate.end_time_sec ?? '?'}s</span>
                <span>Łącznie: {Number(selectedCandidate.total_duration_sec || 0).toFixed(1)}s</span>
                <span>Merge confidence: {selectedCandidate.merge_confidence ?? 'n/a'}</span>
              </div>

              <label>
                Status
                <select value={selectedAssignment.status} onChange={(event) => updateAssignment(selectedCandidate, { status: event.target.value as IdentityCandidateStatus })}>
                  {identityStatuses.map((status) => <option key={status.value} value={status.value}>{status.label}</option>)}
                </select>
              </label>

              <label>
                Drużyna
                <select
                  value={selectedAssignment.team_id || ''}
                  onChange={(event) => updateAssignment(selectedCandidate, { team_id: event.target.value || null, player_id: null, status: 'assigned' })}
                >
                  <option value="">-- wybierz drużynę --</option>
                  {teams.map((team) => <option key={teamKey(team)} value={teamKey(team)}>{team.name}</option>)}
                </select>
              </label>

              <label>
                Zawodnik
                <select
                  value={selectedAssignment.player_id || ''}
                  disabled={!selectedTeam}
                  onChange={(event) => updateAssignment(selectedCandidate, { player_id: event.target.value || null, status: event.target.value ? 'assigned' : selectedAssignment.status })}
                >
                  <option value="">-- wybierz zawodnika --</option>
                  {(selectedTeam?.players || []).map((player) => (
                    <option key={player.id || player.name} value={player.id || player.name}>
                      {player.number ? `#${player.number} ` : ''}{player.name} · {player.role}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Notatka
                <textarea rows={3} value={selectedAssignment.notes || ''} onChange={(event) => updateAssignment(selectedCandidate, { notes: event.target.value })} />
              </label>

              <div className="row">
                <button type="button" onClick={() => updateAssignment(selectedCandidate, { status: 'false_positive', team_id: null, player_id: null })}>False positive</button>
                <button type="button" className="secondary" onClick={() => updateAssignment(selectedCandidate, { status: 'unknown', team_id: null, player_id: null })}>Unknown</button>
                <button type="button" className="secondary" onClick={() => updateAssignment(selectedCandidate, { status: 'referee', team_id: null, player_id: null })}>Referee</button>
              </div>
            </div>
          ) : (
            <p className="muted">Brak kandydatów.</p>
          )}
        </div>
      </div>
    </section>
  );
}
