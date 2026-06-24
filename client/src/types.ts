export type VideoMetadata = {
  fps: number;
  frame_count: number;
  width: number;
  height: number;
  duration_sec: number;
  path?: string;
};

export type Player = {
  id?: string;
  name: string;
  number?: string | null;
  role: 'player' | 'goalkeeper' | 'guest' | 'unknown' | string;
  is_guest: boolean;
};

export type Team = {
  id?: string;
  name: string;
  color?: string | null;
  players: Player[];
};

export type MatchMetadataPayload = {
  title: string;
  match_date?: string | null;
  season?: string | null;
  venue?: string | null;
  format: string;
  status: string;
  teams: Team[];
};

export type TrackletAssignmentStatus = 'unassigned' | 'assigned' | 'unknown' | 'false_positive' | 'referee' | 'opponent';

export type PlayerAssignment = {
  tracklet_id: number;
  status: TrackletAssignmentStatus;
  team_id?: string | null;
  player_id?: string | null;
  notes?: string;
};

export type PlayerAssignmentsDocument = {
  schema_version: string;
  updated_at: string;
  assignments: PlayerAssignment[];
  summary: AssignmentSummary;
};

export type AssignmentSummary = {
  raw_tracklets?: number;
  assignments_total?: number;
  assigned_tracklets: number;
  ignored_tracklets?: number;
  unassigned_tracklets?: number;
  unique_players_total: number;
  unique_players_by_team: Record<string, number>;
  assigned_tracklets_by_team?: Record<string, number>;
  roster_players_by_team: Record<string, number>;
};

export type TrackletSummary = {
  tracklet_id: number;
  start_time_sec?: number | null;
  end_time_sec?: number | null;
  duration_sec?: number | null;
  positions_count?: number | null;
  avg_confidence?: number | null;
  first_pitch_m?: number[] | null;
  last_pitch_m?: number[] | null;
  first_bbox_xyxy?: number[] | null;
  last_bbox_xyxy?: number[] | null;
};

export type TrackletReviewState = {
  tracklets: TrackletSummary[];
  assignments: PlayerAssignment[];
  summary: AssignmentSummary;
};

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
  noise_tracklets?: TrackletSummary[];
  candidates: IdentityCandidate[];
  summary: IdentitySummary;
};

export type IdentityAssignmentsDocument = {
  schema_version: string;
  updated_at: string;
  assignments: IdentityAssignment[];
  summary: IdentitySummary;
};

export type Match = MatchMetadataPayload & {
  id: string;
  video_filename: string;
  video: VideoMetadata;
  created_at?: string;
  updated_at?: string;
  published_match_id?: string;
  pitch_config?: unknown;
  analysis_report?: AnalysisReport;
  match_package?: MatchPackage;
  player_assignments?: PlayerAssignmentsDocument;
  identity_candidates?: IdentityReviewState;
  identity_assignments?: IdentityAssignmentsDocument;
};

export type AnalysisPayload = {
  adapter: 'yolo' | 'motion';
  max_seconds: number;
  frame_stride: number;
  yolo_model: string;
  yolo_conf: number;
  yolo_imgsz: number;
  yolo_tracker: string;
  yolo_device: string | null;
};

export type AnalysisReport = {
  status: 'completed' | 'failed' | string;
  analysis_type: string;
  note?: string;
  frames_processed?: number;
  detections_kept?: number;
  detections_rejected_outside_pitch?: number;
  tracks_count?: number;
  warnings?: string[];
  error?: {
    type?: string;
    message?: string;
  };
  artifacts?: {
    tracks_json: string;
    overlay_preview: string;
    heatmap_all_tracks: string;
  };
  [key: string]: unknown;
};

export type MatchPackage = {
  schema_version: string;
  generated_at: string;
  contains_video: boolean;
  match: Match;
  team_count: number;
  player_count: number;
  assets: Record<string, string>;
  publish_status: string;
  player_assignments?: PlayerAssignmentsDocument | null;
  identity_candidates?: IdentityReviewState | null;
  identity_assignments?: IdentityAssignmentsDocument | null;
  [key: string]: unknown;
};

export type PublishedMatch = {
  id: string;
  source_match_id: string;
  title: string;
  match_date?: string | null;
  season?: string | null;
  venue?: string | null;
  format?: string | null;
  status: string;
  schema_version: string;
  team_count: number;
  player_count: number;
  tracks_count?: number | null;
  frames_processed?: number | null;
  detections_kept?: number | null;
  warnings_count: number;
  created_at: string;
  updated_at: string;
};

export type PublishedMatchDetail = PublishedMatch & {
  package: MatchPackage;
  teams: Array<Record<string, unknown>>;
  players: Array<Record<string, unknown>>;
};
