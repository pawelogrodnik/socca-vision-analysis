import type { AnalysisPayload, AnalysisReport, Match, MatchMetadataPayload, MatchPackage, PublishedMatch, PublishedMatchDetail, Team } from './types';

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
  return `${API_BASE}/api/matches/${matchId}/artifact/${artifactName}`;
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

export async function savePitch(matchId: string, payload: { image_points: number[][]; width_m: number; length_m: number; source: string }) {
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
