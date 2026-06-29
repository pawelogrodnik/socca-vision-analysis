import type {
  AnalysisPayload,
  AnalysisReport,
  BallAnalysisPayload,
  ContactCandidateReviewUpdate,
  ContactCandidatesDocument,
  Match,
  MatchMetadataPayload,
  MatchPackage,
  PlayerIdentityAssignment,
  PlayerIdentityReviewState,
  PlayerProfileStatsDocument,
  ResolvedPlayerStatsDocument,
  TeamProfileStatsDocument,
  PlayerAssignment,
  PlayerAssignmentsDocument,
  PublishedMatch,
  PublishedMatchDetail,
  StablePlayerReviewPayload,
  StablePlayersReviewState,
  Team,
  TeamConfigReviewPayload,
  TeamConfigReviewState,
  TrackletReviewState
} from './types';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, options);
  } catch (error) {
    throw new Error(`Network error: ${error instanceof Error ? error.message : String(error)}`);
  }

  if (!res.ok) {
    const contentType = res.headers.get('content-type') || '';
    const body = contentType.includes('application/json') ? await res.json().catch(() => null) : await res.text();
    const detail = typeof body === 'object' && body !== null && 'detail' in body ? String((body as { detail: unknown }).detail) : String(body);
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export function artifactUrl(matchId: string, artifactName: string): string {
  const encodedArtifact = artifactName
    .split(/[\\/]+/)
    .filter(Boolean)
    .map(encodeURIComponent)
    .join('/');
  return `${API_BASE}/api/matches/${encodeURIComponent(matchId)}/artifact/${encodedArtifact}`;
}

export function frameUrl(matchId: string, second: number): string {
  return `${API_BASE}/api/matches/${matchId}/frame?second=${second}&_=${Date.now()}`;
}

export async function createMatch(input: {
  title: string;
  video: File;
  match_date?: string;
  season?: string;
  venue?: string;
  format: string;
  teams: Team[];
}): Promise<Match> {
  const body = new FormData();
  body.append('title', input.title);
  body.append('video', input.video);
  body.append('format', input.format);
  if (input.match_date) body.append('match_date', input.match_date);
  if (input.season) body.append('season', input.season);
  if (input.venue) body.append('venue', input.venue);
  body.append('teams_json', JSON.stringify(input.teams));
  return request<Match>('/api/matches', { method: 'POST', body });
}

export async function listTeams(): Promise<Team[]> {
  return request<Team[]>('/api/teams');
}

export async function getTeam(teamId: string): Promise<Team> {
  return request<Team>(`/api/teams/${encodeURIComponent(teamId)}`);
}

export async function createTeam(payload: Team): Promise<Team> {
  return request<Team>('/api/teams', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
}

export async function updateTeam(teamId: string, payload: Team): Promise<Team> {
  return request<Team>(`/api/teams/${encodeURIComponent(teamId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
}

export async function deleteTeam(teamId: string): Promise<{ status: string; team_id: string }> {
  return request<{ status: string; team_id: string }>(`/api/teams/${encodeURIComponent(teamId)}`, {
    method: 'DELETE'
  });
}

export async function listMatches(): Promise<Match[]> {
  return request<Match[]>('/api/matches');
}

export async function getMatch(matchId: string): Promise<Match> {
  return request<Match>(`/api/matches/${matchId}`);
}

export async function updateMatchMetadata(matchId: string, payload: MatchMetadataPayload): Promise<Match> {
  return request<Match>(`/api/matches/${matchId}/metadata`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
}

export async function savePitch(matchId: string, payload: {
  image_points: number[][];
  width_m: number;
  length_m: number;
  pitch_dimensions_m?: { width_m: number; length_m: number };
  calibration_frame_time_sec?: number;
  source: string;
}) {
  return request(`/api/matches/${matchId}/pitch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
}

export async function analyzeMatch(matchId: string, payload: AnalysisPayload): Promise<AnalysisReport> {
  return request<AnalysisReport>(`/api/matches/${matchId}/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
}

export async function analyzeBall(matchId: string, payload: BallAnalysisPayload): Promise<AnalysisReport> {
  return request<AnalysisReport>(`/api/matches/${matchId}/analyze-ball`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
}

export async function getTrackletReview(matchId: string): Promise<TrackletReviewState> {
  return request<TrackletReviewState>(`/api/matches/${matchId}/tracklets`);
}

export async function savePlayerAssignments(matchId: string, assignments: PlayerAssignment[]): Promise<PlayerAssignmentsDocument> {
  return request<PlayerAssignmentsDocument>(`/api/matches/${matchId}/player-assignments`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ assignments })
  });
}

export async function getStablePlayers(matchId: string): Promise<StablePlayersReviewState> {
  return request<StablePlayersReviewState>(`/api/matches/${matchId}/stable-players`);
}

export async function reviewStablePlayers(matchId: string, payload: StablePlayerReviewPayload): Promise<StablePlayersReviewState> {
  return request<StablePlayersReviewState>(`/api/matches/${matchId}/stable-players/review`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
}

export async function getPlayerIdentityReview(matchId: string): Promise<PlayerIdentityReviewState> {
  return request<PlayerIdentityReviewState>(`/api/matches/${matchId}/player-identity`);
}

export async function savePlayerIdentityAssignments(
  matchId: string,
  assignments: PlayerIdentityAssignment[],
): Promise<PlayerIdentityReviewState> {
  return request<PlayerIdentityReviewState>(`/api/matches/${matchId}/player-identity`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ assignments })
  });
}

export async function getResolvedPlayerStats(matchId: string): Promise<ResolvedPlayerStatsDocument> {
  return request<ResolvedPlayerStatsDocument>(`/api/matches/${matchId}/resolved-player-stats`);
}

export async function getPlayerProfileStats(playerId: string): Promise<PlayerProfileStatsDocument> {
  return request<PlayerProfileStatsDocument>(`/api/players/${encodeURIComponent(playerId)}/stats`);
}

export async function getTeamProfileStats(teamId: string, season?: string): Promise<TeamProfileStatsDocument> {
  const params = season ? `?season=${encodeURIComponent(season)}` : '';
  return request<TeamProfileStatsDocument>(`/api/teams/${encodeURIComponent(teamId)}/stats${params}`);
}

export async function getTeamConfig(matchId: string): Promise<TeamConfigReviewState> {
  return request<TeamConfigReviewState>(`/api/matches/${matchId}/team-config`);
}

export async function reviewTeamConfig(matchId: string, payload: TeamConfigReviewPayload): Promise<TeamConfigReviewState> {
  return request<TeamConfigReviewState>(`/api/matches/${matchId}/team-config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
}

export async function getContactCandidates(matchId: string): Promise<ContactCandidatesDocument> {
  return request<ContactCandidatesDocument>(`/api/matches/${matchId}/contact-candidates`);
}

export async function reviewContactCandidates(
  matchId: string,
  updates: ContactCandidateReviewUpdate[],
): Promise<ContactCandidatesDocument> {
  return request<ContactCandidatesDocument>(`/api/matches/${matchId}/contact-candidates/review`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ updates })
  });
}

export async function createMatchPackage(matchId: string): Promise<MatchPackage> {
  return request<MatchPackage>(`/api/matches/${matchId}/package`, { method: 'POST' });
}

export async function publishLocalMatch(matchId: string, replace = false): Promise<PublishedMatchDetail> {
  return request<PublishedMatchDetail>(`/api/matches/${matchId}/publish-local?replace=${String(replace)}`, { method: 'POST' });
}

export async function listPublishedMatches(): Promise<PublishedMatch[]> {
  return request<PublishedMatch[]>('/api/published/matches');
}

export async function getPublishedMatch(matchId: string): Promise<PublishedMatchDetail> {
  return request<PublishedMatchDetail>(`/api/published/matches/${matchId}`);
}

export async function deletePublishedMatch(matchId: string): Promise<{ status: string; match: PublishedMatch }> {
  return request<{ status: string; match: PublishedMatch }>(`/api/published/matches/${matchId}`, { method: 'DELETE' });
}
