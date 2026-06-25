import type { IdentityAssignment, IdentityAssignmentsDocument, IdentityReviewState } from './identityCandidates';

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

export async function getIdentityReview(matchId: string): Promise<IdentityReviewState> {
  return request<IdentityReviewState>(`/api/matches/${matchId}/identity-candidates`);
}

export async function saveIdentityAssignments(matchId: string, assignments: IdentityAssignment[]): Promise<IdentityAssignmentsDocument> {
  return request<IdentityAssignmentsDocument>(`/api/matches/${matchId}/identity-assignments`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ assignments })
  });
}
