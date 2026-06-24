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
