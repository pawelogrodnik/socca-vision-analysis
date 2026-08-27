import type {
  AnalysisPayload,
  AnalysisJob,
  AnalysisJobsDocument,
  AnalysisReport,
  BallAnalysisPayload,
  ChangeCandidateReviewUpdate,
  ChangeCandidatesDocument,
  ContactCandidateReviewUpdate,
  ContactCandidatesDocument,
  IdentityReviewGalleryDocument,
  IdentityCropReviewDocument,
  IdentityCropReviewUpdate,
  InitialIdentityAuditDocument,
  InitialIdentityAuditSeedStoreDocument,
  InitialIdentityAuditSeedUpdate,
  InitialIdentityAuditTelemetryEvent,
  SecondHalfIdentityReanchorDocument,
  IdentityRosterSubjectReviewDocument,
  IdentityRosterSubjectTelemetryEvent,
  IdentityRosterSubjectReviewUpdate,
  Match,
  MatchPhaseConfigDocument,
  MatchPhaseConfigPayload,
  MatchMetadataPayload,
  MatchPackage,
  PassCandidateReviewUpdate,
  PassCandidatesDocument,
  PlayerIdentityAssignment,
  PlayerIdentityReviewState,
  PlayerProfileStatsDocument,
  PublicMatchReport,
  ResolvedPlayerStatsDocument,
  RuntimeInfo,
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
  TrackletReviewState,
  ReviewedIdentityDocument,
  ReviewedFinalizedIdentitySummary,
  ReviewedIdentityReviewProgress,
  ReviewedIdentityAt,
  ReviewedOutputJob,
  ReviewedStatsResponse,
  ReviewedCorrectionContext,
  ReviewedCorrectionFinalizeResponse,
  ReviewedCorrectionRequest,
  ReviewedCorrectionResponse,
  ReviewedTemporalSplitRequest,
  ReviewedTemporalSplitRefinement,
  ReviewedTemporalSplitResponse,
  ReviewWorkflow,
} from './types';
import type {
  BoundedH2Session,
} from './components/boundedH2ReIdTypes';
import { ApiRequestError } from './lib/apiErrors';

const API_BASE = import.meta.env?.DEV ? '' : (import.meta.env?.VITE_API_BASE_URL || '');

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, options);
  } catch (error) {
    throw new Error(`Network error: ${error instanceof Error ? error.message : String(error)}`);
  }

  if (!res.ok) {
    const contentType = res.headers.get('content-type') || '';
    const body = contentType.includes('application/json') ? await res.json().catch(() => null) : await res.text();
    const rawDetail = typeof body === 'object' && body !== null && 'detail' in body
      ? (body as { detail: unknown }).detail
      : body;
    const detail = typeof rawDetail === 'string'
      ? rawDetail
      : JSON.stringify(rawDetail);
    const code = typeof rawDetail === 'object'
      && rawDetail !== null
      && 'code' in rawDetail
      && typeof (rawDetail as { code?: unknown }).code === 'string'
      ? (rawDetail as { code: string }).code
      : null;
    throw new ApiRequestError(res.status, detail, code);
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

export function boundedH2ArtifactUrl(
  sessionId: string,
  artifact: string,
): string {
  const encoded = artifact.split('/').map(encodeURIComponent).join('/');
  return `${API_BASE}/api/bounded-h2-reid-sessions/${encodeURIComponent(sessionId)}/artifact/${encoded}`;
}

export async function getBoundedH2ReIdSession(
  sessionId: string,
): Promise<BoundedH2Session> {
  return request<BoundedH2Session>(
    `/api/bounded-h2-reid-sessions/${encodeURIComponent(sessionId)}`,
  );
}

export async function saveBoundedH2ReIdDecision(
  sessionId: string,
  payload: {
    updates: Array<Record<string, unknown>>;
    finished?: boolean;
  },
): Promise<BoundedH2Session> {
  return request<BoundedH2Session>(
    `/api/bounded-h2-reid-sessions/${encodeURIComponent(sessionId)}/decisions`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
}

export async function createMatch(input: {
  title: string;
  video: File;
  match_date?: string;
  season?: string;
  venue?: string;
  format: string;
  teams: Team[];
  identity_review_scope?: import('./types').IdentityReviewScope;
}): Promise<Match> {
  const body = new FormData();
  body.append('title', input.title);
  body.append('video', input.video);
  body.append('format', input.format);
  if (input.match_date) body.append('match_date', input.match_date);
  if (input.season) body.append('season', input.season);
  if (input.venue) body.append('venue', input.venue);
  body.append('teams_json', JSON.stringify(input.teams));
  if (input.identity_review_scope) {
    body.append('identity_review_scope_json', JSON.stringify(input.identity_review_scope));
  }
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

export async function getRuntimeInfo(): Promise<RuntimeInfo> {
  return request<RuntimeInfo>('/api/runtime');
}

export async function getMatch(matchId: string): Promise<Match> {
  return request<Match>(`/api/matches/${matchId}`);
}

export async function getReviewedIdentity(matchId: string): Promise<ReviewedIdentityDocument> {
  return request<ReviewedIdentityDocument>(`/api/matches/${encodeURIComponent(matchId)}/reviewed-identity`);
}

export async function getReviewWorkflow(matchId: string): Promise<ReviewWorkflow> {
  return request<ReviewWorkflow>(`/api/matches/${encodeURIComponent(matchId)}/review-workflow`);
}

export type ReviewedRenderOptions = {
  include_minimap: boolean;
  include_ball: boolean;
  show_roster_number: boolean;
};

export async function finalizeReviewWorkflow(
  matchId: string,
  options: ReviewedRenderOptions,
): Promise<ReviewWorkflow> {
  const response = await request<{ workflow: ReviewWorkflow }>(
    `/api/matches/${encodeURIComponent(matchId)}/review-workflow/finalize`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(options) },
  );
  return response.workflow;
}

export async function approveReviewVideoQa(matchId: string): Promise<ReviewWorkflow> {
  const response = await request<{ workflow: ReviewWorkflow }>(
    `/api/matches/${encodeURIComponent(matchId)}/review-workflow/approve-video-qa`,
    { method: 'POST' },
  );
  return response.workflow;
}

export async function retryReviewRender(matchId: string): Promise<ReviewWorkflow> {
  const response = await request<{ workflow: ReviewWorkflow }>(
    `/api/matches/${encodeURIComponent(matchId)}/review-workflow/retry-render`,
    { method: 'POST' },
  );
  return response.workflow;
}

export async function retryReviewRecompute(matchId: string): Promise<ReviewWorkflow> {
  const response = await request<{ workflow: ReviewWorkflow }>(
    `/api/matches/${encodeURIComponent(matchId)}/review-workflow/retry-recompute`,
    { method: 'POST' },
  );
  return response.workflow;
}

export async function reprojectReviewWorkflow(matchId: string): Promise<ReviewWorkflow> {
  const response = await request<{ workflow: ReviewWorkflow }>(
    `/api/matches/${encodeURIComponent(matchId)}/review-workflow/reproject`,
    { method: 'POST' },
  );
  return response.workflow;
}

export async function getReviewedIdentityReviewProgress(
  matchId: string,
  offset = 0,
  limit = 20,
  teamLabel?: import('./types').ReviewedIdentityTeamFilterLabel,
  queue: import('./types').ReviewedIdentityReviewQueue = 'required',
): Promise<ReviewedIdentityReviewProgress> {
  const query = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  });
  if (teamLabel) query.set('team_label', teamLabel);
  query.set('queue', queue);
  return request<ReviewedIdentityReviewProgress>(
    `/api/matches/${encodeURIComponent(matchId)}/reviewed-identity/review-progress?${query}`,
  );
}

export async function finalizeReviewedIdentity(
  matchId: string,
): Promise<ReviewedFinalizedIdentitySummary & { workflow: ReviewWorkflow }> {
  return request<ReviewedFinalizedIdentitySummary & { workflow: ReviewWorkflow }>(
    `/api/matches/${encodeURIComponent(matchId)}/reviewed-identity/finalize`,
    { method: 'POST' },
  );
}

export async function generateReviewedOutput(matchId: string, options: ReviewedRenderOptions): Promise<ReviewedOutputJob> {
  return request<ReviewedOutputJob>(`/api/matches/${encodeURIComponent(matchId)}/reviewed-output/generate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(options) });
}

export async function getReviewedOutputStatus(matchId: string): Promise<ReviewedOutputJob> {
  return request<ReviewedOutputJob>(`/api/matches/${encodeURIComponent(matchId)}/reviewed-output/status`);
}

export async function getReviewedIdentityAt(matchId: string, timeSec: number): Promise<ReviewedIdentityAt> {
  return request<ReviewedIdentityAt>(`/api/matches/${encodeURIComponent(matchId)}/reviewed-identity/at?time_sec=${encodeURIComponent(timeSec)}`);
}

export async function getReviewedCorrectionContext(
  matchId: string,
  candidateSubjectId: string,
  reviewTargetId?: string | null,
): Promise<ReviewedCorrectionContext> {
  const targetQuery = reviewTargetId
    ? `&review_target_id=${encodeURIComponent(reviewTargetId)}`
    : '';
  return request<ReviewedCorrectionContext>(
    `/api/matches/${encodeURIComponent(matchId)}/reviewed-identity/corrections/context?candidate_subject_id=${encodeURIComponent(candidateSubjectId)}${targetQuery}`,
  );
}

export async function saveReviewedIdentityCorrection(
  matchId: string,
  payload: ReviewedCorrectionRequest,
): Promise<ReviewedCorrectionResponse> {
  return request<ReviewedCorrectionResponse>(
    `/api/matches/${encodeURIComponent(matchId)}/reviewed-identity/corrections`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
}

export async function saveReviewedIdentityTemporalSplit(
  matchId: string,
  payload: ReviewedTemporalSplitRequest,
): Promise<ReviewedTemporalSplitResponse> {
  return request<ReviewedTemporalSplitResponse>(
    `/api/matches/${encodeURIComponent(matchId)}/reviewed-identity/temporal-split`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
}

export async function getReviewedIdentityTemporalSplitRefinement(
  matchId: string,
  payload: {
    candidate_subject_id: string;
    source_ownership_digest: string;
    after_frame: number;
    before_frame: number;
    review_target_id?: string;
    continuity_group_id?: string;
  },
): Promise<ReviewedTemporalSplitRefinement> {
  const query = new URLSearchParams({
    candidate_subject_id: payload.candidate_subject_id,
    source_ownership_digest: payload.source_ownership_digest,
    after_frame: String(payload.after_frame),
    before_frame: String(payload.before_frame),
  });
  if (payload.review_target_id) query.set('review_target_id', payload.review_target_id);
  if (payload.continuity_group_id) query.set('continuity_group_id', payload.continuity_group_id);
  return request<ReviewedTemporalSplitRefinement>(
    `/api/matches/${encodeURIComponent(matchId)}/reviewed-identity/temporal-split/refine?${query}`,
  );
}

export async function finalizeReviewedIdentityCorrections(
  matchId: string,
): Promise<ReviewedCorrectionFinalizeResponse> {
  return request<ReviewedCorrectionFinalizeResponse>(
    `/api/matches/${encodeURIComponent(matchId)}/reviewed-identity/corrections/finalize`,
    { method: 'POST' },
  );
}

export async function getMixedPlayersReview(
  matchId: string,
): Promise<import('./types').MixedPlayersReviewQueue> {
  return request<import('./types').MixedPlayersReviewQueue>(
    `/api/matches/${encodeURIComponent(matchId)}/reviewed-identity/mixed-players`,
  );
}

export async function getMixedPlayerReviewCase(
  matchId: string,
  caseId: string,
): Promise<import('./types').MixedPlayerFocusedCaseResponse> {
  return request<import('./types').MixedPlayerFocusedCaseResponse>(
    `/api/matches/${encodeURIComponent(matchId)}/reviewed-identity/mixed-players/${encodeURIComponent(caseId)}`,
  );
}

export async function getMixedBoundaryRefinement(
  matchId: string,
  candidateSubjectId: string,
  afterFrame: number,
  beforeFrame: number,
  caseId?: string,
): Promise<import('./types').MixedBoundaryRefinement> {
  const query = new URLSearchParams({
    candidate_subject_id: candidateSubjectId,
    after_frame: String(afterFrame),
    before_frame: String(beforeFrame),
  });
  if (caseId) query.set('case_id', caseId);
  return request<import('./types').MixedBoundaryRefinement>(
    `/api/matches/${encodeURIComponent(matchId)}/reviewed-identity/mixed-players/refine?${query}`,
  );
}

export async function getConcurrentLaneRefinement(
  matchId: string,
  payload: {
    candidate_subject_id: string;
    parent_case_id: string;
    parent_source_digest: string;
    lane_id: string;
    lane_source_digest: string;
    after_frame: number;
    before_frame: number;
    review_target_id?: string;
    continuity_group_id?: string;
  },
): Promise<import('./types').ConcurrentLaneRefinement> {
  const query = new URLSearchParams({
    candidate_subject_id: payload.candidate_subject_id,
    parent_case_id: payload.parent_case_id,
    parent_source_digest: payload.parent_source_digest,
    lane_id: payload.lane_id,
    lane_source_digest: payload.lane_source_digest,
    after_frame: String(payload.after_frame),
    before_frame: String(payload.before_frame),
  });
  if (payload.review_target_id) query.set('review_target_id', payload.review_target_id);
  if (payload.continuity_group_id) query.set('continuity_group_id', payload.continuity_group_id);
  return request<import('./types').ConcurrentLaneRefinement>(
    `/api/matches/${encodeURIComponent(matchId)}/reviewed-identity/concurrent-lanes/refine?${query}`,
  );
}

export async function saveMixedPlayerResolution(
  matchId: string,
  payload: import('./types').MixedPlayerResolutionRequest,
): Promise<import('./types').MixedPlayerResolutionResponse> {
  return request<import('./types').MixedPlayerResolutionResponse>(
    `/api/matches/${encodeURIComponent(matchId)}/reviewed-identity/mixed-players/resolve`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
  );
}

export function reviewedVideoUrl(matchId: string, digest: string): string {
  return `${API_BASE}/api/matches/${encodeURIComponent(matchId)}/reviewed-output/video?digest=${encodeURIComponent(digest)}`;
}

export async function getReviewedStats(matchId: string): Promise<ReviewedStatsResponse> {
  return request<ReviewedStatsResponse>(`/api/matches/${encodeURIComponent(matchId)}/reviewed-output/stats`);
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

export async function startAnalysisJob(matchId: string, payload: AnalysisPayload): Promise<AnalysisJob> {
  return request<AnalysisJob>(`/api/matches/${matchId}/analyze/background`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
}

export async function getAnalysisJob(jobId: string): Promise<AnalysisJob> {
  return request<AnalysisJob>(`/api/analysis-jobs/${encodeURIComponent(jobId)}`);
}

export async function listAnalysisJobs(matchId: string): Promise<AnalysisJobsDocument> {
  return request<AnalysisJobsDocument>(`/api/matches/${encodeURIComponent(matchId)}/analysis-jobs`);
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

export async function getChangeCandidates(matchId: string): Promise<ChangeCandidatesDocument> {
  return request<ChangeCandidatesDocument>(`/api/matches/${matchId}/change-candidates`);
}

export async function reviewChangeCandidates(
  matchId: string,
  updates: ChangeCandidateReviewUpdate[],
): Promise<ChangeCandidatesDocument> {
  return request<ChangeCandidatesDocument>(`/api/matches/${matchId}/change-candidates/review`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ updates })
  });
}

export async function getPlayerIdentityReview(matchId: string): Promise<PlayerIdentityReviewState> {
  return request<PlayerIdentityReviewState>(`/api/matches/${matchId}/player-identity`);
}

export async function getIdentityReviewGallery(matchId: string): Promise<IdentityReviewGalleryDocument> {
  return request<IdentityReviewGalleryDocument>(`/api/matches/${matchId}/identity-review-gallery`);
}

export async function generateIdentityReviewGallery(
  matchId: string,
  samplesPerStint?: number,
  force = false,
): Promise<IdentityReviewGalleryDocument> {
  const params = new URLSearchParams({ force: force ? 'true' : 'false' });
  if (typeof samplesPerStint === 'number') params.set('samples_per_stint', String(samplesPerStint));
  return request<IdentityReviewGalleryDocument>(`/api/matches/${matchId}/identity-review-gallery?${params}`, {
    method: 'POST',
  });
}

export async function splitIdentityReviewGallery(
  matchId: string,
  splits: Array<{ stable_subject_id: string; parent_stint_id: string; frame: number; reason?: string }>,
  samplesPerStint = 8,
): Promise<IdentityReviewGalleryDocument> {
  return request<IdentityReviewGalleryDocument>(`/api/matches/${matchId}/identity-review-gallery/splits`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ splits, samples_per_stint: samplesPerStint }),
  });
}

export async function getIdentityCropReview(matchId: string): Promise<IdentityCropReviewDocument> {
  return request<IdentityCropReviewDocument>(`/api/matches/${matchId}/identity-crop-review`);
}

export async function getInitialIdentityAudit(
  matchId: string,
  force = false,
): Promise<InitialIdentityAuditDocument> {
  return request<InitialIdentityAuditDocument>(
    `/api/matches/${encodeURIComponent(matchId)}/initial-identity-audit?force=${String(force)}`,
  );
}

export async function getInitialIdentityAuditSeeds(
  matchId: string,
): Promise<InitialIdentityAuditSeedStoreDocument> {
  return request<InitialIdentityAuditSeedStoreDocument>(
    `/api/matches/${encodeURIComponent(matchId)}/initial-identity-audit/seeds`,
  );
}

export async function saveInitialIdentityAuditSeeds(
  matchId: string,
  updates: InitialIdentityAuditSeedUpdate[],
  telemetryEvents: InitialIdentityAuditTelemetryEvent[] = [],
  finalize = false,
): Promise<InitialIdentityAuditSeedStoreDocument> {
  return request<InitialIdentityAuditSeedStoreDocument>(
    `/api/matches/${encodeURIComponent(matchId)}/initial-identity-audit/seeds`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        updates,
        telemetry_events: telemetryEvents,
        finalize,
      }),
    },
  );
}

export async function getSecondHalfIdentityReanchor(
  matchId: string,
  force = false,
): Promise<SecondHalfIdentityReanchorDocument> {
  return request<SecondHalfIdentityReanchorDocument>(
    `/api/matches/${encodeURIComponent(matchId)}/second-half-identity-reanchor?force=${String(force)}`,
  );
}

export async function getSecondHalfIdentityReanchorSeeds(
  matchId: string,
): Promise<InitialIdentityAuditSeedStoreDocument> {
  return request<InitialIdentityAuditSeedStoreDocument>(
    `/api/matches/${encodeURIComponent(matchId)}/second-half-identity-reanchor/seeds`,
  );
}

export async function saveSecondHalfIdentityReanchorSeeds(
  matchId: string,
  updates: InitialIdentityAuditSeedUpdate[],
  telemetryEvents: InitialIdentityAuditTelemetryEvent[] = [],
): Promise<InitialIdentityAuditSeedStoreDocument> {
  return request<InitialIdentityAuditSeedStoreDocument>(
    `/api/matches/${encodeURIComponent(matchId)}/second-half-identity-reanchor/seeds`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        updates,
        telemetry_events: telemetryEvents,
      }),
    },
  );
}

export async function finishProductFlowBenchmarkH1(
  benchmarkId: string,
): Promise<unknown> {
  return request(
    `/api/product-flow-benchmarks/${encodeURIComponent(benchmarkId)}/h1/finish`,
    { method: 'POST' },
  );
}

export async function finishProductFlowBenchmarkH2(
  benchmarkId: string,
): Promise<unknown> {
  return request(
    `/api/product-flow-benchmarks/${encodeURIComponent(benchmarkId)}/h2/finish`,
    { method: 'POST' },
  );
}

export async function saveIdentityCropReview(
  matchId: string,
  updates: IdentityCropReviewUpdate[],
): Promise<IdentityCropReviewDocument> {
  return request<IdentityCropReviewDocument>(`/api/matches/${matchId}/identity-crop-review`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ updates }),
  });
}

export async function getIdentityRosterSubjectReview(matchId: string): Promise<IdentityRosterSubjectReviewDocument> {
  return request<IdentityRosterSubjectReviewDocument>(`/api/matches/${matchId}/identity-roster-subject-review`);
}

export async function saveIdentityRosterSubjectReview(
  matchId: string,
  updates: IdentityRosterSubjectReviewUpdate[],
  telemetryEvents: IdentityRosterSubjectTelemetryEvent[] = [],
): Promise<IdentityRosterSubjectReviewDocument> {
  return request<IdentityRosterSubjectReviewDocument>(`/api/matches/${matchId}/identity-roster-subject-review`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ updates, telemetry_events: telemetryEvents }),
  });
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

export async function getMatchPhaseConfig(matchId: string): Promise<MatchPhaseConfigDocument> {
  return request<MatchPhaseConfigDocument>(`/api/matches/${matchId}/match-phase-config`);
}

export async function saveMatchPhaseConfig(
  matchId: string,
  payload: MatchPhaseConfigPayload,
): Promise<MatchPhaseConfigDocument> {
  return request<MatchPhaseConfigDocument>(`/api/matches/${matchId}/match-phase-config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
}

export async function getPassCandidates(matchId: string): Promise<PassCandidatesDocument> {
  return request<PassCandidatesDocument>(`/api/matches/${matchId}/pass-candidates`);
}

export async function reviewPassCandidates(
  matchId: string,
  updates: PassCandidateReviewUpdate[],
): Promise<PassCandidatesDocument> {
  return request<PassCandidatesDocument>(`/api/matches/${matchId}/pass-candidates/review`, {
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

export async function getStaticPublicMatchReport(matchId: string): Promise<PublicMatchReport> {
  const res = await fetch(`/published/matches/${encodeURIComponent(matchId)}/public_report.json`);
  if (!res.ok) {
    throw new Error(`${res.status}: Public report not found`);
  }
  return res.json() as Promise<PublicMatchReport>;
}

export async function getReviewedMatchReport(matchId: string): Promise<PublicMatchReport> {
  return request<PublicMatchReport>(
    `/api/matches/${encodeURIComponent(matchId)}/reviewed-report`,
  );
}

export async function deletePublishedMatch(matchId: string): Promise<{ status: string; match: PublishedMatch }> {
  return request<{ status: string; match: PublishedMatch }>(`/api/published/matches/${matchId}`, { method: 'DELETE' });
}
