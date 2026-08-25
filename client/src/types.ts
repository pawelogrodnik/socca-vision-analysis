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

export type IdentityReviewTeamScope = 'complete_roster' | 'partial_roster' | 'players_of_interest' | 'unspecified' | 'team_stats_only';
export type IdentityReviewScopeChoice = 'A' | 'B' | 'both';
export type IdentityReviewScope = {
  schema_version?: string;
  teams: { A: IdentityReviewTeamScope; B: IdentityReviewTeamScope };
};

export type MatchMetadataPayload = {
  title: string;
  match_date?: string | null;
  season?: string | null;
  venue?: string | null;
  format: string;
  status: string;
  teams: Team[];
  identity_review_scope?: IdentityReviewScope | null;
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

export type SprintCandidate = {
  start_frame?: number;
  end_frame?: number;
  start_time_sec?: number;
  end_time_sec?: number;
  duration_sec?: number;
  distance_m?: number;
  max_speed_kmh?: number;
  reason?: string;
};

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
  peak_sustained_speed_mps?: number;
  peak_sustained_speed_kmh?: number;
  top_speed_mps: number;
  top_speed_kmh: number;
  raw_segment_top_speed_mps?: number;
  raw_segment_top_speed_kmh?: number;
  detected_coverage: number;
  estimated_distance_ratio: number;
  distance_quality: 'high' | 'medium' | 'low' | string;
  speed_quality?: 'high' | 'medium' | 'low' | string;
  speed_window_sec?: number;
  samples_used: number;
  active_frames: number;
  detected_frames: number;
  missing_frames: number;
  ambiguous_frames: number;
  predicted_frames: number;
  observed_segments: number;
  estimated_gap_segments: number;
  skipped_outlier_segments: number;
  skipped_speed_outlier_segments?: number;
  skipped_long_gap_segments: number;
  sustained_speed_windows?: number;
  intensity?: {
    high_intensity_threshold_kmh?: number;
    sprint_threshold_kmh?: number;
    min_sprint_duration_sec?: number;
    high_intensity_time_sec?: number;
    high_intensity_distance_m?: number;
    high_intensity_segments?: number;
    high_intensity_distance_ratio?: number;
    sprint_count?: number;
    sprint_time_sec?: number;
    sprint_distance_m?: number;
    sprint_distance_ratio?: number;
    longest_sprint_time_sec?: number;
    longest_sprint_distance_m?: number;
    max_sprint_speed_kmh?: number;
    trusted_speed_segments?: number;
    sprint_candidate_count?: number;
    rejected_sprint_candidate_count?: number;
    best_sprint_candidate_speed_kmh?: number;
    best_sprint_candidate_duration_sec?: number;
    best_sprint_candidate_distance_m?: number;
    best_sprint_candidate_reason?: string;
    best_rejected_sprint_candidate?: SprintCandidate;
    rejected_sprint_candidates?: SprintCandidate[];
  };
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
  heatmap_path?: string;
  heatmap_samples?: number;
  heatmap_quality?: 'high' | 'medium' | 'low' | string;
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

export type AnalysisQualityComponent = {
  name: string;
  quality: 'high' | 'medium' | 'low' | string;
  score: number;
  warnings: string[];
  metrics: Record<string, unknown>;
};

export type AnalysisQualityReport = {
  schema_version: string;
  generated_at: string;
  status: string;
  quality: 'high' | 'medium' | 'low' | string;
  score: number;
  recommendation: string;
  summary: Record<string, unknown>;
  components: {
    tracking?: AnalysisQualityComponent;
    identity_stability?: AnalysisQualityComponent;
    stats?: AnalysisQualityComponent;
    team_assignment?: AnalysisQualityComponent;
    [key: string]: AnalysisQualityComponent | undefined;
  };
  warnings: string[];
  frame_ranges?: Record<string, Array<Record<string, number>>>;
  top_problem_frames?: Array<Record<string, unknown>>;
};

export type ChangeCandidateReviewStatus =
  | 'needs_review'
  | 'confirmed'
  | 'rejected'
  | 'uncertain'
  | 'ignored';

export type ChangeCandidate = {
  candidate_id: string;
  event_type?: 'substitution_candidate' | string;
  team_label?: string | null;
  team_id?: string | null;
  team_name?: string | null;
  time_sec?: number | null;
  gap_sec?: number | null;
  confidence?: string | null;
  confidence_score?: number | null;
  status?: ChangeCandidateReviewStatus | string;
  review_status?: ChangeCandidateReviewStatus;
  review_source?: string;
  review_notes?: string;
  reviewed_at?: string;
  out_stable_subject_id?: string | null;
  out_stable_player_id?: string | null;
  out_slot_id?: string | null;
  out_end_time_sec?: number | null;
  in_stable_subject_id?: string | null;
  in_stable_player_id?: string | null;
  in_slot_id?: string | null;
  in_start_time_sec?: number | null;
  out_candidates?: Array<Record<string, unknown>>;
  reid_candidates?: Array<Record<string, unknown>>;
  suggested_existing_stable_subject_id?: string | null;
  suggested_real_player_id?: string | null;
  suggested_real_player_name?: string | null;
  reviewed_out_stable_subject_id?: string | null;
  linked_existing_stable_subject_id?: string | null;
  reviewed_player_id?: string | null;
  notes?: string[];
};

export type ChangeCandidateReviewUpdate = {
  candidate_id: string;
  review_status: ChangeCandidateReviewStatus;
  out_stable_subject_id?: string | null;
  linked_existing_stable_subject_id?: string | null;
  player_id?: string | null;
  notes?: string;
};

export type ChangeCandidatesDocument = {
  schema_version?: string;
  generated_at?: string;
  updated_at?: string;
  source?: string;
  experimental?: boolean;
  candidate_semantics?: string;
  parameters?: Record<string, unknown>;
  summary?: Record<string, unknown>;
  skipped_reasons?: Record<string, number>;
  candidates: ChangeCandidate[];
};

export type ChangeReviewReport = {
  schema_version?: string;
  generated_at?: string;
  source?: string;
  experimental?: boolean;
  summary?: Record<string, unknown>;
  warnings?: string[];
  notes?: string[];
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

export type TeamConfigDocument = {
  schema_version: string;
  generated_at: string;
  updated_at?: string;
  source: string;
  match_id?: string;
  team_assignment_semantics: string;
  locked: boolean;
  teams: Array<{
    team_label: 'A' | 'B' | string;
    team_id?: string | null;
    team_name: string;
    display_color?: string | null;
    detected_color_hex?: string | null;
    cluster_id?: string | null;
    cluster_confidence?: number | null;
    reference_tracklets_count?: number;
    candidate_tracklets_count?: number;
    stable_players_count?: number;
    locked: boolean;
    assignment_source?: string;
    goalkeeper_exceptions?: string[];
    notes?: string;
  }>;
  unknown_stable_players?: number;
  team_clusters_method?: string | null;
  team_clusters_summary?: Record<string, unknown>;
};

export type TeamStatsDocument = {
  schema_version: string;
  generated_at: string;
  source: string;
  scope: string;
  units: Record<string, string>;
  summary: Record<string, unknown>;
  teams: Array<Record<string, unknown>>;
};

export type PlayerHeatmapsDocument = {
  schema_version: string;
  generated_at: string;
  source: string;
  identity_semantics: string;
  method: string;
  pitch_dimensions_m: {
    width_m: number;
    length_m: number;
  };
  image_size_px: {
    width: number;
    height: number;
  };
  summary: Record<string, unknown>;
  heatmaps: Array<{
    stable_player_id: string;
    stable_subject_id: string;
    slot_id?: string;
    team_label?: string;
    team_id?: string | null;
    team_name?: string | null;
    path: string;
    samples: number;
    detected_samples: number;
    interpolated_samples: number;
    quality: 'high' | 'medium' | 'low' | string;
    included_sources: string[];
    ignored_sources: string[];
  }>;
};

export type TeamConfigReviewState = {
  team_config: TeamConfigDocument;
  team_stats?: TeamStatsDocument | null;
  player_stats?: PlayerStatsDocument | null;
  team_clusters?: TeamClustersDocument | null;
};

export type TeamConfigReviewPayload = {
  teams: Array<{
    team_label: 'A' | 'B';
    team_id?: string | null;
    team_name?: string;
    display_color?: string | null;
    detected_color_hex?: string | null;
    locked?: boolean;
    notes?: string;
    goalkeeper_exceptions?: string[];
  }>;
};

export type StablePlayersReviewState = {
  stable_players: StablePlayersDocument;
  stabilization_report?: Record<string, unknown> | null;
  global_identity_report?: GlobalIdentityReport | null;
  team_clusters?: TeamClustersDocument | null;
  movement_stats?: MovementStatsDocument | null;
  player_stats?: PlayerStatsDocument | null;
  resolved_player_stats?: ResolvedPlayerStatsDocument | null;
  player_heatmaps?: PlayerHeatmapsDocument | null;
  team_config?: TeamConfigDocument | null;
  team_stats?: TeamStatsDocument | null;
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

export type PlayerStatsDocument = {
  schema_version: string;
  generated_at: string;
  source: string;
  identity_semantics: string;
  scope: 'tracking_only_no_ball' | string;
  units: Record<string, string>;
  summary: Record<string, unknown>;
  teams: Array<Record<string, unknown>>;
  players: Array<{
    stable_player_id: string;
    stable_subject_id: string;
    slot_id?: string;
    identity_semantics?: string;
    status?: string;
    team_label?: string;
    team_id?: string | null;
    team_name?: string | null;
    confidence?: string;
    confidence_score?: number | null;
    tracklet_ids?: string[];
    raw_track_ids?: number[];
    stint_count?: number;
    time: Record<string, number>;
    distance: Record<string, number | string>;
    speed: Record<string, number>;
    intensity?: Record<string, unknown>;
    frames: Record<string, number>;
    segments: Record<string, number>;
    tracking_only: boolean;
    stats_note?: string;
  }>;
};

export type ResolvedPlayerStatsDocument = {
  schema_version: string;
  generated_at: string;
  source: string;
  stats_source: string;
  calculation_method?: string;
  identity_assignments_updated_at?: string | null;
  is_stale?: boolean;
  identity_semantics: string;
  scope: 'resolved_player_tracking_only_no_ball' | string;
  units: Record<string, string>;
  summary: Record<string, unknown>;
  teams: Array<Record<string, unknown>>;
  players: Array<Record<string, unknown>>;
  skipped_assignments?: Array<Record<string, unknown>>;
  quality_report?: string;
};

export type PlayerProfileStatsDocument = {
  schema_version: string;
  generated_at: string;
  scope: 'player_profile_tracking_only_no_ball' | string;
  identity_semantics: string;
  player: {
    player_id: string;
    player_name?: string | null;
    player_number?: string | null;
    player_role?: string | null;
    is_guest?: boolean;
    team_id?: string | null;
    team_name?: string | null;
    known_from_registry?: boolean;
  };
  teams: Array<Record<string, unknown>>;
  summary: Record<string, unknown>;
  appearances: Array<{
    match_id: string;
    match_title?: string | null;
    match_date?: string | null;
    season?: string | null;
    venue?: string | null;
    format?: string | null;
    match_status?: string | null;
    team_label?: string | null;
    team_id?: string | null;
    team_name?: string | null;
    player_name?: string | null;
    player_number?: string | null;
    player_role?: string | null;
    stable_player_ids?: string[];
    stable_subject_ids?: string[];
    source_stable_slots?: Array<Record<string, unknown>>;
    time?: Record<string, number>;
    distance?: Record<string, number | string>;
    speed?: Record<string, number | string>;
    intensity?: Record<string, unknown>;
    frames?: Record<string, number>;
    segments?: Record<string, number>;
    review_warnings?: string[];
    distance_quality?: string;
    speed_quality?: string;
    tracking_only?: boolean;
  }>;
  notes?: string[];
};

export type TeamProfileStatsDocument = {
  schema_version: string;
  generated_at: string;
  scope: 'team_tracking_only_no_ball' | string;
  identity_semantics: string;
  team: {
    team_id: string;
    team_name?: string | null;
    color?: string | null;
    known_from_registry?: boolean;
    roster_players?: Player[];
  };
  season?: string | null;
  available_seasons: string[];
  summary: Record<string, unknown>;
  players: Array<Record<string, unknown>>;
  matches: Array<Record<string, unknown>>;
  missing_matches: Array<Record<string, unknown>>;
  notes?: string[];
};

export type AnalysisRunSummary = {
  run_id?: string;
  status?: string;
  analysis_type?: string;
  generated_at?: string;
  frames_processed?: number;
  tracks_count?: number;
  stable_players_count?: number;
  parameters?: Record<string, unknown>;
  run_directory?: string;
  run_manifest?: string;
};

export type FrameDetectionCountsDocument = {
  schema_version?: string;
  generated_at?: string;
  source?: string;
  target_players?: number;
  summary?: Record<string, unknown>;
  frames?: Array<Record<string, unknown>>;
};

export type TrackingQualityReport = {
  schema_version?: string;
  generated_at?: string;
  source?: string;
  parameters?: Record<string, unknown>;
  summary?: Record<string, unknown>;
  frame_team_counts?: Array<Record<string, unknown>>;
  suspicious_events?: Array<Record<string, unknown>>;
  rejected_tracklets?: Array<Record<string, unknown>>;
};

export type BallTrackingReport = {
  schema_version?: string;
  generated_at?: string;
  source?: string;
  status?: string;
  experimental?: boolean;
  summary?: Record<string, unknown>;
  parameters?: Record<string, unknown>;
  warnings?: string[];
};

export type BallQualityReport = {
  schema_version?: string;
  generated_at?: string;
  source?: string;
  experimental?: boolean;
  summary?: Record<string, unknown>;
  recommendation?: {
    decision?: string;
    custom_dataset_recommended?: boolean;
    confidence?: string;
    reasons?: string[];
    next_step?: string;
  };
  diagnostics?: Record<string, unknown>;
  notes?: string[];
};

export type PossessionReport = {
  schema_version?: string;
  generated_at?: string;
  source?: string;
  status?: string;
  experimental?: boolean;
  summary?: Record<string, unknown>;
  possession_timeline?: PossessionTimelinePoint[];
  warnings?: string[];
  notes?: string[];
};

export type PossessionTimelinePoint = {
  index: number;
  time_sec: number;
  start_time_sec: number;
  end_time_sec: number;
  frames: number;
  controlled_frames: number;
  contested_frames: number;
  free_frames: number;
  unknown_frames: number;
  team_controlled_frames: Record<'A' | 'B', number>;
  team_a_share?: number | null;
  team_b_share?: number | null;
  controlled_coverage?: number;
  unknown_coverage?: number;
};

export type AttackingMomentumPoint = {
  index: number;
  time_sec: number;
  start_time_sec: number;
  end_time_sec: number;
  all_samples?: number;
  team_a_controlled_samples?: number;
  team_b_controlled_samples?: number;
  team_a_positional_raw?: number;
  team_b_positional_raw?: number;
  team_a_event_bonus?: number;
  team_b_event_bonus?: number;
  team_a_raw?: number;
  team_b_raw?: number;
  signed_raw?: number;
  smoothed_signed_raw?: number;
  signed_score: number;
  team_a_value: number;
  team_b_value: number;
  dominant_team_label?: 'A' | 'B' | null;
  confidence?: number;
  positional_confidence?: number;
  event_confidence?: number;
  controlled_coverage?: number;
  direction_coverage?: number;
  intensity?: number;
  evidence?: Record<string, number>;
};

export type AnalyticsWarning = {
  code: string;
  message: string;
};

export type AttackingMomentumDocument = {
  schema_version?: string;
  generated_at?: string;
  source?: string;
  status?: string;
  signal_quality?: string;
  product_readiness?: string;
  quality?: string;
  experimental?: boolean;
  semantics?: string;
  parameters?: Record<string, unknown>;
  summary?: Record<string, unknown>;
  points: AttackingMomentumPoint[];
  warnings?: Array<string | AnalyticsWarning>;
  notes?: string[];
};

export type TeamShapeDocument = {
  available?: boolean;
  scope?: 'all_in_play' | string;
  pitch_dimensions_m?: {
    width_m: number;
    length_m: number;
  };
  takeaways?: string[];
  teams?: Array<{
    team_label?: string;
    team_id?: string | null;
    team_name?: string | null;
    summary?: {
      average_width_m?: number;
      average_depth_m?: number;
      average_compactness_m?: number;
      average_block_height_percent?: number;
    };
    average_shape?: {
      grid: {
        columns: number;
        rows: number;
      };
      cells: Array<{
        column: number;
        row: number;
        value: number;
      }>;
    };
    timeline?: Array<{
      minute?: number;
      label?: string;
      width_m?: number | null;
      depth_m?: number | null;
      compactness_m?: number | null;
      block_height_percent?: number | null;
    }>;
  }>;
};

export type ContactCandidateReviewStatus = 'needs_review' | 'accepted' | 'rejected' | 'uncertain';

export type ContactCandidate = {
  candidate_id: string;
  stable_player_id?: string | null;
  stable_subject_id?: string | null;
  team_label?: string | null;
  team_id?: string | null;
  team_name?: string | null;
  start_frame?: number | null;
  end_frame?: number | null;
  start_time_sec?: number | null;
  end_time_sec?: number | null;
  duration_sec?: number | null;
  frames?: number | null;
  detected_ball_frames?: number | null;
  detected_player_frames?: number | null;
  interpolated_player_frames?: number | null;
  mean_distance_m?: number | null;
  min_distance_m?: number | null;
  mean_confidence?: number | null;
  source?: string;
  status?: ContactCandidateReviewStatus | string;
  review_status?: ContactCandidateReviewStatus;
  review_source?: string;
  review_notes?: string;
  reviewed_at?: string;
  auto_review?: {
    review_status?: ContactCandidateReviewStatus;
    source?: string;
    score?: number;
    reasons?: string[];
    thresholds?: Record<string, number>;
  };
  player_source_counts?: Record<string, number>;
};

export type ContactCandidateReviewUpdate = {
  candidate_id: string;
  review_status: ContactCandidateReviewStatus;
  notes?: string;
};

export type ContactCandidatesDocument = {
  schema_version?: string;
  generated_at?: string;
  updated_at?: string;
  source?: string;
  experimental?: boolean;
  summary?: Record<string, unknown>;
  candidates: ContactCandidate[];
};

export type EventCandidate = {
  event_id: string;
  event_type: 'ball_contact' | string;
  source_candidate_id?: string | null;
  review_status?: ContactCandidateReviewStatus;
  final_stat_eligible?: boolean;
  confidence?: number | null;
  source_confidence?: number | null;
  stable_player_id?: string | null;
  stable_subject_id?: string | null;
  team_label?: string | null;
  team_id?: string | null;
  team_name?: string | null;
  start_frame?: number | null;
  end_frame?: number | null;
  start_time_sec?: number | null;
  end_time_sec?: number | null;
  duration_sec?: number | null;
  evidence?: Record<string, unknown>;
};

export type EventCandidatesDocument = {
  schema_version?: string;
  generated_at?: string;
  source?: string;
  experimental?: boolean;
  event_semantics?: string;
  summary?: Record<string, unknown>;
  events: EventCandidate[];
};

export type EventReviewReport = {
  schema_version?: string;
  generated_at?: string;
  source?: string;
  experimental?: boolean;
  summary?: Record<string, unknown>;
  warnings?: string[];
  notes?: string[];
};

export type PassCandidatesDocument = {
  schema_version?: string;
  generated_at?: string;
  source?: string;
  experimental?: boolean;
  candidate_semantics?: string;
  parameters?: Record<string, unknown>;
  summary?: Record<string, unknown>;
  candidates: PassCandidate[];
};

export type PassCandidateReviewStatus = 'needs_review' | 'accepted' | 'uncertain' | 'rejected';

export type PassCandidate = {
  candidate_id: string;
  event_type?: string;
  pass_type?: string;
  outcome?: string;
  count_for_team_label?: string | null;
  completed?: boolean;
  failed?: boolean;
  from_restart?: boolean;
  excluded_reason?: string | null;
  source_event_id?: string | null;
  target_event_id?: string | null;
  from_stable_player_id?: string | null;
  from_team_label?: string | null;
  from_team_name?: string | null;
  to_stable_player_id?: string | null;
  to_team_label?: string | null;
  to_team_name?: string | null;
  start_frame?: number | null;
  end_frame?: number | null;
  start_time_sec?: number | null;
  end_time_sec?: number | null;
  duration_sec?: number | null;
  start_position_m?: number[] | null;
  end_position_m?: number[] | null;
  displacement_m?: number[] | null;
  distance_m?: number | null;
  match_phase_period_id?: string | null;
  attack_direction?: string | null;
  forward_progress_m?: number | null;
  direction?: string | null;
  is_progressive?: boolean;
  confidence?: number | null;
  auto_review_status?: string;
  review_status?: PassCandidateReviewStatus;
  review_source?: string;
  review_notes?: string;
  reviewed_at?: string;
  final_stat_eligible?: boolean;
  release_evidence?: Record<string, unknown>;
  receiver_evidence?: Record<string, unknown>;
  trajectory_evidence?: Record<string, unknown>;
  rejection_reasons?: string[];
};

export type PassCandidateReviewUpdate = {
  candidate_id: string;
  review_status: PassCandidateReviewStatus;
  notes?: string;
};

export type MatchPhasePeriod = {
  period_id: string;
  label?: string;
  start_time_sec: number;
  end_time_sec?: number | null;
  team_attack_directions: Record<string, string>;
  direction_source?: string;
};

export type MatchPhaseConfigDocument = {
  schema_version?: string;
  generated_at?: string;
  updated_at?: string;
  source?: string;
  coordinate_system?: string;
  direction_axis?: string;
  default_team_a_first_half_direction?: string;
  default_team_b_first_half_direction?: string;
  halves_switch_sides?: boolean;
  second_half_start_time_sec?: number | null;
  summary?: Record<string, unknown>;
  periods: MatchPhasePeriod[];
  notes?: string[];
};

export type MatchPhaseConfigPayload = {
  second_half_start_time_sec?: number | null;
  first_half_start_time_sec?: number;
  first_half_end_time_sec?: number | null;
  second_half_end_time_sec?: number | null;
  team_a_first_half_direction?: string;
};

export type PassReviewReport = {
  schema_version?: string;
  generated_at?: string;
  source?: string;
  experimental?: boolean;
  summary?: Record<string, unknown>;
  warnings?: string[];
  notes?: string[];
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

export type PlayerIdentityAssignmentStatus =
  | 'unassigned'
  | 'assigned'
  | 'unknown'
  | 'ignore'
  | 'referee'
  | 'false_positive'
  | 'wrong_target';

export type PlayerIdentityAssignment = {
  stable_subject_id: string;
  stable_player_id?: string;
  slot_id?: string | null;
  stint_id?: string | null;
  stint_ids?: string[];
  assignment_scope?: 'stable_slot' | 'stint' | string;
  status: PlayerIdentityAssignmentStatus;
  team_label?: 'A' | 'B' | 'U' | string;
  team_id?: string | null;
  team_name?: string | null;
  player_id?: string | null;
  player_name?: string | null;
  player_number?: string | null;
  player_role?: string | null;
  notes?: string;
  review_warnings?: string[];
  parent_stint_id?: string | null;
  start_frame?: number | null;
  end_frame?: number | null;
  start_time_sec?: number | null;
  end_time_sec?: number | null;
  duration_sec?: number | null;
};

export type PlayerIdentityAssignmentsDocument = {
  schema_version: string;
  updated_at: string;
  source: string;
  identity_semantics: string;
  assignment_scope: string;
  assignments: PlayerIdentityAssignment[];
  expanded_stint_assignments: Array<Record<string, unknown>>;
  summary: Record<string, unknown>;
};

export type PlayerIdentityReviewState = {
  player_identity_assignments: PlayerIdentityAssignmentsDocument;
  resolved_player_stats?: ResolvedPlayerStatsDocument | null;
  roster: {
    teams: Team[];
    summary: Record<string, unknown>;
  };
};

export type IdentityReviewCrop = {
  artifact: string;
  frame: number;
  time_sec?: number | null;
  bbox_xyxy?: number[];
  crop_bbox_xyxy?: number[];
  confidence?: number | null;
  track_id?: number | string | null;
  source?: string | null;
  appearance_change_from_previous?: number | null;
  coverage_intervals?: Array<{
    start_frame: number;
    end_frame: number;
    start_time_sec?: number | null;
    end_time_sec?: number | null;
  }>;
  coverage_frames?: number;
  representative_reason?: string | null;
  appearance_cluster_id?: string | null;
  appearance_signature?: number[];
  similarity_descriptor?: number[];
};

export type IdentityReviewGalleryStint = {
  stint_id: string;
  parent_stint_id?: string | null;
  review_segment_index?: number | null;
  review_segment_count?: number | null;
  split_reasons?: string[];
  slot_id?: string | null;
  start_frame?: number | null;
  end_frame?: number | null;
  start_time_sec?: number | null;
  end_time_sec?: number | null;
  duration_sec?: number | null;
  status?: string | null;
  detected_frames?: number | null;
  predicted_frames?: number | null;
  missing_frames?: number | null;
  ambiguous_frames?: number | null;
  tracklet_ids: string[];
  raw_track_ids: number[];
  candidate_positions: number;
  representative_clusters?: number;
  represented_intervals?: number;
  crops: IdentityReviewCrop[];
  appearance_purity?: 'consistent' | 'review' | 'mixed' | string;
  appearance_max_change?: number | null;
  appearance_change_candidates?: Array<{ frame?: number | null; score?: number | null }>;
};

export type IdentityReviewGalleryPlayer = {
  stable_subject_id: string;
  stable_player_id: string;
  slot_id?: string | null;
  team_label?: 'A' | 'B' | 'U' | string;
  team_id?: string | null;
  team_name?: string | null;
  status?: string | null;
  confidence?: string | null;
  confidence_score?: number | null;
  tracklet_ids: string[];
  raw_track_ids: number[];
  start_time_sec?: number | null;
  end_time_sec?: number | null;
  duration_sec?: number | null;
  stint_count: number;
  crop_count: number;
  stints: IdentityReviewGalleryStint[];
};

export type IdentityReviewGalleryDocument = {
  schema_version: string;
  generated_at: string;
  source: string;
  identity_semantics?: string;
  parameters: Record<string, unknown>;
  summary: {
    stable_players: number;
    stints: number;
    stints_with_crops: number;
    players_with_crops: number;
    crops: number;
    automatic_splits?: number;
    manual_splits?: number;
    mixed_segments?: number;
  };
  players: IdentityReviewGalleryPlayer[];
};

export type IdentityCropAssignmentStatus =
  | 'unassigned'
  | 'assigned'
  | 'unknown'
  | 'wrong_team'
  | 'false_positive';

export type IdentityCropReviewCrop = IdentityReviewCrop & {
  stable_subject_id: string;
  stable_player_id: string;
  slot_id?: string | null;
  team_label: 'A' | 'B' | 'U' | string;
  team_id?: string | null;
  team_name?: string | null;
  stint_id: string;
  parent_stint_id?: string | null;
  stint_start_frame?: number | null;
  stint_end_frame?: number | null;
  status: IdentityCropAssignmentStatus;
  player_id?: string | null;
  player_name?: string | null;
  updated_at?: string | null;
};

export type IdentityCropReviewUpdate = {
  artifact: string;
  status: IdentityCropAssignmentStatus;
  player_id?: string | null;
};

export type IdentityCropReviewDocument = {
  schema_version: string;
  updated_at?: string | null;
  source: string;
  summary: {
    crops_total: number;
    reviewed: number;
    remaining: number;
    by_status: Record<string, number>;
    by_player: Record<string, number>;
    derived_stints?: number;
    assigned_crops?: number;
    overlap_clipped?: number;
    covered_frames?: number;
  };
  crops: IdentityCropReviewCrop[];
  roster: Team[];
  resolved_player_stats?: ResolvedPlayerStatsDocument | null;
};

export type IdentityRosterSubjectDecision =
  | 'confirm_recommended_player'
  | 'assign_roster_player'
  | 'mark_unresolved';

export type IdentityRosterSubjectDecisionUpdate = {
  update_id?: string;
  review_card_key: string;
  decision: IdentityRosterSubjectDecision | 'clear_decision' | null;
  player_id?: string | null;
  comment?: string | null;
};

export type IdentityRosterSubjectJerseyNumberAnnotationUpdate = {
  update_id?: string;
  review_card_key: string;
  anchor_crop_id: string;
  jersey_number_annotation: IdentityRosterSubjectJerseyNumberAnnotation;
};

export type IdentityRosterSubjectNumberPanelAnnotation = {
  number_panel_source_artifact: string;
  number_panel_source_sha256?: string | null;
  coordinate_space_version: 'number_panel_source_pixels_v1';
  number_panel_bbox_normalized: [number, number, number, number];
  glyph_height_px?: number | null;
};

export type IdentityRosterSubjectNumberPanelAnnotationUpdate = {
  update_id?: string;
  review_card_key: string;
  anchor_crop_id: string;
  number_panel_annotation: IdentityRosterSubjectNumberPanelAnnotation;
};

export type IdentityRosterSubjectNumberPanelAnnotationClearUpdate = {
  update_id?: string;
  review_card_key: string;
  anchor_crop_id: string;
  clear_number_panel_annotation: true;
};

export type IdentityRosterSubjectReviewUpdate =
  | IdentityRosterSubjectDecisionUpdate
  | IdentityRosterSubjectJerseyNumberAnnotationUpdate
  | IdentityRosterSubjectNumberPanelAnnotationUpdate
  | IdentityRosterSubjectNumberPanelAnnotationClearUpdate;

export type IdentityRosterSubjectTelemetryEventType =
  | 'session_started'
  | 'card_opened'
  | 'activity'
  | 'session_completed'
  | 'remediation_action';

export type IdentityRosterSubjectTelemetryEvent = {
  event_id: string;
  session_id: string;
  event_type: IdentityRosterSubjectTelemetryEventType;
  occurred_at: string;
  active_delta_seconds?: number;
  review_card_key?: string | null;
};

export type IdentityRosterSubjectOperatorTelemetry = {
  review_session_started_at?: string | null;
  review_session_completed_at?: string | null;
  active_review_seconds: number;
  cards_opened: number;
  cards_decided: number;
  cards_reopened: number;
  decisions_changed: number;
  confirm_recommendation_count: number;
  manual_assignment_count: number;
  unresolved_count: number;
  remediation_actions_count: number;
  average_seconds_per_card?: number | null;
  cards_per_minute?: number | null;
  sessions: number;
};

export type IdentityRosterSubjectOperatorDecision = {
  review_card_key: string;
  candidate_subject_id: string;
  decision: IdentityRosterSubjectDecision;
  player_id?: string | null;
  comment?: string | null;
  updated_at: string;
};

export type IdentityRosterSubjectRecommendedPlayer = {
  player_id: string;
  player_name?: string | null;
  team_label?: string | null;
  confidence?: number | null;
  source?: string | null;
};

export type IdentityRosterSubjectSeedResolution = {
  status?: 'accepted' | 'conflict' | string;
  assigned_player?: {
    player_id?: string | null;
    name?: string | null;
    player_name?: string | null;
    team_label?: string | null;
  } | null;
  seed_observations?: string[];
  tracklet_ids?: string[];
  start_frame?: number | null;
  end_frame?: number | null;
  reason_codes?: string[];
  conflict_type?: string | null;
  conflicts?: unknown[];
};

export type IdentityRosterSubjectCandidate = {
  player_id: string;
  player_name?: string | null;
  team_label?: string | null;
  direct_coverage_ratio?: number | null;
  reid_support?: number | null;
  recommended?: boolean;
  ranked_candidate?: boolean;
};

export type IdentityRosterSubjectAnchorCrop = {
  anchor_crop_id: string;
  artifact: string;
  frame: number;
  time_sec?: number | null;
  bbox_xyxy?: number[];
  detection_confidence?: number | null;
  selection_score?: number | null;
  quality_class?: string | null;
  tracklet_id?: string | null;
  jersey_number_visual_diagnostics?: IdentityRosterSubjectJerseyNumberVisualDiagnostics | null;
  jersey_number_annotation?: IdentityRosterSubjectJerseyNumberAnnotation | null;
  number_panel_annotation?: IdentityRosterSubjectNumberPanelAnnotation | null;
};

export type IdentityRosterSubjectJerseyNumberVisualDiagnostics = {
  status?: string | null;
  reason_codes?: string[];
  observations?: string[];
};

export type IdentityRosterSubjectJerseyNumberAnnotation = {
  jersey_number_state?: 'number_confirmed' | 'number_absent' | 'number_unreadable';
  jersey_number?: string | null;
  number_panel_bbox_normalized?: number[] | null;
  number_panel_artifact?: string | null;
};

export type IdentityRosterSubjectReviewCard = {
  review_card_key: string;
  candidate_subject_id: string;
  review_status: string;
  review_unit?: string;
  team_label?: string | null;
  role?: string | null;
  start_frame?: number | null;
  end_frame?: number | null;
  detected_frames?: number | null;
  roster_status?: string | null;
  recommended_player?: IdentityRosterSubjectRecommendedPlayer | null;
  roster_candidates: IdentityRosterSubjectCandidate[];
  operator_roster_options?: IdentityRosterSubjectCandidate[];
  visual_evidence: {
    status?: string | null;
    selected_crop_count?: number;
    minimum_required?: number;
    anchor_crops: IdentityRosterSubjectAnchorCrop[];
    rejected_observations?: Record<string, number>;
  };
  quality_flags: string[];
  reason_codes: string[];
  blockers: string[];
  allowed_actions: string[];
  requires_operator_review?: boolean;
  overlapping_seeded_player_ids?: string[];
  seed_resolution?: IdentityRosterSubjectSeedResolution | null;
  operator_decision?: IdentityRosterSubjectOperatorDecision | null;
};

export type IdentityRosterSubjectReviewDocument = {
  schema_version: string;
  mode: 'shadow_operator_review';
  source_artifact_digest: string;
  decisions_fresh: boolean;
  summary: Record<string, unknown> & {
    reviewed_cards: number;
    pending_cards: number;
    stale_decisions: number;
  };
  safety: {
    writes_shadow_decisions_only: boolean;
    writes_player_identity_assignments: boolean;
    mutates_production_identity: boolean;
    eligible_for_player_stats: boolean;
    eligible_for_heatmaps: boolean;
  };
  operator_telemetry?: IdentityRosterSubjectOperatorTelemetry;
  initial_audit_integration?: {
    status?: string;
    reason_codes?: string[];
    metrics?: {
      review_cards_before_seeding?: number;
      review_cards_after_seeding?: number;
      review_cards_reduced?: number;
      subjects_resolved_after_seeding?: number;
      tracklets_resolved_after_seeding?: number;
      frames_resolved_after_seeding?: number;
      manual_decisions_before_seeding?: number;
      manual_decisions_after_seeding?: number;
      conflicts_created?: number;
      false_assignments_found?: number;
      blocked_overlapping_player_options?: number;
    };
  };
  cards: IdentityRosterSubjectReviewCard[];
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

export type InitialIdentityAuditTeamLabel = 'A' | 'B' | 'U';

export type InitialIdentityAuditRosterPlayer = {
  player_id: string;
  player_name: string;
  player_number?: string | null;
  player_role: string;
};

export type InitialIdentityAuditRosterTeam = {
  team_label: InitialIdentityAuditTeamLabel;
  team_id?: string | null;
  team_name: string;
  players: InitialIdentityAuditRosterPlayer[];
};

export type InitialIdentityAuditObservation = {
  observation_key: string;
  bbox_xyxy: [number, number, number, number];
  team_label: InitialIdentityAuditTeamLabel;
  role: string;
  provenance: Record<string, unknown>;
  display_order: number;
  suggested_player?: {
    player_id: string;
    player_name: string;
    team_label: InitialIdentityAuditTeamLabel;
    rank?: number;
    advisory_only?: boolean;
    suggestion_source?: string;
    candidate_subject_id?: string | null;
    observation_key?: string | null;
  } | null;
  reid_suggestions?: Array<{
    player_id: string;
    player_name: string;
    rank: number;
    distance?: number | null;
    suggestion_source: 'cross_analysis_reid_top3_advisory';
    advisory_only: true;
    candidate_subject_id?: string | null;
    observation_key: string;
  }>;
  reid_suggestion_notice?: {
    status: 'hidden_low_quality';
    reason_codes: string[];
  } | null;
};

export type InitialIdentityAuditFrame = {
  audit_frame_key: string;
  frame_number: number;
  time_sec: number;
  full_frame_artifact: string;
  thumbnail_artifact: string;
  observations: InitialIdentityAuditObservation[];
};

export type InitialIdentityAuditDocument = {
  schema_version: string;
  mode: string;
  read_only: boolean;
  selection_digest?: string | null;
  video: VideoMetadata;
  summary: {
    selected_frames: number;
    visible_observations: number;
    maximum_frames: number;
    target_actions: string;
  };
  roster: InitialIdentityAuditRosterTeam[];
  frames: InitialIdentityAuditFrame[];
  actions: string[];
  operator_contract: {
    certainty: string;
    finish_before_full_coverage: boolean;
    raw_coordinates_required: boolean;
    technical_ids_visible: boolean;
    decisions_persisted: boolean;
  };
  safety: {
    production_identity_untouched: boolean;
    candidate_identity_untouched: boolean;
    yolo_not_required: boolean;
    downstream_rebuild_triggered: boolean;
  };
};

export type SecondHalfIdentityReanchorDocument = {
  schema_version: string;
  mode: string;
  status: 'ready' | 'not_applicable' | 'skipped_already_resolved';
  reason?: string | null;
  read_only?: boolean;
  selection_digest?: string | null;
  video?: VideoMetadata;
  second_half: {
    start_time_sec?: number | null;
    start_frame?: number | null;
  };
  safely_resolved_players: Array<{
    player_id: string;
    player_name?: string | null;
    team_label?: InitialIdentityAuditTeamLabel | null;
    candidate_subject_id?: string | null;
    tracklet_ids?: string[];
    trusted_second_half_frames?: number;
  }>;
  summary: {
    selected_frames: number;
    visible_observations: number;
    maximum_frames: number;
    target_actions: string;
    confirmation_first: boolean;
  };
  roster: InitialIdentityAuditRosterTeam[];
  frames: InitialIdentityAuditFrame[];
  actions: string[];
  operator_contract: {
    confirmation_first: boolean;
    second_full_lineup_audit: boolean;
    finish_before_full_coverage: boolean;
    [key: string]: unknown;
  };
  safety: {
    production_identity_untouched: boolean;
    candidate_identity_untouched: boolean;
    yolo_not_required: boolean;
    [key: string]: boolean;
  };
};

export type InitialIdentityAuditSeedAction =
  | 'assign_roster_player'
  | 'team_a_unknown'
  | 'team_b_unknown'
  | 'referee'
  | 'false_detection'
  | 'skip';

export type InitialIdentityAuditStoredDecision = {
  observation_key: string;
  action: InitialIdentityAuditSeedAction;
  assigned_team?: {
    team_label: InitialIdentityAuditTeamLabel;
    team_id?: string | null;
    team_name: string;
  } | null;
  assigned_player?: {
    player_id: string;
    player_name: string;
    player_number?: string | null;
    player_role: string;
  } | null;
  team_assignment_corrected: boolean;
  suggestion_context?: {
    suggestion_source: string;
    advisory_only: boolean;
    rank?: number | null;
    candidate_subject_id?: string | null;
    observation_key: string;
    player_id?: string | null;
  } | null;
  updated_at?: string | null;
};

export type InitialIdentityAuditTelemetryMetrics = {
  audit_frames_shown: number;
  audit_crops_clicked: number;
  audit_actions: number;
  active_operator_seconds: number;
  unique_players_seeded: number;
  team_assignments_corrected: number;
  false_detections_marked: number;
};

export type InitialIdentityAuditSeedStoreDocument = {
  schema_version: string;
  mode: string;
  status: 'empty' | 'fresh' | 'stale';
  decisions_fresh: boolean;
  source_selection_digest?: string | null;
  decisions: InitialIdentityAuditStoredDecision[];
  operator_telemetry: {
    metrics: InitialIdentityAuditTelemetryMetrics;
  };
  safety: Record<string, boolean>;
  operator_budget?: {
    limit: number;
    active_decisions: number;
    remaining: number;
    reached: boolean;
  };
  benchmark_state?: string;
  updated_at?: string | null;
  workflow?: ReviewWorkflow;
};

export type InitialIdentityAuditSeedUpdate = {
  update_id: string;
  observation_key: string;
  action: InitialIdentityAuditSeedAction | 'clear';
  player_id?: string;
  suggestion_context?: {
    suggestion_source: string;
    advisory_only: boolean;
    rank?: number | null;
    candidate_subject_id?: string | null;
    observation_key: string;
    player_id?: string | null;
  } | null;
};

export type InitialIdentityAuditTelemetryEvent = {
  event_id: string;
  session_id: string;
  event_type:
    | 'session_started'
    | 'frame_shown'
    | 'crop_clicked'
    | 'action'
    | 'session_finished';
  audit_frame_key?: string;
  observation_key?: string;
  active_delta_seconds?: number;
  occurred_at?: string;
};

export type Match = MatchMetadataPayload & {
  id: string;
  video_filename: string;
  video: VideoMetadata;
  created_at?: string;
  updated_at?: string;
  published_match_id?: string;
  analysis_runs?: AnalysisRunSummary[];
  latest_analysis_run_id?: string;
  latest_analysis_job_id?: string;
  analysis_job_status?: string;
  pitch_config?: unknown;
  analysis_report?: AnalysisReport;
  performance_report?: PerformanceReport;
  camera_motion_report?: Record<string, unknown>;
  analysis_chunk_manifest?: Record<string, unknown>;
  stable_players?: StablePlayersDocument;
  stabilization_report?: Record<string, unknown>;
  global_identity_report?: GlobalIdentityReport;
  analysis_quality_report?: AnalysisQualityReport;
  change_candidates?: ChangeCandidatesDocument;
  change_review_report?: ChangeReviewReport;
  team_clusters?: TeamClustersDocument;
  team_config?: TeamConfigDocument;
  team_stats?: TeamStatsDocument;
  frame_detection_counts?: FrameDetectionCountsDocument;
  movement_stats?: MovementStatsDocument;
  player_stats?: PlayerStatsDocument;
  resolved_player_stats?: ResolvedPlayerStatsDocument;
  player_heatmaps?: PlayerHeatmapsDocument | null;
  tracklets?: Record<string, unknown>;
  tracking_quality_report?: TrackingQualityReport;
  ball_analysis_report?: Record<string, unknown>;
  ball_tracking_report?: BallTrackingReport;
  ball_quality_report?: BallQualityReport;
  contact_candidates?: ContactCandidatesDocument;
  match_phase_config?: MatchPhaseConfigDocument;
  event_candidates?: EventCandidatesDocument;
  event_review_report?: EventReviewReport;
  pass_candidates?: PassCandidatesDocument;
  pass_review_report?: PassReviewReport;
  attacking_momentum?: AttackingMomentumDocument;
  possession_report?: PossessionReport;
  match_package?: MatchPackage;
  player_assignments?: PlayerAssignmentsDocument;
  player_identity_assignments?: PlayerIdentityAssignmentsDocument;
};

export type ReviewedIdentitySummary = {
  tracklets_total: number;
  confirmed: number;
  probable: number;
  unresolved: number;
  conflicted: number;
  blocked: number;
  confirmed_players: number;
  confirmed_detected_observation_ratio: number | null;
};

export type ReviewedIdentityDocument = {
  status: 'missing' | 'stale' | 'blocked' | 'partial_reviewed' | 'complete_reviewed';
  semantic_digest?: string;
  summary: ReviewedIdentitySummary | null;
  readiness?: { identity?: string; reason?: string };
  workflow?: ReviewWorkflow;
};

export type ReviewedOutputJob = {
  status: 'missing' | 'queued' | 'running' | 'completed' | 'failed' | 'stale';
  job_key?: string;
  created_at?: string;
  started_at?: string;
  completed_at?: string;
  video_digest?: string;
  source_snapshot_digest?: string;
  error?: { message?: string } | null;
};

export type ReviewWorkflowStepId = 'initial_audit' | 'exceptions' | 'mixed_players' | 'finalize' | 'video_qa';
export type ReviewWorkflowStepStatus = 'locked' | 'current' | 'processing' | 'completed' | 'error';
export type ReviewWorkflowAction =
  | 'identify_players'
  | 'review_identity_issue'
  | 'review_mixed_players'
  | 'finalize_identity'
  | 'wait_for_render'
  | 'review_video'
  | 'approve_video_qa'
  | 'correct_video_identity'
  | 'retry_render'
  | 'retry_review_recompute';

export type ReviewWorkflowStep = {
  id: ReviewWorkflowStepId;
  status: ReviewWorkflowStepStatus;
  completed: number | null;
  total: number | null;
  remaining: number | null;
  locked_reason_code: string | null;
  locked_reason_details?: Record<string, unknown>;
};

export type ReviewWorkflow = {
  schema_version: '1.0.0' | string;
  match_id: string;
  available: boolean;
  phase: string;
  status: 'unavailable' | 'action_required' | 'processing' | 'ready' | 'complete' | 'error';
  current_step_id: ReviewWorkflowStepId;
  review_complete: boolean;
  can_enter_report: boolean;
  can_publish: boolean;
  steps: ReviewWorkflowStep[];
  required_action: { type: ReviewWorkflowAction | string; step_id: string; remaining?: number | null } | null;
  initial_audit?: {
    prepared?: boolean;
    selected_frames?: number;
    visible_observations?: number;
    completed?: number;
    total?: number;
    remaining?: number;
    safe_to_stop?: boolean;
    complete?: boolean;
    completion_evidence_current?: boolean;
    completion_evidence_reason?: string;
    required_case_observation_keys?: string[];
  };
  issues: { blocking: number; actionable_blocking?: number; overall_identity_blocked?: boolean; coverage_readiness_blocked?: boolean; normal_blocking?: number; mixed_blocking?: number; mixed_total?: number; mixed_resolved?: number; important: number; semantic?: number; coverage?: number; optional: number; optional_audit?: number; coverage_readiness?: ReviewedIdentityCoverageReadiness | null; identity_coverage?: ReviewedIdentityCoverage | null; workload?: ReviewedIdentityWorkload | null; optional_audit_summary?: ReviewedIdentityOptionalAudit | null };
  freshness: {
    reviewed_identity_current: boolean;
    reviewed_stats_current: boolean;
    reviewed_output_current: boolean;
    qa_approval_current: boolean;
    review_progress_current?: boolean;
    review_progress_reason?: string | null;
  };
  processing?: ReviewedOutputJob | null;
  blockers: Array<{ code: string; step_id: string; user_actionable: boolean; details: Record<string, unknown> }>;
  allowed_actions: ReviewWorkflowAction[];
};

export type ReviewedPlayerStats = {
  player_id: string;
  player_name: string;
  team_label: string;
  detected_time_sec: number;
  observed_distance_m: number;
  heatmap_samples: number;
  confirmed_detected_observations: number;
  confirmed_fragments: number;
  readiness?: Record<string, string>;
};

export type ReviewedStatsResponse = {
  stats: {
    source_snapshot_digest: string;
    players: ReviewedPlayerStats[];
    global_coverage: {
      coverage_unit?: string;
      confirmed_ratio?: number | null;
    };
  };
  readiness: { status: string };
};

export type ReviewedIdentityAt = {
  time_sec: number;
  frame: number;
  reference_snapshot_stale?: boolean;
  entities: ReviewedIdentityAtEntity[];
};

export type ReviewedIdentityAtEntity = {
  frame: number;
  time_sec: number;
  tracklet_id: string;
  candidate_subject_id: string | null;
  candidate_subject_ids: string[];
  team_label: string;
  stable_anonymous_slot_id: string | null;
  canonical_player_id: string | null;
  player_name: string | null;
  display_label: string;
  identity_status: string;
  identity_source: string | null;
  fallback_label: string;
  requires_review: boolean;
  hard_blockers: string[];
  conflicts: Array<Record<string, unknown>>;
  detected_evidence_count: number;
  frame_start: number;
  frame_end: number;
  observation_key?: string;
  bbox_xyxy?: number[] | null;
  review_target_id?: string | null;
  scope_kind?: 'whole_subject' | 'canonical_segment' | 'material_continuity';
  correction_scope?: 'whole_subject' | 'canonical_segment' | 'material_continuity';
  operator_actionable?: boolean;
  non_actionable_reason?: string | null;
  has_operator_visual_evidence?: boolean;
  source_ownership_digest?: string | null;
};

export type ReviewedCorrectionAction =
  | 'assign_roster_player'
  | 'assign_existing_slot'
  | 'assign_team'
  | 'create_new_stable_player'
  | 'referee'
  | 'false_detection'
  | 'team_unknown'
  | 'unresolved'
  | 'mixed_players';

export type ReviewedCorrectionPrimaryAction = ReviewedCorrectionAction | 'split';

export type ReviewedCorrectionActionCapability = {
  allowed: boolean;
  reason?: string;
  mode?: 'temporal' | 'staged_temporal_review';
  minimum_observations?: number;
  requires_player_id?: boolean;
  requires_team_label?: boolean;
  requires_slot_id?: boolean;
};

export type ReviewedCorrectionRosterOption = {
  player_id: string;
  player_name: string;
  roster_number?: string | null;
  team_label: string;
};

export type ReviewedCorrectionSlotOption = {
  stable_slot_id: string;
  team_label: string;
  source: string;
  status: string;
};

export type ReviewedCorrectionContext = {
  candidate_subject_id: string;
  review_target_id?: string | null;
  scope_kind?: 'whole_subject' | 'canonical_segment' | 'material_continuity';
  team_label: string;
  source_team_label: string;
  effective_team_label: string;
  available_team_labels: string[];
  tracklet_ids: string[];
  review_card_key: string | null;
  roster_options: ReviewedCorrectionRosterOption[];
  slot_options: ReviewedCorrectionSlotOption[];
  current_decision: Record<string, unknown> | null;
  semantic_decision_digest: string;
  source_ownership_digest?: string | null;
  frame_ranges?: number[][];
  frame_start?: number | null;
  frame_end?: number | null;
  detected_observation_count?: number | null;
  continuity_group_id?: string | null;
  continuity_subject_ids?: string[];
  visual_evidence?: {
    kind?: 'team_attribution' | 'identity_continuity';
    status?: string;
    selected_crop_count?: number;
    anchor_crops: IdentityRosterSubjectAnchorCrop[];
    boundary_crops?: Array<IdentityRosterSubjectAnchorCrop & { outside_target?: boolean }>;
  } | null;
  source_evidence_kind?: string;
  legacy_suggestion?: {
    action: 'assign_roster_player';
    player_id: string;
    player_name: string;
    roster_number?: string | null;
    team_label: string;
    requires_confirmation: boolean;
  } | null;
  temporal_split?: {
    resolution_status?: string;
    split_after_frames: number[];
    split_semantic_digest?: string | null;
    segment_assignments: MixedSegmentAssignment[];
  } | null;
  action_capabilities: Partial<Record<ReviewedCorrectionPrimaryAction, ReviewedCorrectionActionCapability>>;
  scope_copy?: string;
  review_state_version?: number;
};

export type ReviewedCorrectionRequest = {
  candidate_subject_id: string;
  review_target_id?: string;
  continuity_group_id?: string;
  source_ownership_digest?: string;
  action: ReviewedCorrectionAction;
  player_id?: string;
  stable_slot_id?: string;
  team_label?: string;
  comment?: string;
  mixed_hint?: 'cross_team' | 'same_team_a' | 'same_team_b' | 'player_referee' | 'unknown';
  defer_recompute?: boolean;
  review_state_version?: number;
};

export type ReviewedCorrectionResponse = {
  saved_decision: Record<string, unknown> | null;
  effective_action: ReviewedCorrectionAction;
  allocated_stable_slot_id: string | null;
  snapshot?: { status: string; stale: boolean };
  semantic_decision_digest: string;
  recompute_deferred: boolean;
  review_state_version?: number;
  review_state_rebuild_required?: boolean;
  persistence?: {
    status: 'saved';
    downstream_recompute_triggered: boolean;
  };
  performance?: Record<string, number>;
  review_progress?: ReviewedIdentityReviewProgress;
  coverage_debt?: ReviewedIdentityCoverageDebt;
  decision_impact?: ReviewedCorrectionDecisionImpact;
  workflow?: ReviewWorkflow;
  reviewed_identity?: ReviewedFinalizedIdentitySummary;
  render_job?: ReviewedOutputJob;
};

export type ReviewedIdentityReviewUnit = {
  candidate_subject_id: string;
  tracklet_ids: string[];
  tracklet_count: number;
  source_team_label: string;
  effective_team_label: string;
  frame_start: number | null;
  frame_end: number | null;
  detected_frame_count: number;
  detected_observation_count: number;
  detected_time_sec: number;
  current_resolution_status: string;
  priority: 'high' | 'coverage' | 'continuity' | 'optional' | null;
  reason_codes: string[];
  review_target_id?: string | null;
  scope_kind?: 'whole_subject' | 'canonical_segment' | 'material_continuity';
  source_ownership_digest?: string | null;
  stable_slot_id?: string | null;
  frame_ranges?: number[][];
  visual_evidence?: {
    kind?: 'team_attribution' | 'identity_continuity';
    status?: string;
    selected_crop_count?: number;
    anchor_crops: IdentityRosterSubjectAnchorCrop[];
    boundary_crops?: Array<IdentityRosterSubjectAnchorCrop & { outside_target?: boolean }>;
  } | null;
  legacy_suggestion?: ReviewedCorrectionContext['legacy_suggestion'];
  continuity_group_id?: string | null;
  continuity_subject_ids?: string[];
  continuity_fragment_count?: number | null;
  continuity_span_sec?: number | null;
  material_continuity_required?: boolean;
  coverage_team_label?: string | null;
  potential_named_observation_gain?: number | null;
  marginal_named_observation_gain?: number | null;
  potential_team_unnamed_share?: number | null;
  potential_named_coverage_gain_pp?: number | null;
  optional_max_rank?: number | null;
  optional_max_marginal_coverage_gain_pp?: number | null;
  named_coverage_before?: number | null;
  named_coverage_after_max?: number | null;
  filter_team_label?: 'A' | 'B' | 'U';
};

export type ReviewedIdentityTeamFilterLabel = 'A' | 'B';
export type ReviewedIdentityReviewQueue = 'required' | 'optional_audit';

export type ReviewedIdentityReviewFilters = {
  queue?: ReviewedIdentityReviewQueue;
  active_team_label: ReviewedIdentityTeamFilterLabel | null;
  counts: {
    all: number;
    A: number;
    B: number;
    U: number;
  };
};

export type ReviewedIdentityCoverageRow = {
  scope?: IdentityReviewTeamScope;
  roster_scope?: IdentityReviewTeamScope;
  reliable_observations: number;
  confirmed_named_observations: number;
  named_observation_coverage: number | null;
  team_known_observations: number;
  team_known_observation_coverage: number | null;
  unresolved_observations: number;
  conflicted_observations: number;
  ignored_observations: number;
  unresolved_observation_share: number | null;
  named_player_review_required?: boolean;
  team_stats_required?: boolean;
  named_coverage_status?: 'required' | 'not_required_by_scope';
};

export type ReviewedIdentityCoverage = ReviewedIdentityCoverageRow & {
  schema_version: string;
  policy_version: string;
  coverage_unit: string;
  per_team: Record<string, ReviewedIdentityCoverageRow>;
};

export type ReviewedIdentityCoverageReadiness = {
  status: 'not_assessable' | 'incomplete' | 'ready' | 'ready_with_review';
  policy_version: string;
  allows_finalize: boolean;
  blockers: Array<Record<string, unknown>>;
  roster_scope: Record<string, string>;
};

export type ReviewedIdentityWorkload = {
  remaining_cases: number;
  level: 'normal' | 'elevated' | 'excessive' | 'critical';
  diagnostic_only: boolean;
  queue_truncated: boolean;
};

export type ReviewedIdentityOptionalAudit = {
  policy_version: string;
  queue: 'optional_audit';
  team_label: 'A';
  scope: IdentityReviewTeamScope;
  blocking: false;
  status: 'not_ready' | 'available' | 'safe_max_reached';
  eligible_to_start: boolean;
  minimum_target_ratio: number;
  minimum_target_met: boolean;
  current_minimum_target_met: boolean;
  projected_minimum_target_met: boolean;
  required_readiness_met: boolean;
  remaining_cases: number;
  actionable_cases_remaining: number;
  current_named_observations: number;
  reliable_observations: number;
  current_named_coverage: number | null;
  pending_named_gain: number;
  projected_named_observations: number;
  projected_named_coverage: number | null;
  safe_max_named_observations: number;
  safe_max_named_coverage: number | null;
  remaining_actionable_named_gain: number;
  actionable_unique_observations_remaining: number;
  unavailable_residual_observations: number;
  unavailable_residual_ratio: number | null;
  unavailable_actionable_observations: number;
  unavailable_reason_counts: Record<string, number>;
  per_team: Record<string, number>;
};

export type ReviewedIdentityCoverageDebtBucket = {
  case_count: number;
  unique_observations: number;
  share_of_reliable: number | null;
  coverage_pp: number;
  reason_counts?: Record<string, number>;
  breakdown?: Record<'semantic' | 'continuity' | 'coverage', {
    case_count: number;
    unique_observations: number;
    coverage_pp: number;
  }>;
};

export type ReviewedIdentityCoverageDebtTeam = {
  scope: IdentityReviewTeamScope;
  reliable_observations: number;
  current_named_observations: number;
  current_named_coverage: number | null;
  target_named_coverage: number | null;
  target_named_observations: number | null;
  target_gap_observations: number | null;
  target_gap_pp: number | null;
  projected_named_coverage_after_committed: number | null;
  unnamed_observations: number;
  operator_identity_debt_observations: number;
  not_required_by_scope: {
    unique_observations: number;
    share_of_reliable: number | null;
    coverage_pp: number;
  };
  accounted_unnamed_observations: number;
  unaccounted_unnamed_observations: number;
  buckets: Record<'committed_pending' | 'required' | 'mixed' | 'optional_max' | 'unavailable', ReviewedIdentityCoverageDebtBucket>;
};

export type ReviewedIdentityCoverageDebt = {
  policy_version: string;
  coverage_unit: string;
  accounting_precedence: string[];
  per_team: Record<string, ReviewedIdentityCoverageDebtTeam>;
  ambiguous: {
    mixed_case_count: number;
    raw_marker_observations: number;
    note: string;
  };
};

export type ReviewedIdentityMixedPlayersSummary = {
  schema_version: string;
  mode: string;
  match_id: string;
  summary: { total: number; unresolved: number; resolved: number; complex_unresolved: number };
  cases: Array<{
    case_id?: string;
    candidate_subject_id: string;
    original_issue: 'mixed_players';
    mixed_hint: MixedPlayerHint;
    resolution_status: 'unresolved' | 'resolved' | 'unresolved_complex_mix';
    observation_count: number;
    updated_at?: string | null;
    has_exact_source: boolean;
  }>;
};

export type ReviewedIdentityReviewProgress = {
  schema_version: string;
  status: 'ready';
  match_id: string;
  queue: ReviewedIdentityReviewQueue;
  identity_review_scope?: {
    schema_version: string;
    explicit: boolean;
    teams: Record<string, {
      scope: IdentityReviewTeamScope;
      named_player_review_required: boolean;
      team_stats_required: boolean;
      player_stats_status: 'reviewed' | 'not_reviewed_by_scope';
    }>;
  };
  summary: {
    review_units_total: number;
    review_units_completed: number;
    review_units_actionable_total: number;
    completed_by_operator: number;
    completed_automatically: number;
    important_decisions_remaining: number;
    semantic_decisions_remaining: number;
    coverage_decisions_remaining: number;
    optional_cases_remaining: number;
    optional_audit_cases_remaining?: number;
    structural_blockers: number;
    non_actionable_review_units?: number;
    non_actionable_reason_counts?: Record<string, number>;
    ignored_low_impact: number;
    operator_decisions_saved: number;
    operator_queue_completion_ratio: number;
  };
  identity_coverage: ReviewedIdentityCoverage;
  coverage_readiness: ReviewedIdentityCoverageReadiness;
  coverage_residuals: Record<string, Record<string, unknown>>;
  coverage_debt: ReviewedIdentityCoverageDebt;
  workload: ReviewedIdentityWorkload;
  optional_audit: ReviewedIdentityOptionalAudit;
  pagination: {
    offset: number;
    limit: number;
    returned: number;
    total_remaining: number;
    global_total_remaining: number;
    has_more: boolean;
  };
  filters: ReviewedIdentityReviewFilters;
  observations: {
    total_detected_observations: number;
    operator_reviewed_observations: number;
    operator_reviewed_observation_ratio: number;
    team_known_observations: number;
    team_known_observation_ratio: number;
    confirmed_player_observations: number;
    confirmed_player_observation_ratio: number;
  };
  next_cases: ReviewedIdentityReviewUnit[];
  mixed_players: ReviewedIdentityMixedPlayersSummary;
  server_timing?: {
    review_hot_state_ms: number;
    review_queue_page_ms: number;
    total_ms: number;
    review_hot_state_source?: 'warm_hit' | 'cold_rebuild';
  };
  technical_diagnostics: {
    candidate_subjects: number;
    tracklets: number;
    unresolved_tracklet_assignments: number;
  };
  policy: Record<string, number>;
  recompute_required?: boolean;
};

export type MixedPlayerHint = 'cross_team' | 'same_team_a' | 'same_team_b' | 'player_referee' | 'unknown';

export type MixedPlayerCase = {
  case_id?: string;
  candidate_subject_id: string;
  original_issue: 'mixed_players';
  mixed_hint: MixedPlayerHint;
  resolution_status: 'unresolved' | 'resolved' | 'unresolved_complex_mix';
  source_subject_digest: string;
  source_tracklet_ids: string[];
  observation_count: number;
  frame_start: number;
  frame_end: number;
  reviewed_complex?: boolean;
  reviewed_complex_at?: string | null;
  temporal_evidence: { status: string; anchor_crops: Array<IdentityRosterSubjectAnchorCrop & { team_label?: string }> };
};

export type MixedPlayersReviewQueue = {
  schema_version: string;
  mode: string;
  match_id: string;
  summary: { total: number; unresolved: number; resolved: number; complex_unresolved: number };
  assignment_options: { roster: ReviewedCorrectionRosterOption[]; slots: ReviewedCorrectionSlotOption[] };
  cases: MixedPlayerCase[];
};

export type MixedBoundaryRefinement = {
  schema_version: string;
  mode: string;
  match_id: string;
  candidate_subject_id: string;
  case_id?: string;
  source_subject_digest: string;
  after_frame: number;
  before_frame: number;
  anchor_crops: Array<IdentityRosterSubjectAnchorCrop & { team_label?: string }>;
};

export type MixedSegmentAssignment = {
  action: Exclude<ReviewedCorrectionAction, 'mixed_players'>;
  player_id?: string;
  stable_slot_id?: string;
  team_label?: string;
};

export type ReviewedTemporalSplitRequest = {
  candidate_subject_id: string;
  review_target_id?: string;
  continuity_group_id?: string;
  source_ownership_digest: string;
  existing_split_semantic_digest?: string;
  resolution: 'split' | 'unresolved_complex_mix';
  split_after_frames?: number[];
  segment_assignments?: MixedSegmentAssignment[];
  comment?: string;
  review_state_version?: number;
};

export type ReviewedTemporalSplitRefinement = MixedBoundaryRefinement & {
  review_target_id?: string | null;
  continuity_group_id?: string | null;
};

export type ReviewedTemporalSplitResponse = {
  saved_case: Record<string, unknown>;
  semantic_decision_digest: string;
  recompute_deferred: true;
  idempotent?: boolean;
  complex_mix?: boolean;
};

export type MixedPlayerResolutionRequest = {
  candidate_subject_id: string;
  case_id?: string;
  source_subject_digest: string;
  resolution: 'split' | 'unresolved_complex_mix';
  split_after_frames?: number[];
  segment_assignments?: MixedSegmentAssignment[];
  comment?: string;
};

export type MixedPlayerResolutionResponse = {
  saved_case: MixedPlayerCase;
  semantic_decision_digest: string;
  recompute_deferred: true;
};

export type ReviewedFinalizedIdentitySummary = {
  status: ReviewedIdentityDocument['status'];
  semantic_digest: string | null;
  summary: ReviewedIdentityDocument['summary'];
  identity_coverage: Record<string, unknown> | null;
  coverage_readiness: ReviewedIdentityCoverageReadiness | null;
  source: Record<string, unknown> | null;
  entities_total: number;
};

export type ReviewedCorrectionFinalizeResponse = {
  workflow: ReviewWorkflow;
  reviewed_identity: ReviewedFinalizedIdentitySummary;
  review_progress: ReviewedIdentityReviewProgress;
  recompute_deferred: false;
  performance?: Record<string, number>;
};

export type ReviewedCorrectionDecisionImpact = {
  affected_tracklets: number;
  affected_detected_observations: number;
  important_decisions_remaining_before: number;
  important_decisions_remaining_after: number;
  operator_reviewed_observations_delta: number;
  operator_reviewed_ratio_before: number;
  operator_reviewed_ratio_after: number;
};

export type AnalysisPayload = {
  adapter: 'yolo' | 'motion';
  max_seconds: number;
  frame_stride: number;
  chunked: boolean;
  chunk_duration_sec: number;
  chunk_overlap_sec: number;
  include_ball: boolean;
  render_stable_overlay: boolean;
  yolo_model: string;
  yolo_conf: number;
  yolo_imgsz: number;
  yolo_tracker: string;
  yolo_device: string | null;
  ball_yolo_model: string;
  ball_yolo_conf: number;
  ball_yolo_imgsz: number;
  ball_yolo_device: string | null;
  camera_motion_compensation: boolean;
  camera_motion_interval_sec: number;
  camera_motion_min_inlier_ratio: number;
};

export type RuntimeInfo = {
  schema_version: string;
  python: {
    version: string;
    executable: string;
  };
  platform: {
    system: string;
    release: string;
    machine: string;
    processor: string;
    platform: string;
  };
  torch: {
    available: boolean;
    version?: string | null;
    import_error?: string;
    cuda_available: boolean;
    cuda_device_count: number;
    cuda_device_names: string[];
    cuda_version?: string | null;
    cudnn_version?: number | null;
    active_cuda_device?: number | null;
    active_cuda_device_name?: string | null;
    gpu_memory_total_mb?: number[];
    gpu_memory_allocated_mb?: number[];
    gpu_memory_reserved_mb?: number[];
    mps_available: boolean;
    mps_built: boolean;
  };
  recommended_yolo_devices: string[];
};

export type PerformanceReport = {
  schema_version?: string;
  label?: string;
  runtime?: RuntimeInfo;
  requested_device?: string;
  normalized_yolo_device?: string;
  cuda_available?: boolean;
  cuda_device_names?: string[];
  torch_cuda_version?: string | null;
  active_cuda_device?: number | null;
  gpu_memory_total_mb?: number[];
  elapsed_wall_sec?: number;
  throughput?: {
    processed_frames?: number;
    processed_frames_per_wall_sec?: number;
    analyzed_video_sec?: number;
    video_seconds_per_wall_second?: number;
    estimated_40_min_wall_min?: number | null;
  };
  analysis_summary?: Record<string, unknown>;
  parameters?: Record<string, unknown>;
  artifacts?: Record<string, unknown>;
};

export type AnalysisJob = {
  schema_version: string;
  job_id: string;
  match_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | string;
  stage: string;
  progress_percent: number;
  message: string;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  payload?: Record<string, unknown>;
  chunk_count?: number;
  chunk_manifest?: string;
  result?: {
    status?: string;
    analysis_type?: string;
    run_id?: string;
    generated_at?: string;
    frames_processed?: number;
    tracks_count?: number;
    stable_players_count?: number;
    run_directory?: string;
    run_manifest?: string;
    artifacts?: Record<string, string>;
  } | null;
  error?: {
    type?: string;
    message?: string;
    traceback?: string;
  } | null;
  progress_plan?: {
    schema_version?: string;
    active_step_id?: string;
    last_heartbeat_at?: string;
    last_artifact_at?: string | null;
    active_step_elapsed_sec?: number;
    current?: {
      current?: number;
      total?: number;
      unit?: string;
      label?: string;
    } | null;
    steps?: {
      id: string;
      label: string;
      status: 'pending' | 'running' | 'completed' | 'failed' | string;
      progress_start?: number;
      progress_end?: number;
      started_at?: string | null;
      finished_at?: string | null;
      message?: string | null;
    }[];
  };
};

export type AnalysisJobsDocument = {
  schema_version: string;
  match_id: string;
  jobs: AnalysisJob[];
  latest_job?: AnalysisJob | null;
};

export type BallAnalysisPayload = {
  max_seconds: number;
  frame_stride: number;
  yolo_model: string;
  yolo_conf: number;
  yolo_imgsz: number;
  yolo_device: string | null;
};

export type AnalysisReport = {
  status: 'completed' | 'failed' | string;
  analysis_type: string;
  run_id?: string;
  generated_at?: string;
  run_directory?: string;
  run_manifest?: string;
  performance_report?: PerformanceReport;
  parameters?: Record<string, unknown>;
  note?: string;
  frames_processed?: number;
  detections_kept?: number;
  detections_rejected_outside_pitch?: number;
  tracks_count?: number;
  ball_tracking_summary?: Record<string, unknown>;
  ball_quality_summary?: Record<string, unknown>;
  ball_quality_recommendation?: BallQualityReport['recommendation'];
  possession_summary?: Record<string, unknown>;
  attacking_momentum_summary?: Record<string, unknown>;
  warnings?: string[];
  error?: {
    type?: string;
    message?: string;
  };
  artifacts?: {
    tracks_json: string;
    overlay_preview?: string;
    heatmap_all_tracks: string;
    performance_report?: string;
    camera_motion_report?: string;
    camera_motion_overlay?: string;
    analysis_chunk_manifest?: string;
    stable_players?: string;
    global_identity?: string;
    global_identity_report?: string;
    analysis_quality_report?: string;
    stabilization_report?: string;
    change_candidates?: string;
    change_review_report?: string;
    stable_overlay_preview?: string;
    debug_identity_overlay?: string;
    team_clusters?: string;
    team_config?: string;
    team_stats?: string;
    frame_detection_counts?: string;
    movement_stats?: string;
    player_stats?: string;
    resolved_player_stats?: string;
    resolved_stats_quality_report?: string;
    player_heatmaps?: string;
    tracklets?: string;
    tracking_quality_report?: string;
    ball_candidates?: string;
    ball_tracks?: string;
    ball_analysis_report?: string;
    ball_tracking_report?: string;
    ball_quality_report?: string;
    ball_overlay_preview?: string;
    possession_candidates?: string;
    possession_segments?: string;
    contact_candidates?: string;
    match_phase_config?: string;
    event_candidates?: string;
    event_review_report?: string;
    pass_candidates?: string;
    pass_review_report?: string;
    attacking_momentum?: string;
    possession_report?: string;
    possession_overlay_preview?: string;
  };
  run_artifacts?: Record<string, string>;
  [key: string]: unknown;
};

export type MatchPackageValidation = {
  status: 'ready' | 'blocked' | 'warnings' | string;
  missing_required: string[];
  warnings: string[];
  optional_available: string[];
  debug_available?: string[];
  identity_source?: 'legacy_identity' | 'reviewed_identity' | null;
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
  required?: Record<string, unknown>;
  optional?: Record<string, unknown>;
  debug?: Record<string, unknown>;
  package_validation?: MatchPackageValidation;
  player_assignments?: PlayerAssignmentsDocument | null;
  player_identity_assignments?: PlayerIdentityAssignmentsDocument | null;
  stable_players?: StablePlayersDocument | null;
  global_identity?: Record<string, unknown> | null;
  global_identity_report?: GlobalIdentityReport | null;
  camera_motion_report?: Record<string, unknown> | null;
  performance_report?: PerformanceReport | null;
  analysis_quality_report?: AnalysisQualityReport | null;
  analysis_chunk_manifest?: Record<string, unknown> | null;
  stabilization_report?: Record<string, unknown> | null;
  change_candidates?: ChangeCandidatesDocument | null;
  change_review_report?: ChangeReviewReport | null;
  team_clusters?: TeamClustersDocument | null;
  team_config?: TeamConfigDocument | null;
  team_stats?: TeamStatsDocument | null;
  frame_detection_counts?: FrameDetectionCountsDocument | null;
  movement_stats?: MovementStatsDocument | null;
  player_stats?: PlayerStatsDocument | null;
  resolved_player_stats?: ResolvedPlayerStatsDocument | null;
  reviewed_player_stats?: {
    source_snapshot_digest: string;
    players: ReviewedPlayerStats[];
    [key: string]: unknown;
  } | null;
  reviewed_player_heatmaps?: Record<string, unknown> | null;
  reviewed_stats_readiness?: Record<string, unknown> | null;
  reviewed_output_manifest?: Record<string, unknown> | null;
  identity_report_source?: 'reviewed_identity' | null;
  reviewed_identity_digest?: string | null;
  player_heatmaps?: PlayerHeatmapsDocument | null;
  tracklets?: Record<string, unknown> | null;
  tracking_quality_report?: TrackingQualityReport | null;
  ball_analysis_report?: Record<string, unknown> | null;
  ball_tracks?: Record<string, unknown> | null;
  ball_tracking_report?: BallTrackingReport | null;
  ball_quality_report?: BallQualityReport | null;
  possession_candidates?: Record<string, unknown> | null;
  possession_segments?: Record<string, unknown> | null;
  contact_candidates?: ContactCandidatesDocument | null;
  match_phase_config?: MatchPhaseConfigDocument | null;
  event_candidates?: EventCandidatesDocument | null;
  event_review_report?: EventReviewReport | null;
  pass_candidates?: PassCandidatesDocument | null;
  pass_review_report?: PassReviewReport | null;
  attacking_momentum?: AttackingMomentumDocument | null;
  possession_report?: PossessionReport | null;
  [key: string]: unknown;
};

export type PublicReportTeam = {
  team_label?: string;
  team_id?: string | null;
  team_name: string;
  display_color?: string | null;
  playing_time_sec: number;
  total_distance_m: number;
  high_intensity_distance_m: number;
  sprint_count: number;
  avg_speed_kmh: number;
  peak_speed_kmh: number;
  possession_share_percent?: number | null;
  pass_candidates: number;
  pass_attempts?: number;
  completed_passes?: number;
  failed_passes?: number;
  completion_rate?: number;
  restart_passes?: number;
  same_team_pass_candidates: number;
  turnover_or_interception_candidates: number;
  progressive_pass_candidates: number;
  accepted_passes: number;
};

export type PublicReportPlayer = {
  player_id: string;
  player_name: string;
  player_number?: string | null;
  player_role?: string | null;
  team_id?: string | null;
  team_name?: string | null;
  team_label?: string | null;
  playing_time_sec: number;
  detected_time_sec: number;
  certain_playing_time_sec?: number;
  possible_playing_time_sec?: number;
  ambiguous_playing_time_sec?: number;
  continuity_gap_time_sec?: number;
  playing_time_method?: string | null;
  total_distance_m: number;
  avg_speed_kmh: number;
  peak_speed_kmh: number;
  high_intensity_distance_m: number;
  sprint_count: number;
  heatmap?: {
    path: string;
    samples: number;
    detected_samples: number;
    quality: string;
    interactive?: {
      method: string;
      width: number;
      height: number;
      grid_width: number;
      grid_length: number;
      radius: number;
      max_value: number;
      points: Array<{
        x: number;
        y: number;
        value: number;
      }>;
    };
  };
};

export type PublicMatchReport = {
  schema_version: string;
  generated_at: string;
  id: string;
  source_match_id: string;
  report_type: 'public_match_report' | string;
  stats_semantics?: Record<string, string>;
  reviewed_identity_digest?: string | null;
  identity_coverage?: ReviewedIdentityCoverage | null;
  identity_coverage_readiness?: ReviewedIdentityCoverageReadiness | null;
  match: {
    id: string;
    title: string;
    match_date?: string | null;
    season?: string | null;
    venue?: string | null;
    format?: string | null;
    duration_sec?: number;
  };
  teams: PublicReportTeam[];
  players: PublicReportPlayer[];
  team_shape?: TeamShapeDocument | null;
  ball?: {
    known_possession_coverage?: number;
    controlled_coverage?: number;
    pass_candidates?: number;
    pass_attempts?: number;
    completed_passes?: number;
    failed_passes?: number;
    completion_rate?: number;
    restart_passes?: number;
    same_team_pass_candidates?: number;
    progressive_pass_candidates?: number;
    accepted_passes?: number;
    possession_timeline?: Array<{
      index: number;
      minute: number;
      label: string;
      window_label?: string;
      start_time_sec: number;
      end_time_sec: number;
      team_a_frames: number;
      team_b_frames: number;
      known_team_frames: number;
      team_a_percent: number;
      team_b_percent: number;
      cumulative_team_a_frames: number;
      cumulative_team_b_frames: number;
      cumulative_known_team_frames: number;
      cumulative_team_a_percent: number;
      cumulative_team_b_percent: number;
      free_frames: number;
      unknown_frames: number;
      team_a_share: number;
      team_b_share: number;
      controlled_coverage: number;
      controlled_coverage_percent: number;
      unknown_coverage: number;
    }>;
    attacking_momentum?: {
      experimental: boolean;
      status?: string;
      signal_quality?: string;
      product_readiness?: string;
      quality: string;
      warnings: Array<string | AnalyticsWarning>;
      timeline: Array<{
        index: number;
        minute: number;
        label: string;
        time_sec: number;
        start_time_sec: number;
        end_time_sec: number;
        signed_score: number;
        team_a_value: number;
        team_b_value: number;
        dominant_team_label?: 'A' | 'B' | null;
        confidence?: number;
        positional_confidence?: number;
        event_confidence?: number;
        controlled_coverage?: number;
        intensity?: number;
        evidence?: Record<string, number>;
      }>;
    };
  };
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
  public_report?: PublicMatchReport | null;
  teams: Array<Record<string, unknown>>;
  players: Array<Record<string, unknown>>;
  stable_players?: Array<Record<string, unknown>>;
};
