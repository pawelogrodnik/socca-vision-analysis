import type { Team } from './types';

export type IdentityCandidateStatus = 'needs_review' | 'assigned' | 'unknown' | 'false_positive' | 'opponent' | 'referee';

export type IdentityCandidate = {
  candidate_id: string;
  tracklet_ids: number[];
  status: IdentityCandidateStatus;
  team_id?: string | null;
  player_id?: string | null;
  notes?: string;
  merge_confidence?: number | null;
  start_time_sec?: number | null;
  end_time_sec?: number | null;
  total_duration_sec?: number | null;
  positions_count?: number | null;
  avg_confidence?: number | null;
  first_pitch_m?: number[] | null;
  last_pitch_m?: number[] | null;
  sample_tracklet_id?: number | null;
  tracklet_count: number;
};

export type IdentityAssignment = {
  candidate_id: string;
  status: IdentityCandidateStatus;
  team_id?: string | null;
  player_id?: string | null;
  notes?: string;
};

export type IdentitySummary = {
  identity_candidates: number;
  assigned_candidates: number;
  needs_review_candidates: number;
  ignored_candidates: number;
  assigned_tracklets: number;
  unique_players_total: number;
  unique_players_by_team: Record<string, number>;
  assigned_candidates_by_team: Record<string, number>;
  assigned_tracklets_by_team: Record<string, number>;
  roster_players_by_team: Record<string, number>;
};

export type IdentityReviewState = {
  schema_version: string;
  generated_at: string;
  parameters: Record<string, number>;
  raw_tracklets_count: number;
  usable_tracklets_count: number;
  noise_tracklets_count: number;
  noise_tracklet_ids: number[];
  candidates: IdentityCandidate[];
  summary: IdentitySummary;
};

export type IdentityAssignmentsDocument = {
  schema_version: string;
  updated_at: string;
  assignments: IdentityAssignment[];
  summary: IdentitySummary;
};

export const identityStatuses: Array<{ value: IdentityCandidateStatus; label: string }> = [
  { value: 'needs_review', label: 'Do decyzji' },
  { value: 'assigned', label: 'Przypisany zawodnik' },
  { value: 'unknown', label: 'Nie wiem / później' },
  { value: 'false_positive', label: 'Fałszywa detekcja' },
  { value: 'opponent', label: 'Poza rosterem / inny mecz' },
  { value: 'referee', label: 'Sędzia / osoba techniczna' }
];

export function emptyIdentityAssignment(candidate: IdentityCandidate): IdentityAssignment {
  return {
    candidate_id: candidate.candidate_id,
    status: candidate.status || 'needs_review',
    team_id: candidate.team_id || null,
    player_id: candidate.player_id || null,
    notes: candidate.notes || ''
  };
}

export function playerLabel(teams: Team[], teamId?: string | null, playerId?: string | null): string {
  if (!teamId || !playerId) return '';
  const team = teams.find((item) => (item.id || item.name) === teamId);
  const player = team?.players.find((item) => (item.id || item.name) === playerId);
  if (!player) return playerId;
  return `${player.number ? `#${player.number} ` : ''}${player.name}`;
}
