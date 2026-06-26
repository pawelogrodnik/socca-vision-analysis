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

export type StablePlayerStatus =
  | 'active'
  | 'ignore'
  | 'referee'
  | 'false_positive'
  | 'unknown';

export type MovementStats = {
  playing_time_sec: number;
  detected_time_sec: number;
  missing_time_sec: number;
  ambiguous_time_sec: number;
  observed_distance_m: number;
  estimated_gap_distance_m: number;
  total_distance_m: number;
  avg_speed_mps: number;
  avg_speed_kmh: number;
  observed_avg_speed_mps: number;
  top_speed_mps: number;
  top_speed_kmh: number;
  detected_coverage: number;
  estimated_distance_ratio: number;
  distance_quality: 'high' | 'medium' | 'low' | string;
  samples_used: number;
  active_frames: number;
  detected_frames: number;
  missing_frames: number;
  ambiguous_frames: number;
  predicted_frames: number;
  observed_segments: number;
  estimated_gap_segments: number;
  skipped_outlier_segments: number;
  skipped_long_gap_segments: number;
  stats_note?: string;
};

export type StablePlayer = {
  slot_id?: string;
  stable_subject_id: string;
  stable_player_id: string;
  identity_semantics?: 'stint_first' | string;
  status: StablePlayerStatus;
  team_label: 'A' | 'B' | 'U' | string;
  team_id?: string | null;
  team_name?: string | null;
  team_confidence?: number | null;
  confidence: 'high' | 'medium' | 'low' | string;
  confidence_score?: number | null;
  duration_sec: number;
  tracklet_ids: string[];
  raw_track_ids: number[];
  tracklet_count: number;
  positions_count: number;
  detected_frames?: number;
  predicted_frames?: number;
  missing_frames?: number;
  ambiguous_frames?: number;
  blocked_team_switches?: number;
  blocked_identity_switches?: number;
  rejected_candidates?: Array<Record<string, unknown>>;
  identity_events?: Array<Record<string, unknown>>;
  stint_count?: number;
  mean_detection_confidence?: number | null;
  jersey_color_hex?: string | null;
  movement_stats?: MovementStats;
  trajectory_m: Array<{
    frame: number;
    time_sec: number;
    pitch_m: number[] | null;
    source?: string;
    status?: string;
  }>;
  risky_links: Array<Record<string, unknown>>;
  suspicious_assignments?: Array<Record<string, unknown>>;
  stints?: Array<{
    stint_id: string;
    slot_id?: string;
    start_frame: number;
    end_frame: number;
    start_time_sec: number;
    end_time_sec: number;
    duration_sec?: number;
    status?: string;
    detected_frames?: number;
    predicted_frames?: number;
    missing_frames?: number;
    ambiguous_frames?: number;
    tracklet_ids?: string[];
    raw_track_ids?: number[];
  }>;
};

export type StablePlayersDocument = {
  schema_version: string;
  generated_at: string;
  updated_at?: string;
  source?: string;
  identity_semantics?: 'stint_first' | string;
  pitch_dimensions_m: {
    width_m: number;
    length_m: number;
  };
  players: StablePlayer[];
  summary: {
    raw_tracks: number;
    clean_tracklets: number;
    rejected_tracklets: number;
    stable_players: number;
    stable_player_candidates?: number;
    suppressed_extra_candidates?: number;
    team_counts: Record<string, number>;
    risky_links: number;
    low_confidence_players: number;
    interpolated_frames?: number;
    interpolated_gaps?: number;
    skipped_interpolation_gaps?: number;
    players_with_interpolation?: number;
    longest_interpolated_gap_frames?: number;
    detected_frames?: number;
    predicted_frames?: number;
    missing_frames?: number;
    ambiguous_frames?: number;
    blocked_team_switches?: number;
    blocked_identity_switches?: number;
    rejected_candidates?: number;
    suspicious_assignments?: number;
    stints_total?: number;
    total_distance_m?: number;
    observed_distance_m?: number;
    estimated_gap_distance_m?: number;
    players_with_estimated_distance?: number;
  };
  frame_detection_summary?: Record<string, unknown>;
  movement_stats_summary?: Record<string, unknown>;
};

export type GlobalIdentityReport = {
  schema_version: string;
  generated_at: string;
  status: string;
  resolver_version?: string;
  identity_semantics?: string;
  summary?: Record<string, unknown>;
  frame_detection_summary?: Record<string, unknown>;
  problem_frames?: Array<Record<string, unknown>>;
  low_visible_frames?: Array<Record<string, unknown>>;
  ambiguous_frame_ranges?: Array<Record<string, unknown>>;
  blocked_switches?: Array<Record<string, unknown>>;
  rejected_candidates?: Array<Record<string, unknown>>;
  rejected_start_candidates?: Array<Record<string, unknown>>;
  risky_slots?: Array<Record<string, unknown>>;
};

export type TeamClustersDocument = {
  schema_version: string;
  generated_at: string;
  method: string;
  clusters: Array<{
    cluster_id: string;
    team_label: string;
    team_id?: string | null;
    team_name?: string | null;
    color_hex?: string | null;
    center_rgb?: number[] | null;
    center_hsv?: number[] | null;
    center_lab?: number[] | null;
    confidence: number;
    tracklets_count: number;
    candidate_tracklets_count?: number;
    reference_tracklets_count?: number;
  }>;
  reference_tracklets_count?: number;
  candidate_tracklets_count?: number;
  white_reference_tracklets_count?: number;
  bib_reference_tracklets_count?: number;
  goalkeeper_color_outliers_count?: number;
  unknown_tracklets: string[];
};

export type StablePlayersReviewState = {
  stable_players: StablePlayersDocument;
  stabilization_report?: Record<string, unknown> | null;
  global_identity_report?: GlobalIdentityReport | null;
  team_clusters?: TeamClustersDocument | null;
  movement_stats?: MovementStatsDocument | null;
};

export type MovementStatsDocument = {
  schema_version: string;
  generated_at: string;
  source: string;
  identity_semantics: string;
  units: Record<string, string>;
  summary: Record<string, unknown>;
  players: Array<{
    stable_player_id: string;
    stable_subject_id: string;
    slot_id?: string;
    team_label?: string;
    team_id?: string | null;
    team_name?: string | null;
    confidence?: string;
    confidence_score?: number | null;
    movement_stats: MovementStats;
  }>;
};

export type StablePlayerReviewUpdate = {
  stable_subject_id?: string;
  stable_player_id?: string;
  team_label?: 'A' | 'B' | 'U';
  team_id?: string | null;
  team_name?: string | null;
  status?: StablePlayerStatus;
};

export type StablePlayerReviewPayload = {
  swap_teams?: boolean;
  updates?: StablePlayerReviewUpdate[];
};

export type AssignmentSummary = {
  raw_tracklets: number;
  assignments_total: number;
  assigned_tracklets: number;
  ignored_tracklets: number;
  unassigned_tracklets: number;
  unique_players_total: number;
  unique_players_by_team: Record<string, number>;
  assigned_tracklets_by_team: Record<string, number>;
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

export type Match = MatchMetadataPayload & {
  id: string;
  video_filename: string;
  video: VideoMetadata;
  created_at?: string;
  updated_at?: string;
  published_match_id?: string;
  pitch_config?: unknown;
  analysis_report?: AnalysisReport;
  stable_players?: StablePlayersDocument;
  stabilization_report?: Record<string, unknown>;
  global_identity_report?: GlobalIdentityReport;
  team_clusters?: TeamClustersDocument;
  frame_detection_counts?: Record<string, unknown>;
  movement_stats?: MovementStatsDocument;
  tracklets?: Record<string, unknown>;
  tracking_quality_report?: Record<string, unknown>;
  match_package?: MatchPackage;
  player_assignments?: PlayerAssignmentsDocument;
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
  run_id?: string;
  generated_at?: string;
  run_directory?: string;
  run_manifest?: string;
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
    stable_players?: string;
    global_identity?: string;
    global_identity_report?: string;
    stabilization_report?: string;
    stable_overlay_preview?: string;
    debug_identity_overlay?: string;
    team_clusters?: string;
    frame_detection_counts?: string;
    movement_stats?: string;
    tracklets?: string;
    tracking_quality_report?: string;
  };
  run_artifacts?: Record<string, string>;
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
  stable_players?: StablePlayersDocument | null;
  global_identity?: Record<string, unknown> | null;
  global_identity_report?: GlobalIdentityReport | null;
  stabilization_report?: Record<string, unknown> | null;
  team_clusters?: TeamClustersDocument | null;
  frame_detection_counts?: Record<string, unknown> | null;
  movement_stats?: MovementStatsDocument | null;
  tracklets?: Record<string, unknown> | null;
  tracking_quality_report?: Record<string, unknown> | null;
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
  stable_players?: Array<Record<string, unknown>>;
};
