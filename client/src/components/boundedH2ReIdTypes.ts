export type BoundedH2Player = {
  player_id: string;
  player_name: string;
  player_number?: string | null;
};

export type BoundedH2Team = {
  team_label: string;
  team_name: string;
  players: BoundedH2Player[];
};

export type BoundedH2Card = {
  card_id: string;
  candidate_subject_id: string;
  observation_key: string;
  frame: number;
  tracklet_id: string;
  bbox_xyxy: [number, number, number, number];
  source_artifact_digest: string;
  team_label: string;
  frame_artifact: string;
  crop_artifact: string;
  frame_width: number;
  frame_height: number;
  decision_observation?: {
    observation_key: string;
    frame: number;
    tracklet_id: string;
    bbox_xyxy: [number, number, number, number];
    full_frame_artifact: string;
  };
  display_crop_observation?: {
    anchor_crop_id?: string | null;
    frame?: number | null;
    tracklet_id?: string | null;
    artifact: string;
  };
  preferred_advisory: {
    visible: boolean;
    reason: string;
  };
};

export type BoundedH2Decision = {
  candidate_subject_id: string;
  action: string;
  player_id?: string | null;
};

export type BoundedH2Session = {
  session_id: string;
  status: string;
  selection_digest: string;
  ranking_digest: string;
  cards: BoundedH2Card[];
  roster: BoundedH2Team[];
  decisions: BoundedH2Decision[];
  finished: boolean;
};
