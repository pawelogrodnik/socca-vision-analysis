export type VideoMetadata = {
  fps: number;
  frame_count: number;
  width: number;
  height: number;
  duration_sec: number;
  path?: string;
};

export type Match = {
  id: string;
  title: string;
  video_filename: string;
  video: VideoMetadata;
  pitch_config?: unknown;
  analysis_report?: AnalysisReport;
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
