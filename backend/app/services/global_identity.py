from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import math
from typing import Any


TARGET_PLAYERS_PER_TEAM = 7
TARGET_ACTIVE_PLAYERS = TARGET_PLAYERS_PER_TEAM * 2
MAX_SUBJECTS_PER_TEAM = 14
MAX_STABLE_SUBJECTS = MAX_SUBJECTS_PER_TEAM * 2
MAX_ASSIGNMENT_SPEED_MPS = 9.5
MAX_ASSIGNMENT_DISTANCE_M = 12.0
MAX_PREDICTION_SEC = 2.0
MAX_VISUAL_PREDICTION_SEC = 0.75
SUBSTITUTION_GAP_SEC = 8.0
MIN_DETECTIONS_FOR_VISUAL_PREDICTION = 6
SWITCH_CONFIRMATION_FRAMES = 10
SWITCH_GUARD_MIN_DETECTIONS = 3
SWITCH_CONFLICT_RADIUS_M = 2.2
UNMATCHED_CONFIRMATION_FRAMES = 3
UNMATCHED_MAX_FRAME_GAP = 2
UNMATCHED_REPAIR_MAX_DISTANCE_M = 18.0
BBOX_OUTLIER_MIN_DETECTIONS = 6
SHADOW_LIKE_MAX_ASPECT_RATIO = 1.35
SHADOW_LIKE_LOW_CONFIDENCE = 0.25
TEAM_LABELS = ("A", "B")
MAX_STATS_SPEED_MPS = 8.5
MAX_STATS_SUSTAINED_SPEED_MPS = 8.0
MAX_STATS_ESTIMATED_GAP_SEC = 2.0
STATS_OBSERVED_GAP_FRAMES = 2
STATS_PEAK_SPEED_MIN_WINDOW_SEC = 0.5
STATS_PEAK_SPEED_MAX_WINDOW_SEC = 1.25
STATS_PEAK_SPEED_MAX_SEGMENT_GAP_SEC = 0.25
HIGH_INTENSITY_THRESHOLD_KMH = 15.0
SPRINT_THRESHOLD_KMH = 20.0
SPRINT_MIN_DURATION_SEC = 0.5


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _round_point(point: list[float] | tuple[float, float] | None, digits: int = 3) -> list[float] | None:
    if not point or len(point) < 2:
        return None
    return [round(float(point[0]), digits), round(float(point[1]), digits)]


def _distance_m(a: list[float] | tuple[float, float] | None, b: list[float] | tuple[float, float] | None) -> float | None:
    if not a or not b or len(a) < 2 or len(b) < 2:
        return None
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _bbox_center(bbox: list[float] | tuple[float, float, float, float] | None) -> list[float] | None:
    if not bbox or len(bbox) != 4:
        return None
    return [float(bbox[0] + bbox[2]) / 2.0, float(bbox[1] + bbox[3]) / 2.0]


def _bbox_size(bbox: list[float] | tuple[float, float, float, float] | None) -> list[float] | None:
    if not bbox or len(bbox) != 4:
        return None
    return [max(1.0, float(bbox[2]) - float(bbox[0])), max(1.0, float(bbox[3]) - float(bbox[1]))]


def _bbox_shape_penalty(a: list[float] | None, b: list[float] | None) -> float:
    a_size = _bbox_size(a)
    b_size = _bbox_size(b)
    if not a_size or not b_size:
        return 1.0
    w_ratio = max(a_size[0] / b_size[0], b_size[0] / a_size[0])
    h_ratio = max(a_size[1] / b_size[1], b_size[1] / a_size[1])
    return min(3.0, max(0.0, (w_ratio - 1.0) + (h_ratio - 1.0)))


def _lerp(a: float, b: float, ratio: float) -> float:
    return a + (b - a) * ratio


def _interpolate_bbox(a: list[float] | None, b: list[float] | None, ratio: float) -> list[int] | None:
    if not a or not b or len(a) != 4 or len(b) != 4:
        return None
    return [int(round(_lerp(float(a[idx]), float(b[idx]), ratio))) for idx in range(4)]


def _shift_bbox(bbox: list[float] | None, dx: float, dy: float) -> list[int] | None:
    if not bbox or len(bbox) != 4:
        return None
    return [
        int(round(float(bbox[0]) + dx)),
        int(round(float(bbox[1]) + dy)),
        int(round(float(bbox[2]) + dx)),
        int(round(float(bbox[3]) + dy)),
    ]


def _rgb_to_hex(rgb: list[float] | tuple[float, float, float] | None) -> str | None:
    if not rgb or len(rgb) < 3:
        return None
    return "#" + "".join(f"{max(0, min(255, int(round(channel)))):02x}" for channel in rgb[:3])


def confidence_level(score: float) -> str:
    if score >= 0.72:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


@dataclass
class Observation:
    frame: int
    time_sec: float
    bbox_xyxy: list[int]
    footpoint: list[float] | None
    calibrated_footpoint: list[float] | None
    pitch_m: list[float]
    confidence: float
    tracklet_id: str
    raw_track_id: int
    team_label: str
    team_id: str | None
    team_name: str | None
    team_confidence: float
    appearance_rgb: list[float] | None = None


@dataclass
class SlotState:
    slot_id: str
    stable_subject_id: str
    team_label: str
    pitch_width_m: float
    pitch_length_m: float
    pitch_polygon: Any | None = None
    active: bool = False
    status: str = "inactive"
    stint_index: int = 0
    current_stint_id: str | None = None
    stints: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    tracklet_ids: set[str] = field(default_factory=set)
    raw_track_ids: set[int] = field(default_factory=set)
    team_id: str | None = None
    team_name: str | None = None
    team_confidences: list[float] = field(default_factory=list)
    detection_confidences: list[float] = field(default_factory=list)
    appearance_samples: list[list[float]] = field(default_factory=list)
    first_frame: int | None = None
    last_frame: int | None = None
    first_time_sec: float | None = None
    last_time_sec: float | None = None
    last_detected_frame: int | None = None
    last_detected_time_sec: float | None = None
    last_pitch_m: list[float] | None = None
    previous_pitch_m: list[float] | None = None
    last_bbox_xyxy: list[int] | None = None
    previous_bbox_xyxy: list[int] | None = None
    blocked_team_switches: int = 0
    blocked_identity_switches: int = 0
    suspicious_assignments: list[dict[str, Any]] = field(default_factory=list)
    rejected_candidates: list[dict[str, Any]] = field(default_factory=list)
    identity_events: list[dict[str, Any]] = field(default_factory=list)
    pending_tracklet_id: str | None = None
    pending_tracklet_frames: int = 0
    pending_last_frame: int | None = None
    pending_observations: list[Observation] = field(default_factory=list)
    predicted_frames: int = 0
    missing_frames: int = 0
    ambiguous_frames: int = 0
    detected_frames: int = 0

    def begin_stint(self, obs: Observation) -> None:
        self.stint_index += 1
        self.current_stint_id = f"{self.slot_id}-S{self.stint_index:02d}"
        self.active = True
        self.status = "detected"
        self.stints.append(
            {
                "stint_id": self.current_stint_id,
                "slot_id": self.slot_id,
                "team_label": self.team_label,
                "start_frame": obs.frame,
                "end_frame": obs.frame,
                "start_time_sec": round(obs.time_sec, 3),
                "end_time_sec": round(obs.time_sec, 3),
                "status": "active",
                "detected_frames": 0,
                "predicted_frames": 0,
                "missing_frames": 0,
                "ambiguous_frames": 0,
                "tracklet_ids": [],
                "raw_track_ids": [],
            }
        )

    def close_stint(self, frame: int, time_sec: float, reason: str) -> None:
        if self.stints:
            self.stints[-1]["end_frame"] = int(frame)
            self.stints[-1]["end_time_sec"] = round(time_sec, 3)
            self.stints[-1]["status"] = "closed"
            self.stints[-1]["end_reason"] = reason
        self.current_stint_id = None
        self.active = False
        self.status = "inactive"

    def predicted_pitch(self, time_sec: float) -> list[float] | None:
        if not self.last_pitch_m:
            return None
        if not self.previous_pitch_m or self.last_detected_time_sec is None:
            return list(self.last_pitch_m)
        previous_time = self._previous_time_sec()
        dt_history = max(0.001, self.last_detected_time_sec - previous_time)
        vx = (self.last_pitch_m[0] - self.previous_pitch_m[0]) / dt_history
        vy = (self.last_pitch_m[1] - self.previous_pitch_m[1]) / dt_history
        speed = math.hypot(vx, vy)
        if speed > MAX_ASSIGNMENT_SPEED_MPS:
            scale = MAX_ASSIGNMENT_SPEED_MPS / speed
            vx *= scale
            vy *= scale
        dt = max(0.0, time_sec - self.last_detected_time_sec)
        return [self.last_pitch_m[0] + vx * dt, self.last_pitch_m[1] + vy * dt]

    def predicted_bbox(self, time_sec: float) -> list[int] | None:
        if not self.last_bbox_xyxy:
            return None
        if not self.previous_bbox_xyxy or self.last_detected_time_sec is None:
            return list(self.last_bbox_xyxy)
        previous_time = self._previous_time_sec()
        dt_history = max(0.001, self.last_detected_time_sec - previous_time)
        last_center = _bbox_center(self.last_bbox_xyxy)
        previous_center = _bbox_center(self.previous_bbox_xyxy)
        if not last_center or not previous_center:
            return list(self.last_bbox_xyxy)
        vx = (last_center[0] - previous_center[0]) / dt_history
        vy = (last_center[1] - previous_center[1]) / dt_history
        dt = max(0.0, time_sec - self.last_detected_time_sec)
        dx = vx * dt
        dy = vy * dt
        width, height = _bbox_size(self.last_bbox_xyxy) or [20.0, 40.0]
        max_shift = max(16.0, min(80.0, max(width, height) * 1.4))
        shift = math.hypot(dx, dy)
        if shift > max_shift:
            scale = max_shift / shift
            dx *= scale
            dy *= scale
        return _shift_bbox(self.last_bbox_xyxy, dx, dy)

    def _previous_time_sec(self) -> float:
        detected = [row for row in self.history if row.get("source") == "detected"]
        if len(detected) < 2:
            return self.last_detected_time_sec or 0.0
        return float(detected[-2].get("time_sec") or self.last_detected_time_sec or 0.0)

    def add_detection(self, obs: Observation, *, assignment_cost: float | None = None) -> None:
        if not self.active:
            self.begin_stint(obs)
        if obs.team_label in TEAM_LABELS and obs.team_label != self.team_label:
            self.blocked_team_switches += 1
            self.suspicious_assignments.append(
                {
                    "frame": obs.frame,
                    "time_sec": round(obs.time_sec, 3),
                    "reason": "team_switch_blocked",
                    "slot_team": self.team_label,
                    "observation_team": obs.team_label,
                    "tracklet_id": obs.tracklet_id,
                }
            )
            return

        self.clear_pending_tracklet(obs.tracklet_id)
        self.status = "detected"
        self.detected_frames += 1
        self.first_frame = obs.frame if self.first_frame is None else min(self.first_frame, obs.frame)
        self.last_frame = obs.frame if self.last_frame is None else max(self.last_frame, obs.frame)
        self.first_time_sec = obs.time_sec if self.first_time_sec is None else min(self.first_time_sec, obs.time_sec)
        self.last_time_sec = obs.time_sec if self.last_time_sec is None else max(self.last_time_sec, obs.time_sec)
        self.previous_pitch_m = self.last_pitch_m
        self.previous_bbox_xyxy = self.last_bbox_xyxy
        self.last_pitch_m = list(obs.pitch_m)
        self.last_bbox_xyxy = list(obs.bbox_xyxy)
        self.last_detected_frame = obs.frame
        self.last_detected_time_sec = obs.time_sec
        self.tracklet_ids.add(obs.tracklet_id)
        self.raw_track_ids.add(obs.raw_track_id)
        self.detection_confidences.append(obs.confidence)
        if obs.team_id and not self.team_id:
            self.team_id = obs.team_id
        if obs.team_name and not self.team_name:
            self.team_name = obs.team_name
        if obs.team_confidence:
            self.team_confidences.append(obs.team_confidence)
        if obs.appearance_rgb:
            self.appearance_samples.append(obs.appearance_rgb)
        if assignment_cost is not None and assignment_cost > 18.0:
            self.suspicious_assignments.append(
                {
                    "frame": obs.frame,
                    "time_sec": round(obs.time_sec, 3),
                    "reason": "high_assignment_cost",
                    "cost": round(assignment_cost, 3),
                    "tracklet_id": obs.tracklet_id,
                }
            )
        row = {
            "frame": obs.frame,
            "time_sec": round(obs.time_sec, 3),
            "bbox_xyxy": list(obs.bbox_xyxy),
            "footpoint": obs.footpoint,
            "pitch_m": _round_point(obs.pitch_m),
            "tracklet_id": obs.tracklet_id,
            "raw_track_id": obs.raw_track_id,
            "confidence": round(obs.confidence, 4),
            "source": "detected",
            "status": "detected",
            "visual_trusted": True,
            "stint_id": self.current_stint_id,
            "assignment_cost": round(assignment_cost, 3) if assignment_cost is not None else None,
        }
        self.history.append(row)
        self._update_current_stint(obs.frame, obs.time_sec, "detected", obs.tracklet_id, obs.raw_track_id)

    def backfill_missing_detection(self, obs: Observation, *, reason: str) -> bool:
        if obs.team_label in TEAM_LABELS and obs.team_label != self.team_label:
            return False
        for index, row in enumerate(self.history):
            if int(row.get("frame") or -1) != obs.frame:
                continue
            if row.get("source") != "missing":
                continue
            self.history[index] = {
                **row,
                "bbox_xyxy": list(obs.bbox_xyxy),
                "footpoint": obs.footpoint,
                "pitch_m": _round_point(obs.pitch_m),
                "tracklet_id": obs.tracklet_id,
                "raw_track_id": obs.raw_track_id,
                "confidence": round(obs.confidence, 4),
                "source": "detected",
                "status": "detected",
                "visual_trusted": True,
                "repair_reason": reason,
                "repaired_from": "unmatched_raw_detection",
                "missing_since_frame": None,
                "short_gap_sec": None,
            }
            self.tracklet_ids.add(obs.tracklet_id)
            self.raw_track_ids.add(obs.raw_track_id)
            if obs.team_confidence:
                self.team_confidences.append(obs.team_confidence)
            if obs.appearance_rgb:
                self.appearance_samples.append(obs.appearance_rgb)
            self.identity_events.append(
                {
                    "type": "unmatched_raw_backfill",
                    "frame": obs.frame,
                    "time_sec": round(obs.time_sec, 3),
                    "slot_id": self.slot_id,
                    "tracklet_id": obs.tracklet_id,
                    "raw_track_id": obs.raw_track_id,
                    "reason": reason,
                }
            )
            self._recompute_history_state()
            return True
        return False

    def add_ambiguous(
        self,
        obs: Observation,
        *,
        reason: str,
        assignment_cost: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if not self.active:
            self.begin_stint(obs)
        self.status = "ambiguous"
        self.ambiguous_frames += 1
        self.blocked_identity_switches += 1
        self.first_frame = obs.frame if self.first_frame is None else min(self.first_frame, obs.frame)
        self.last_frame = obs.frame if self.last_frame is None else max(self.last_frame, obs.frame)
        self.first_time_sec = obs.time_sec if self.first_time_sec is None else min(self.first_time_sec, obs.time_sec)
        self.last_time_sec = obs.time_sec if self.last_time_sec is None else max(self.last_time_sec, obs.time_sec)
        event = {
            "frame": obs.frame,
            "time_sec": round(obs.time_sec, 3),
            "reason": reason,
            "slot_id": self.slot_id,
            "candidate_tracklet_id": obs.tracklet_id,
            "candidate_raw_track_id": obs.raw_track_id,
            "assignment_cost": round(assignment_cost, 3) if assignment_cost is not None else None,
        }
        if details:
            event.update({key: value for key, value in details.items() if value is not None})
        self.rejected_candidates.append(event)
        self.identity_events.append({"type": "ambiguous_candidate", **event})
        self.suspicious_assignments.append(event)
        row = {
            "frame": obs.frame,
            "time_sec": round(obs.time_sec, 3),
            "bbox_xyxy": list(obs.bbox_xyxy),
            "footpoint": obs.footpoint,
            "pitch_m": _round_point(obs.pitch_m),
            "tracklet_id": None,
            "raw_track_id": None,
            "candidate_tracklet_id": obs.tracklet_id,
            "candidate_raw_track_id": obs.raw_track_id,
            "confidence": round(obs.confidence, 4),
            "source": "ambiguous",
            "status": "ambiguous",
            "visual_trusted": False,
            "stint_id": self.current_stint_id,
            "ambiguous_reason": reason,
            "assignment_cost": round(assignment_cost, 3) if assignment_cost is not None else None,
        }
        self.history.append(row)
        self._update_current_stint(obs.frame, obs.time_sec, "ambiguous", None, None)

    def add_prediction_or_missing(self, frame: int, time_sec: float) -> None:
        if not self.active or self.last_detected_time_sec is None:
            return
        gap_sec = time_sec - self.last_detected_time_sec
        if gap_sec > SUBSTITUTION_GAP_SEC:
            self.close_stint(frame, time_sec, "missing_too_long")
            return
        predicted_pitch = self.predicted_pitch(time_sec)
        self.first_frame = frame if self.first_frame is None else min(self.first_frame, frame)
        self.last_frame = frame if self.last_frame is None else max(self.last_frame, frame)
        self.first_time_sec = time_sec if self.first_time_sec is None else min(self.first_time_sec, time_sec)
        self.last_time_sec = time_sec if self.last_time_sec is None else max(self.last_time_sec, time_sec)
        self.status = "missing"
        self.missing_frames += 1
        row = {
            "frame": int(frame),
            "time_sec": round(time_sec, 3),
            "bbox_xyxy": None,
            "pitch_m": _round_point(predicted_pitch),
            "tracklet_id": None,
            "raw_track_id": None,
            "confidence": 0.0,
            "source": "missing",
            "status": "missing",
            "visual_trusted": False,
            "stint_id": self.current_stint_id,
            "missing_since_frame": self.last_detected_frame,
            "short_gap_sec": round(gap_sec, 3) if gap_sec <= MAX_PREDICTION_SEC else None,
        }
        self.history.append(row)
        self._update_current_stint(frame, time_sec, "missing", None, None)

    def record_pending_tracklet(self, obs: Observation) -> int:
        if self.pending_tracklet_id != obs.tracklet_id or (
            self.pending_last_frame is not None and obs.frame > self.pending_last_frame + 2
        ):
            self.pending_tracklet_id = obs.tracklet_id
            self.pending_tracklet_frames = 0
            self.pending_observations = []
        self.pending_tracklet_frames += 1
        self.pending_last_frame = obs.frame
        self.pending_observations.append(obs)
        return self.pending_tracklet_frames

    def clear_pending_tracklet(self, tracklet_id: str | None = None) -> None:
        if tracklet_id is not None and self.pending_tracklet_id != tracklet_id:
            return
        self.pending_tracklet_id = None
        self.pending_tracklet_frames = 0
        self.pending_last_frame = None
        self.pending_observations = []

    def _pitch_point_inside_play_area(self, point: list[float] | None) -> bool:
        if not point or len(point) < 2:
            return False
        x, y = float(point[0]), float(point[1])
        return 0.0 <= x <= self.pitch_width_m and 0.0 <= y <= self.pitch_length_m

    def _can_draw_predicted_bbox(self, gap_sec: float) -> bool:
        if self.detected_frames < MIN_DETECTIONS_FOR_VISUAL_PREDICTION:
            return False
        return gap_sec <= MAX_VISUAL_PREDICTION_SEC

    def _bbox_footpoint_inside_pitch_polygon(self, bbox_xyxy: list[int], footpoint_override: list[float] | None = None) -> bool:
        if self.pitch_polygon is None:
            return True
        try:
            import cv2

            if footpoint_override and len(footpoint_override) >= 2:
                footpoint = (float(footpoint_override[0]), float(footpoint_override[1]))
            else:
                x1, _, x2, y2 = [float(value) for value in bbox_xyxy]
                footpoint = ((x1 + x2) / 2.0, y2)
            return cv2.pointPolygonTest(self.pitch_polygon.astype("float32"), footpoint, False) >= 0
        except Exception:
            return True

    def _update_current_stint(
        self,
        frame: int,
        time_sec: float,
        status: str,
        tracklet_id: str | None,
        raw_track_id: int | None,
    ) -> None:
        if not self.stints:
            return
        stint = self.stints[-1]
        stint["end_frame"] = int(frame)
        stint["end_time_sec"] = round(time_sec, 3)
        if status == "detected":
            stint["detected_frames"] = int(stint.get("detected_frames") or 0) + 1
        elif status == "predicted":
            stint["predicted_frames"] = int(stint.get("predicted_frames") or 0) + 1
        elif status == "missing":
            stint["missing_frames"] = int(stint.get("missing_frames") or 0) + 1
        elif status == "ambiguous":
            stint["ambiguous_frames"] = int(stint.get("ambiguous_frames") or 0) + 1
        if tracklet_id and tracklet_id not in stint["tracklet_ids"]:
            stint["tracklet_ids"].append(tracklet_id)
        if raw_track_id is not None and raw_track_id not in stint["raw_track_ids"]:
            stint["raw_track_ids"].append(raw_track_id)

    def _recompute_history_state(self) -> None:
        active_rows = [
            row
            for row in sorted(self.history, key=lambda item: (int(item.get("frame") or 0), float(item.get("time_sec") or 0.0)))
            if row.get("source") in {"detected", "missing", "ambiguous", "predicted"}
        ]
        detected_rows = [row for row in active_rows if row.get("source") == "detected"]
        missing_rows = [row for row in active_rows if row.get("source") == "missing"]
        ambiguous_rows = [row for row in active_rows if row.get("source") == "ambiguous"]
        predicted_rows = [row for row in active_rows if row.get("source") == "predicted"]

        self.detected_frames = len(detected_rows)
        self.missing_frames = len(missing_rows)
        self.ambiguous_frames = len(ambiguous_rows)
        self.predicted_frames = len(predicted_rows)
        self.tracklet_ids = {str(row.get("tracklet_id")) for row in detected_rows if row.get("tracklet_id")}
        self.raw_track_ids = {int(row.get("raw_track_id")) for row in detected_rows if row.get("raw_track_id") is not None}
        self.detection_confidences = [float(row.get("confidence") or 0.0) for row in detected_rows]

        if active_rows:
            self.first_frame = int(active_rows[0].get("frame") or 0)
            self.last_frame = int(active_rows[-1].get("frame") or 0)
            self.first_time_sec = float(active_rows[0].get("time_sec") or 0.0)
            self.last_time_sec = float(active_rows[-1].get("time_sec") or 0.0)
            self.status = str(active_rows[-1].get("status") or active_rows[-1].get("source") or self.status)
        if detected_rows:
            self.previous_pitch_m = detected_rows[-2].get("pitch_m") if len(detected_rows) >= 2 else None
            self.previous_bbox_xyxy = detected_rows[-2].get("bbox_xyxy") if len(detected_rows) >= 2 else None
            self.last_pitch_m = detected_rows[-1].get("pitch_m")
            self.last_bbox_xyxy = detected_rows[-1].get("bbox_xyxy")
            self.last_detected_frame = int(detected_rows[-1].get("frame") or 0)
            self.last_detected_time_sec = float(detected_rows[-1].get("time_sec") or 0.0)

        stints_by_id = {stint.get("stint_id"): stint for stint in self.stints if stint.get("stint_id")}
        for stint in stints_by_id.values():
            stint["detected_frames"] = 0
            stint["predicted_frames"] = 0
            stint["missing_frames"] = 0
            stint["ambiguous_frames"] = 0
            stint["tracklet_ids"] = []
            stint["raw_track_ids"] = []
        for row in active_rows:
            stint = stints_by_id.get(row.get("stint_id"))
            if stint is None:
                continue
            frame = int(row.get("frame") or 0)
            time_sec = round(float(row.get("time_sec") or 0.0), 3)
            if frame >= int(stint.get("end_frame") or frame):
                stint["end_frame"] = frame
                stint["end_time_sec"] = time_sec
            source = str(row.get("source") or "")
            if source == "detected":
                stint["detected_frames"] = int(stint.get("detected_frames") or 0) + 1
                tracklet_id = row.get("tracklet_id")
                raw_track_id = row.get("raw_track_id")
                if tracklet_id and tracklet_id not in stint["tracklet_ids"]:
                    stint["tracklet_ids"].append(tracklet_id)
                if raw_track_id is not None and raw_track_id not in stint["raw_track_ids"]:
                    stint["raw_track_ids"].append(raw_track_id)
            elif source == "predicted":
                stint["predicted_frames"] = int(stint.get("predicted_frames") or 0) + 1
            elif source == "missing":
                stint["missing_frames"] = int(stint.get("missing_frames") or 0) + 1
            elif source == "ambiguous":
                stint["ambiguous_frames"] = int(stint.get("ambiguous_frames") or 0) + 1


def build_observations_from_tracklets(tracklets: list[dict[str, Any]]) -> list[Observation]:
    observations: list[Observation] = []
    for tracklet in tracklets:
        tracklet_id = str(tracklet.get("tracklet_id") or "")
        if not tracklet_id:
            continue
        raw_track_id = int(tracklet.get("source_track_id") or 0)
        team_label = str(tracklet.get("team_label") or "U")
        if team_label not in {"A", "B"}:
            team_label = "U"
        for position in tracklet.get("positions") or []:
            bbox = position.get("bbox_xyxy")
            pitch = position.get("smoothed_pitch_m") or position.get("pitch_m")
            if not bbox or len(bbox) != 4 or not pitch or len(pitch) < 2:
                continue
            observations.append(
                Observation(
                    frame=int(position.get("frame") or 0),
                    time_sec=float(position.get("time_sec") or 0.0),
                    bbox_xyxy=[int(round(float(value))) for value in bbox],
                    footpoint=position.get("footpoint"),
                    calibrated_footpoint=position.get("calibrated_footpoint"),
                    pitch_m=[float(pitch[0]), float(pitch[1])],
                    confidence=float(position.get("confidence") or tracklet.get("mean_confidence") or 0.0),
                    tracklet_id=tracklet_id,
                    raw_track_id=raw_track_id,
                    team_label=team_label,
                    team_id=tracklet.get("team_id"),
                    team_name=tracklet.get("team_name"),
                    team_confidence=float(tracklet.get("team_confidence") or 0.0),
                    appearance_rgb=tracklet.get("appearance_rgb"),
                )
            )
    return sorted(observations, key=lambda item: (item.frame, item.time_sec, item.raw_track_id))


def resolve_global_identity(
    tracklets: list[dict[str, Any]],
    *,
    raw_tracks_count: int,
    rejected_tracklets_count: int,
    pitch_width_m: float,
    pitch_length_m: float,
    fps: float,
    pitch_polygon: Any | None = None,
) -> dict[str, Any]:
    observations = build_observations_from_tracklets(tracklets)
    observations_by_frame: dict[int, list[Observation]] = defaultdict(list)
    for obs in observations:
        observations_by_frame[obs.frame].append(obs)
    max_frame = max([0] + list(observations_by_frame.keys()))
    slots = _create_slots(_team_info_from_tracklets(tracklets), pitch_width_m, pitch_length_m, pitch_polygon)
    tracklet_owner: dict[str, str] = {}
    rejected_start_candidates: list[dict[str, Any]] = []

    for frame in range(max_frame + 1):
        frame_time = frame / max(fps, 0.001)
        frame_observations = observations_by_frame.get(frame, [])
        _assign_frame(slots, frame_observations, frame, frame_time, tracklet_owner, rejected_start_candidates)

    repair_summary = _repair_unmatched_observations(slots, observations_by_frame, tracklet_owner)
    unmatched_observations = _remaining_unmatched_observations(slots, observations_by_frame)

    for slot in slots:
        if slot.active:
            slot.close_stint(slot.last_frame or max_frame, slot.last_time_sec or (max_frame / max(fps, 0.001)), "end_of_analysis")

    frame_rows = _build_identity_frame_rows(slots, observations_by_frame, max_frame, fps)
    slot_docs = [_slot_to_doc(slot, fps) for slot in slots]
    active_slot_docs = [slot for slot in slot_docs if slot["detected_frames"] > 0 or slot["predicted_frames"] > 0]
    movement_stats = [slot.get("movement_stats") or {} for slot in active_slot_docs]
    summary = {
        "raw_tracks": raw_tracks_count,
        "clean_tracklets": len(tracklets),
        "rejected_tracklets": rejected_tracklets_count,
        "target_active_players": TARGET_ACTIVE_PLAYERS,
        "players_per_team": TARGET_PLAYERS_PER_TEAM,
        "slots_total": len(active_slot_docs),
        "stable_players": len(active_slot_docs),
        "stints_total": sum(len(slot.get("stints") or []) for slot in active_slot_docs),
        "team_counts": _team_counts(active_slot_docs),
        "detected_frames": sum(int(slot.get("detected_frames") or 0) for slot in active_slot_docs),
        "predicted_frames": sum(int(slot.get("predicted_frames") or 0) for slot in active_slot_docs),
        "missing_frames": sum(int(slot.get("missing_frames") or 0) for slot in active_slot_docs),
        "ambiguous_frames": sum(int(slot.get("ambiguous_frames") or 0) for slot in active_slot_docs),
        "blocked_team_switches": sum(int(slot.get("blocked_team_switches") or 0) for slot in active_slot_docs),
        "blocked_identity_switches": sum(int(slot.get("blocked_identity_switches") or 0) for slot in active_slot_docs),
        "suspicious_assignments": sum(len(slot.get("suspicious_assignments") or []) for slot in active_slot_docs),
        "rejected_candidates": sum(len(slot.get("rejected_candidates") or []) for slot in active_slot_docs),
        "rejected_start_candidates": len(rejected_start_candidates),
        "unmatched_raw_backfilled": int(repair_summary.get("backfilled_observations") or 0),
        "unmatched_raw_backfill_tracklets": int(repair_summary.get("backfilled_tracklets") or 0),
        "unmatched_raw_remaining": len(unmatched_observations),
        "low_confidence_players": sum(1 for slot in active_slot_docs if slot.get("confidence") == "low"),
        "risky_links": sum(len(slot.get("suspicious_assignments") or []) for slot in active_slot_docs),
        "predicted_visible_boxes": 0,
        "ghost_bbox_count": 0,
        "total_distance_m": round(sum(float(stats.get("total_distance_m") or 0.0) for stats in movement_stats), 2),
        "observed_distance_m": round(sum(float(stats.get("observed_distance_m") or 0.0) for stats in movement_stats), 2),
        "estimated_gap_distance_m": round(sum(float(stats.get("estimated_gap_distance_m") or 0.0) for stats in movement_stats), 2),
        "players_with_estimated_distance": sum(1 for stats in movement_stats if float(stats.get("estimated_gap_distance_m") or 0.0) > 0),
    }
    return {
        "schema_version": "0.1.0",
        "resolver_version": "conservative_identity_v2",
        "identity_semantics": "stint_first",
        "generated_at": now_iso(),
        "pitch_dimensions_m": {"width_m": pitch_width_m, "length_m": pitch_length_m},
        "parameters": {
        "players_per_team": TARGET_PLAYERS_PER_TEAM,
        "max_subjects_per_team": MAX_SUBJECTS_PER_TEAM,
        "target_active_players": TARGET_ACTIVE_PLAYERS,
            "max_assignment_speed_mps": MAX_ASSIGNMENT_SPEED_MPS,
            "max_assignment_distance_m": MAX_ASSIGNMENT_DISTANCE_M,
            "max_prediction_sec": MAX_PREDICTION_SEC,
            "substitution_gap_sec": SUBSTITUTION_GAP_SEC,
            "switch_confirmation_frames": SWITCH_CONFIRMATION_FRAMES,
            "switch_conflict_radius_m": SWITCH_CONFLICT_RADIUS_M,
            "unmatched_confirmation_frames": UNMATCHED_CONFIRMATION_FRAMES,
            "unmatched_repair_max_distance_m": UNMATCHED_REPAIR_MAX_DISTANCE_M,
            "assignment_solver": "lapjv_or_greedy_fallback",
            "stats_max_speed_mps": MAX_STATS_SPEED_MPS,
            "stats_max_sustained_speed_mps": MAX_STATS_SUSTAINED_SPEED_MPS,
            "stats_max_estimated_gap_sec": MAX_STATS_ESTIMATED_GAP_SEC,
            "stats_peak_speed_min_window_sec": STATS_PEAK_SPEED_MIN_WINDOW_SEC,
            "stats_peak_speed_max_window_sec": STATS_PEAK_SPEED_MAX_WINDOW_SEC,
            "high_intensity_threshold_kmh": HIGH_INTENSITY_THRESHOLD_KMH,
            "sprint_threshold_kmh": SPRINT_THRESHOLD_KMH,
            "sprint_min_duration_sec": SPRINT_MIN_DURATION_SEC,
        },
        "summary": summary,
        "slots": sorted(active_slot_docs, key=lambda item: item["slot_id"]),
        "rejected_start_candidates": rejected_start_candidates[:1000],
        "unmatched_repair_summary": repair_summary,
        "unmatched_observations": unmatched_observations,
        "frames": frame_rows,
    }


def _team_info_from_tracklets(tracklets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    info: dict[str, dict[str, Any]] = {}
    for tracklet in tracklets:
        label = str(tracklet.get("team_label") or "U")
        if label not in TEAM_LABELS or label in info:
            continue
        info[label] = {
            "team_id": tracklet.get("team_id"),
            "team_name": tracklet.get("team_name") or f"Team {label}",
        }
    return info


def _create_slots(
    team_info: dict[str, dict[str, Any]],
    pitch_width_m: float,
    pitch_length_m: float,
    pitch_polygon: Any | None,
) -> list[SlotState]:
    slots: list[SlotState] = []
    for team_label in TEAM_LABELS:
        for index in range(1, MAX_SUBJECTS_PER_TEAM + 1):
            slot_id = f"{team_label}{index:02d}"
            slot = SlotState(
                slot_id=slot_id,
                stable_subject_id=f"slot-{slot_id}",
                team_label=team_label,
                pitch_width_m=pitch_width_m,
                pitch_length_m=pitch_length_m,
                pitch_polygon=pitch_polygon,
            )
            slot.team_id = team_info.get(team_label, {}).get("team_id")
            slot.team_name = team_info.get(team_label, {}).get("team_name") or f"Team {team_label}"
            slots.append(slot)
    return slots


def _assign_frame(
    slots: list[SlotState],
    observations: list[Observation],
    frame: int,
    time_sec: float,
    tracklet_owner: dict[str, str],
    rejected_start_candidates: list[dict[str, Any]],
) -> None:
    active_slots = [slot for slot in slots if slot.active]
    slots_by_id = {slot.slot_id: slot for slot in slots}
    cost_matrix: list[list[float]] = []
    slot_rows: list[SlotState] = []
    obs_cols = list(observations)
    for slot in active_slots:
        row: list[float] = []
        any_cost = False
        for obs in obs_cols:
            owner = tracklet_owner.get(obs.tracklet_id)
            if owner is not None and owner != slot.slot_id:
                row.append(10_000.0)
                continue
            cost = _assignment_cost(slot, obs)
            if cost is None:
                row.append(10_000.0)
            else:
                row.append(cost)
                any_cost = True
        if any_cost:
            slot_rows.append(slot)
            cost_matrix.append(row)

    assigned_slots: set[str] = set()
    assigned_observations: set[int] = set()
    for row_index, col_index, cost in _solve_assignment(cost_matrix, cost_limit=999.0):
        slot = slot_rows[row_index]
        obs = obs_cols[col_index]
        if slot.slot_id in assigned_slots or col_index in assigned_observations:
            continue
        if _assignment_cost(slot, obs) is None:
            continue
        team_rebalanced = obs.team_label in TEAM_LABELS and obs.team_label != slot.team_label and obs.tracklet_id in slot.tracklet_ids
        assigned_obs = _observation_for_slot(obs, slot) if team_rebalanced else obs
        guard = _conservative_assignment_guard(slot, assigned_obs, obs_cols)
        if guard is not None:
            slot.add_ambiguous(
                assigned_obs,
                reason=str(guard.get("reason") or "identity_switch_guard"),
                assignment_cost=cost,
                details=guard,
            )
            assigned_slots.add(slot.slot_id)
            assigned_observations.add(col_index)
            continue
        slot.add_detection(assigned_obs, assignment_cost=cost)
        tracklet_owner[obs.tracklet_id] = slot.slot_id
        if team_rebalanced:
            slot.suspicious_assignments.append(
                {
                    "frame": obs.frame,
                    "time_sec": round(obs.time_sec, 3),
                    "reason": "owned_tracklet_team_rebalanced",
                    "observation_team": obs.team_label,
                    "slot_team": slot.team_label,
                    "tracklet_id": obs.tracklet_id,
                }
            )
        assigned_slots.add(slot.slot_id)
        assigned_observations.add(col_index)

    for slot in active_slots:
        if slot.slot_id not in assigned_slots:
            slot.add_prediction_or_missing(frame, time_sec)

    for obs_index, obs in enumerate(obs_cols):
        if obs_index in assigned_observations:
            continue
        owner = tracklet_owner.get(obs.tracklet_id)
        if owner is not None:
            owner_slot = slots_by_id.get(owner)
            if owner_slot is None or owner_slot.active:
                continue
            candidate = (owner_slot, obs.team_label in TEAM_LABELS and obs.team_label != owner_slot.team_label)
        else:
            spawn_rejection = _stateless_detection_rejection_reason(obs)
            if spawn_rejection is not None:
                rejected_start_candidates.append(
                    {
                        "frame": obs.frame,
                        "time_sec": round(obs.time_sec, 3),
                        "reason": spawn_rejection["reason"],
                        "tracklet_id": obs.tracklet_id,
                        "raw_track_id": obs.raw_track_id,
                        "team_label": obs.team_label,
                        "confidence": round(obs.confidence, 4),
                        **{key: value for key, value in spawn_rejection.items() if key != "reason"},
                    }
                )
                continue
            selected = _select_inactive_slot(slots, obs)
            if selected is None:
                continue
            candidate = selected
        slot, team_rebalanced = candidate
        if slot is None:
            continue
        assigned_obs = _observation_for_slot(obs, slot) if team_rebalanced else obs
        slot.add_detection(assigned_obs, assignment_cost=None)
        tracklet_owner[obs.tracklet_id] = slot.slot_id
        if team_rebalanced:
            slot.suspicious_assignments.append(
                {
                    "frame": obs.frame,
                    "time_sec": round(obs.time_sec, 3),
                    "reason": "team_rebalanced_to_fill_7v7_slots",
                    "observation_team": obs.team_label,
                    "slot_team": slot.team_label,
                    "tracklet_id": obs.tracklet_id,
                }
            )


def _repair_unmatched_observations(
    slots: list[SlotState],
    observations_by_frame: dict[int, list[Observation]],
    tracklet_owner: dict[str, str],
) -> dict[str, Any]:
    unmatched_by_tracklet: dict[str, list[Observation]] = defaultdict(list)
    assigned_by_frame = _assigned_observation_keys_by_frame(slots)
    for frame, observations in observations_by_frame.items():
        assigned = assigned_by_frame.get(frame, set())
        for obs in observations:
            if (obs.tracklet_id, obs.raw_track_id) in assigned:
                continue
            if obs.team_label not in TEAM_LABELS:
                continue
            if _stateless_detection_rejection_reason(obs) is not None:
                continue
            unmatched_by_tracklet[obs.tracklet_id].append(obs)

    backfilled_observations = 0
    repaired_tracklets: set[str] = set()
    repair_events: list[dict[str, Any]] = []
    for tracklet_id, observations in unmatched_by_tracklet.items():
        for run in _consecutive_observation_runs(observations):
            if len(run) < UNMATCHED_CONFIRMATION_FRAMES:
                continue
            slot = _select_unmatched_repair_slot(slots, run)
            if slot is None:
                continue
            repaired_frames = []
            for obs in run:
                if slot.backfill_missing_detection(obs, reason="unmatched_raw_confirmed"):
                    backfilled_observations += 1
                    repaired_frames.append(obs.frame)
            if not repaired_frames:
                continue
            repaired_tracklets.add(tracklet_id)
            tracklet_owner[tracklet_id] = slot.slot_id
            repair_events.append(
                {
                    "tracklet_id": tracklet_id,
                    "raw_track_id": run[0].raw_track_id,
                    "slot_id": slot.slot_id,
                    "team_label": slot.team_label,
                    "frames": len(repaired_frames),
                    "start_frame": min(repaired_frames),
                    "end_frame": max(repaired_frames),
                }
            )
    return {
        "method": "confirmed_unmatched_raw_backfill_v1",
        "confirmation_frames": UNMATCHED_CONFIRMATION_FRAMES,
        "max_frame_gap": UNMATCHED_MAX_FRAME_GAP,
        "max_distance_m": UNMATCHED_REPAIR_MAX_DISTANCE_M,
        "backfilled_observations": backfilled_observations,
        "backfilled_tracklets": len(repaired_tracklets),
        "events": repair_events[:1000],
    }


def _assigned_observation_keys_by_frame(slots: list[SlotState]) -> dict[int, set[tuple[str, int]]]:
    assigned: dict[int, set[tuple[str, int]]] = defaultdict(set)
    for slot in slots:
        for row in slot.history:
            frame = int(row.get("frame") or 0)
            tracklet_id = row.get("tracklet_id") or row.get("candidate_tracklet_id")
            raw_track_id = row.get("raw_track_id") if row.get("raw_track_id") is not None else row.get("candidate_raw_track_id")
            if not tracklet_id or raw_track_id is None:
                continue
            assigned[frame].add((str(tracklet_id), int(raw_track_id)))
    return assigned


def _remaining_unmatched_observations(
    slots: list[SlotState],
    observations_by_frame: dict[int, list[Observation]],
) -> list[dict[str, Any]]:
    assigned_by_frame = _assigned_observation_keys_by_frame(slots)
    rows: list[dict[str, Any]] = []
    for frame in sorted(observations_by_frame):
        assigned = assigned_by_frame.get(frame, set())
        for obs in observations_by_frame[frame]:
            if (obs.tracklet_id, obs.raw_track_id) in assigned:
                continue
            if _stateless_detection_rejection_reason(obs) is not None:
                continue
            rows.append(_unmatched_observation_doc(obs))
    return rows


def _unmatched_observation_doc(obs: Observation) -> dict[str, Any]:
    return {
        "frame": obs.frame,
        "time_sec": round(obs.time_sec, 3),
        "bbox_xyxy": list(obs.bbox_xyxy),
        "footpoint": obs.footpoint,
        "pitch_m": _round_point(obs.pitch_m),
        "tracklet_id": obs.tracklet_id,
        "raw_track_id": obs.raw_track_id,
        "confidence": round(obs.confidence, 4),
        "team_label": obs.team_label,
        "team_id": obs.team_id,
        "team_name": obs.team_name,
        "team_confidence": round(obs.team_confidence, 4),
        "source": "unmatched_raw",
        "status": "unmatched_raw",
        "visual_trusted": False,
    }


def _consecutive_observation_runs(observations: list[Observation]) -> list[list[Observation]]:
    runs: list[list[Observation]] = []
    current: list[Observation] = []
    for obs in sorted(observations, key=lambda item: (item.frame, item.time_sec)):
        if current and obs.frame > current[-1].frame + UNMATCHED_MAX_FRAME_GAP:
            runs.append(current)
            current = []
        current.append(obs)
    if current:
        runs.append(current)
    return runs


def _select_unmatched_repair_slot(slots: list[SlotState], run: list[Observation]) -> SlotState | None:
    if not run or run[0].team_label not in TEAM_LABELS:
        return None
    team_label = run[0].team_label
    candidates: list[tuple[float, int, str, SlotState]] = []
    for slot in slots:
        if slot.team_label != team_label:
            continue
        missing_by_frame = _slot_missing_rows_by_frame(slot)
        overlap = [obs for obs in run if obs.frame in missing_by_frame]
        if len(overlap) < UNMATCHED_CONFIRMATION_FRAMES:
            continue
        owns_tracklet = any(obs.tracklet_id in slot.tracklet_ids for obs in run)
        distances = [
            distance
            for obs in overlap
            for distance in [_distance_m(missing_by_frame[obs.frame].get("pitch_m"), obs.pitch_m)]
            if distance is not None
        ]
        mean_distance = _mean(distances) if distances else UNMATCHED_REPAIR_MAX_DISTANCE_M
        min_distance = min(distances) if distances else 0.0
        if not owns_tracklet and min_distance > UNMATCHED_REPAIR_MAX_DISTANCE_M:
            continue
        continuity_bonus = -1000.0 if owns_tracklet else 0.0
        score = continuity_bonus + float(mean_distance or 0.0) - len(overlap) * 0.5
        candidates.append((score, -len(overlap), slot.slot_id, slot))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item[0], item[1], item[2]))[0][3]


def _slot_missing_rows_by_frame(slot: SlotState) -> dict[int, dict[str, Any]]:
    return {
        int(row.get("frame") or 0): row
        for row in slot.history
        if row.get("source") == "missing"
    }


def _assignment_cost(slot: SlotState, obs: Observation) -> float | None:
    team_mismatch = obs.team_label in TEAM_LABELS and obs.team_label != slot.team_label
    if team_mismatch and obs.tracklet_id not in slot.tracklet_ids:
        return None
    reference_time = slot.last_detected_time_sec
    reference_pitch = slot.predicted_pitch(obs.time_sec)
    reference_bbox = slot.last_bbox_xyxy
    if slot.pending_tracklet_id == obs.tracklet_id and slot.pending_observations:
        pending_obs = slot.pending_observations[-1]
        reference_time = pending_obs.time_sec
        reference_pitch = pending_obs.pitch_m
        reference_bbox = pending_obs.bbox_xyxy
    if reference_time is None:
        return None
    dt = max(0.001, obs.time_sec - reference_time)
    if dt < -0.001:
        return None
    distance = _distance_m(reference_pitch, obs.pitch_m)
    if distance is None:
        return None
    max_distance = min(MAX_ASSIGNMENT_DISTANCE_M, max(3.0, MAX_ASSIGNMENT_SPEED_MPS * max(dt, 0.3) * 1.25))
    if distance > max_distance:
        return None
    required_speed = distance / max(dt, 0.3)
    if required_speed > MAX_ASSIGNMENT_SPEED_MPS:
        return None
    team_penalty = 4.0 if team_mismatch else 2.0 if obs.team_label == "U" else 0.0
    continuity_discount = 0.0
    if obs.tracklet_id in slot.tracklet_ids:
        continuity_discount += 4.0
    if obs.raw_track_id in slot.raw_track_ids:
        continuity_discount += 1.0
    shape_penalty = _bbox_shape_penalty(reference_bbox, obs.bbox_xyxy)
    confidence_penalty = max(0.0, 1.0 - obs.confidence) * 2.0
    missing_penalty = min(4.0, dt / MAX_PREDICTION_SEC)
    return max(0.0, distance * 4.0 + shape_penalty + confidence_penalty + team_penalty + missing_penalty - continuity_discount)


def _conservative_assignment_guard(
    slot: SlotState,
    obs: Observation,
    frame_observations: list[Observation],
) -> dict[str, Any] | None:
    quality_reason = _trusted_detection_rejection_reason(slot, obs)
    if quality_reason is not None:
        slot.record_pending_tracklet(obs)
        return quality_reason
    if obs.tracklet_id in slot.tracklet_ids or obs.raw_track_id in slot.raw_track_ids:
        slot.clear_pending_tracklet(obs.tracklet_id)
        return None
    if slot.detected_frames < SWITCH_GUARD_MIN_DETECTIONS:
        return None

    pending_frames = slot.record_pending_tracklet(obs)
    conflict = _nearby_competing_observation(slot, obs, frame_observations)
    if pending_frames < SWITCH_CONFIRMATION_FRAMES:
        return {
            "reason": "tracklet_switch_needs_confirmation",
            "pending_frames": pending_frames,
            "required_frames": SWITCH_CONFIRMATION_FRAMES,
            "conflicting_tracklet_id": conflict.tracklet_id if conflict else None,
        }
    if conflict is not None:
        return {
            "reason": "tracklet_switch_has_nearby_competitor",
            "pending_frames": pending_frames,
            "required_frames": SWITCH_CONFIRMATION_FRAMES,
            "conflicting_tracklet_id": conflict.tracklet_id,
        }
    return None


def _trusted_detection_rejection_reason(slot: SlotState, obs: Observation) -> dict[str, Any] | None:
    stateless_rejection = _stateless_detection_rejection_reason(obs)
    if stateless_rejection is not None:
        return stateless_rejection
    if not slot._bbox_footpoint_inside_pitch_polygon(obs.bbox_xyxy, obs.calibrated_footpoint):
        return {"reason": "bbox_outside_pitch_polygon"}
    if slot.detected_frames < BBOX_OUTLIER_MIN_DETECTIONS:
        return None
    stats = _recent_detected_bbox_stats(slot)
    if stats is None:
        return None
    width, height = _bbox_size(obs.bbox_xyxy) or [1.0, 1.0]
    area = width * height
    median_width = max(1.0, stats["median_width"])
    median_height = max(1.0, stats["median_height"])
    median_area = max(1.0, stats["median_area"])
    width_ratio = width / median_width
    height_ratio = height / median_height
    area_ratio = area / median_area
    inverse_area_ratio = median_area / max(1.0, area)
    severe_outlier = (
        area_ratio > 4.0
        or inverse_area_ratio > 5.0
        or width_ratio > 2.4
        or height_ratio > 2.4
        or width_ratio < 0.35
        or height_ratio < 0.35
    )
    if not severe_outlier:
        return None
    return {
        "reason": "bbox_size_outlier",
        "bbox_area_ratio": round(area_ratio, 3),
        "bbox_width_ratio": round(width_ratio, 3),
        "bbox_height_ratio": round(height_ratio, 3),
    }


def _stateless_detection_rejection_reason(obs: Observation) -> dict[str, Any] | None:
    size = _bbox_size(obs.bbox_xyxy)
    if not size:
        return {"reason": "invalid_bbox"}
    width, height = size
    aspect_ratio = width / max(1.0, height)
    if aspect_ratio >= SHADOW_LIKE_MAX_ASPECT_RATIO and obs.confidence < SHADOW_LIKE_LOW_CONFIDENCE:
        return {
            "reason": "shadow_like_wide_low_confidence_bbox",
            "bbox_aspect_ratio": round(aspect_ratio, 3),
            "bbox_width": round(width, 1),
            "bbox_height": round(height, 1),
        }
    return None


def _recent_detected_bbox_stats(slot: SlotState) -> dict[str, float] | None:
    rows = [row for row in slot.history if row.get("status") == "detected" and row.get("bbox_xyxy")]
    if len(rows) < BBOX_OUTLIER_MIN_DETECTIONS:
        return None
    widths: list[float] = []
    heights: list[float] = []
    areas: list[float] = []
    for row in rows[-30:]:
        size = _bbox_size(row.get("bbox_xyxy"))
        if not size:
            continue
        widths.append(size[0])
        heights.append(size[1])
        areas.append(size[0] * size[1])
    if len(areas) < BBOX_OUTLIER_MIN_DETECTIONS:
        return None
    widths_sorted = sorted(widths)
    heights_sorted = sorted(heights)
    areas_sorted = sorted(areas)
    middle = len(areas_sorted) // 2
    return {
        "median_width": widths_sorted[middle],
        "median_height": heights_sorted[middle],
        "median_area": areas_sorted[middle],
    }


def _nearby_competing_observation(
    slot: SlotState,
    obs: Observation,
    frame_observations: list[Observation],
) -> Observation | None:
    for other in frame_observations:
        if other.tracklet_id == obs.tracklet_id:
            continue
        if other.tracklet_id in slot.tracklet_ids or other.raw_track_id in slot.raw_track_ids:
            continue
        distance = _distance_m(obs.pitch_m, other.pitch_m)
        if distance is not None and distance <= SWITCH_CONFLICT_RADIUS_M:
            return other
    return None


def _solve_assignment(cost_matrix: list[list[float]], *, cost_limit: float) -> list[tuple[int, int, float]]:
    if not cost_matrix or not cost_matrix[0]:
        return []
    try:
        import numpy as np
        import lap

        costs = np.array(cost_matrix, dtype=float)
        _, row_assignments, _ = lap.lapjv(costs, extend_cost=True, cost_limit=cost_limit)
        matches: list[tuple[int, int, float]] = []
        for row_index, col_index in enumerate(row_assignments):
            if col_index >= 0 and float(cost_matrix[row_index][int(col_index)]) <= cost_limit:
                matches.append((row_index, int(col_index), float(cost_matrix[row_index][int(col_index)])))
        return matches
    except Exception:
        return _greedy_assignment(cost_matrix, cost_limit=cost_limit)


def _greedy_assignment(cost_matrix: list[list[float]], *, cost_limit: float) -> list[tuple[int, int, float]]:
    candidates: list[tuple[float, int, int]] = []
    for row_index, row in enumerate(cost_matrix):
        for col_index, cost in enumerate(row):
            if cost <= cost_limit:
                candidates.append((float(cost), row_index, col_index))
    assigned_rows: set[int] = set()
    assigned_cols: set[int] = set()
    matches: list[tuple[int, int, float]] = []
    for cost, row_index, col_index in sorted(candidates):
        if row_index in assigned_rows or col_index in assigned_cols:
            continue
        assigned_rows.add(row_index)
        assigned_cols.add(col_index)
        matches.append((row_index, col_index, cost))
    return matches


def _select_inactive_slot(slots: list[SlotState], obs: Observation) -> tuple[SlotState, bool] | None:
    active_counts = {
        team_label: sum(1 for slot in slots if slot.team_label == team_label and slot.active)
        for team_label in TEAM_LABELS
    }
    preferred = obs.team_label if obs.team_label in TEAM_LABELS else _least_loaded_team(slots)
    if active_counts.get(preferred, 0) < TARGET_PLAYERS_PER_TEAM:
        slot = _first_inactive_slot(slots, preferred, prefer_unused=True) or _first_inactive_slot(slots, preferred, prefer_unused=False)
        if slot is not None:
            return slot, False
    for team_label in sorted(TEAM_LABELS, key=lambda label: active_counts[label]):
        if active_counts[team_label] >= TARGET_PLAYERS_PER_TEAM:
            continue
        slot = _first_inactive_slot(slots, team_label, prefer_unused=True) or _first_inactive_slot(slots, team_label, prefer_unused=False)
        if slot is not None:
            return slot, obs.team_label in TEAM_LABELS and obs.team_label != team_label
    return None


def _first_inactive_slot(slots: list[SlotState], team_label: str, *, prefer_unused: bool) -> SlotState | None:
    for slot in slots:
        if slot.team_label != team_label or slot.active:
            continue
        if prefer_unused and slot.detected_frames > 0:
            continue
        if not prefer_unused and _has_unused_slot(slots, team_label):
            continue
        return slot
    return None


def _has_unused_slot(slots: list[SlotState], team_label: str) -> bool:
    return any(slot.team_label == team_label and not slot.active and slot.detected_frames == 0 for slot in slots)


def _observation_for_slot(obs: Observation, slot: SlotState) -> Observation:
    return replace(
        obs,
        team_label=slot.team_label,
        team_id=slot.team_id,
        team_name=slot.team_name,
        team_confidence=min(obs.team_confidence or 0.0, 0.45),
    )


def _least_loaded_team(slots: list[SlotState]) -> str:
    counts = {
        team_label: sum(1 for slot in slots if slot.team_label == team_label and slot.active)
        for team_label in TEAM_LABELS
    }
    return "A" if counts["A"] <= counts["B"] else "B"


def _build_identity_frame_rows(
    slots: list[SlotState],
    observations_by_frame: dict[int, list[Observation]],
    max_frame: int,
    fps: float,
) -> list[dict[str, Any]]:
    status_by_frame: dict[int, list[tuple[str, str]]] = defaultdict(list)
    trusted_visible_by_frame: dict[int, int] = defaultdict(int)
    predicted_visible_by_frame: dict[int, int] = defaultdict(int)
    assigned_by_frame = _assigned_observation_keys_by_frame(slots)
    for slot in slots:
        for row in slot.history:
            frame = int(row["frame"])
            status = str(row.get("status") or "missing")
            status_by_frame[frame].append((slot.team_label, status))
            if status == "detected" and row.get("bbox_xyxy") and row.get("visual_trusted") is not False:
                trusted_visible_by_frame[frame] += 1
            if status in {"predicted", "interpolated"} and row.get("bbox_xyxy"):
                predicted_visible_by_frame[frame] += 1
    frames: list[dict[str, Any]] = []
    for frame in range(max_frame + 1):
        statuses = status_by_frame.get(frame, [])
        raw_detections = len(observations_by_frame.get(frame, []))
        matched_raw = len(assigned_by_frame.get(frame, set()))
        unmatched_raw = max(0, raw_detections - matched_raw)
        slot_detected = sum(1 for _, status in statuses if status == "detected")
        slot_predicted = sum(1 for _, status in statuses if status == "predicted")
        slot_missing = sum(1 for _, status in statuses if status == "missing")
        slot_ambiguous = sum(1 for _, status in statuses if status == "ambiguous")
        active_slots = slot_detected + slot_predicted + slot_missing + slot_ambiguous
        active_team_a = sum(
            1 for team, status in statuses if team == "A" and status in {"detected", "predicted", "missing", "ambiguous"}
        )
        active_team_b = sum(
            1 for team, status in statuses if team == "B" and status in {"detected", "predicted", "missing", "ambiguous"}
        )
        frames.append(
            {
                "frame": frame,
                "time_sec": round(frame / max(fps, 0.001), 3),
                "raw_detections": raw_detections,
                "raw_matched_to_slots": matched_raw,
                "unmatched_raw_detections": unmatched_raw,
                "slot_detected": slot_detected,
                "slot_predicted": slot_predicted,
                "slot_missing": slot_missing,
                "slot_ambiguous": slot_ambiguous,
                "trusted_detected": trusted_visible_by_frame.get(frame, 0),
                "visible_stable_boxes": trusted_visible_by_frame.get(frame, 0),
                "predicted_visible_boxes": predicted_visible_by_frame.get(frame, 0),
                "ambiguous_slots": slot_ambiguous,
                "missing_slots": slot_missing,
                "active_slots": active_slots,
                "active_team_a": active_team_a,
                "active_team_b": active_team_b,
            }
        )
    return frames


def _slot_to_doc(slot: SlotState, fps: float) -> dict[str, Any]:
    detected_rows = [row for row in slot.history if row.get("source") == "detected"]
    overlay_rows = [
        row
        for row in slot.history
        if row.get("bbox_xyxy") is not None and row.get("source") in {"detected", "ambiguous"}
    ]
    confidence_score = _slot_confidence(slot)
    first_time = slot.first_time_sec or 0.0
    last_time = slot.last_time_sec if slot.last_time_sec is not None else first_time
    appearance_rgb = _average_rgb(slot.appearance_samples)
    movement_stats = _slot_movement_stats(slot.history, fps)
    stints = []
    for stint in slot.stints:
        stints.append(
            {
                **stint,
                "duration_sec": round(max(0.0, float(stint.get("end_time_sec") or 0.0) - float(stint.get("start_time_sec") or 0.0)), 3),
            }
        )
    return {
        "slot_id": slot.slot_id,
        "stable_subject_id": slot.stable_subject_id,
        "stable_player_id": slot.slot_id,
        "identity_semantics": "stint_first",
        "status": "active" if detected_rows else "unknown",
        "team_label": slot.team_label,
        "team_id": slot.team_id,
        "team_name": slot.team_name or f"Team {slot.team_label}",
        "team_confidence": round(_mean(slot.team_confidences) or 0.0, 4),
        "confidence": confidence_level(confidence_score),
        "confidence_score": round(confidence_score, 4),
        "duration_sec": round(max(0.0, last_time - first_time), 3),
        "start_time_sec": round(first_time, 3),
        "end_time_sec": round(last_time, 3),
        "tracklet_ids": sorted(slot.tracklet_ids),
        "raw_track_ids": sorted(slot.raw_track_ids),
        "tracklet_count": len(slot.tracklet_ids),
        "positions_count": len(detected_rows),
        "real_positions_count": len(detected_rows),
        "overlay_positions_count": len(overlay_rows),
        "trusted_overlay_positions_count": len(detected_rows),
        "detected_frames": slot.detected_frames,
        "predicted_frames": slot.predicted_frames,
        "missing_frames": slot.missing_frames,
        "ambiguous_frames": slot.ambiguous_frames,
        "interpolated_positions_count": slot.predicted_frames,
        "interpolated_gaps_count": len([stint for stint in stints if int(stint.get("predicted_frames") or 0) > 0]),
        "skipped_interpolation_gaps_count": 0,
        "longest_interpolated_gap_frames": _longest_consecutive_source(slot.history, "predicted"),
        "mean_detection_confidence": round(_mean(slot.detection_confidences) or 0.0, 4),
        "jersey_color_hex": _rgb_to_hex(appearance_rgb),
        "movement_stats": movement_stats,
        "trajectory_m": _downsample_trajectory(slot.history),
        "overlay_positions": _overlay_positions(overlay_rows),
        "stints": stints,
        "stint_count": len(stints),
        "blocked_team_switches": slot.blocked_team_switches,
        "blocked_identity_switches": slot.blocked_identity_switches,
        "suspicious_assignments": slot.suspicious_assignments,
        "rejected_candidates": slot.rejected_candidates,
        "identity_events": slot.identity_events,
        "risky_links": slot.suspicious_assignments,
    }


def _slot_movement_stats(history: list[dict[str, Any]], fps: float) -> dict[str, Any]:
    rows = sorted(history, key=lambda item: (int(item.get("frame") or 0), float(item.get("time_sec") or 0.0)))
    active_rows = [row for row in rows if row.get("source") in {"detected", "missing", "ambiguous", "predicted"}]
    detected_rows = [row for row in rows if row.get("source") == "detected" and row.get("pitch_m")]
    fps_safe = max(float(fps or 0.0), 0.001)
    active_frames = len(active_rows)
    detected_frames = len(detected_rows)
    ambiguous_frames = sum(1 for row in active_rows if row.get("source") == "ambiguous")
    missing_frames = sum(1 for row in active_rows if row.get("source") == "missing")
    predicted_frames = sum(1 for row in active_rows if row.get("source") == "predicted")
    playing_time_sec = active_frames / fps_safe
    detected_time_sec = detected_frames / fps_safe
    ambiguous_time_sec = ambiguous_frames / fps_safe
    missing_time_sec = missing_frames / fps_safe

    observed_distance = 0.0
    estimated_gap_distance = 0.0
    observed_segments = 0
    estimated_gap_segments = 0
    skipped_outlier_segments = 0
    skipped_long_gap_segments = 0
    segment_speeds: list[float] = []
    speed_segments: list[dict[str, Any]] = []

    for previous, current in zip(detected_rows, detected_rows[1:]):
        previous_point = previous.get("pitch_m")
        current_point = current.get("pitch_m")
        distance = _distance_m(previous_point, current_point)
        if distance is None:
            continue
        previous_frame = int(previous.get("frame") or 0)
        current_frame = int(current.get("frame") or 0)
        frame_gap = max(1, current_frame - previous_frame)
        previous_time = float(previous.get("time_sec") or previous_frame / fps_safe)
        current_time = float(current.get("time_sec") or current_frame / fps_safe)
        dt = max(1.0 / fps_safe, current_time - previous_time)
        speed = distance / dt
        if speed > MAX_STATS_SPEED_MPS:
            skipped_outlier_segments += 1
            continue
        speed_segments.append(
            {
                "start_frame": previous_frame,
                "end_frame": current_frame,
                "start_time": previous_time,
                "end_time": current_time,
                "dt": dt,
                "distance": distance,
                "speed": speed,
                "frame_gap": frame_gap,
                "start_point": previous_point,
                "end_point": current_point,
            }
        )
        if frame_gap <= STATS_OBSERVED_GAP_FRAMES:
            observed_distance += distance
            observed_segments += 1
            segment_speeds.append(speed)
        elif dt <= MAX_STATS_ESTIMATED_GAP_SEC:
            estimated_gap_distance += distance
            estimated_gap_segments += 1
            segment_speeds.append(speed)
        else:
            skipped_long_gap_segments += 1

    total_distance = observed_distance + estimated_gap_distance
    peak_speed, sustained_windows = _peak_sustained_speed_mps(detected_rows, speed_segments, fps_safe)
    raw_segment_top_speed = max(segment_speeds) if segment_speeds else 0.0
    avg_speed = total_distance / playing_time_sec if playing_time_sec > 0 else 0.0
    observed_avg_speed = observed_distance / detected_time_sec if detected_time_sec > 0 else 0.0
    detected_coverage = detected_frames / max(1, active_frames)
    estimated_ratio = estimated_gap_distance / total_distance if total_distance > 0 else 0.0
    distance_quality = _movement_quality(detected_coverage, estimated_ratio, skipped_outlier_segments)
    speed_quality = _speed_quality(
        detected_coverage,
        peak_speed,
        raw_segment_top_speed,
        skipped_outlier_segments,
        sustained_windows,
        detected_time_sec,
    )
    intensity = _intensity_metrics(speed_segments, total_distance)

    return {
        "playing_time_sec": round(playing_time_sec, 3),
        "detected_time_sec": round(detected_time_sec, 3),
        "missing_time_sec": round(missing_time_sec, 3),
        "ambiguous_time_sec": round(ambiguous_time_sec, 3),
        "observed_distance_m": round(observed_distance, 2),
        "estimated_gap_distance_m": round(estimated_gap_distance, 2),
        "total_distance_m": round(total_distance, 2),
        "avg_speed_mps": round(avg_speed, 3),
        "avg_speed_kmh": round(avg_speed * 3.6, 2),
        "observed_avg_speed_mps": round(observed_avg_speed, 3),
        "peak_sustained_speed_mps": round(peak_speed, 3),
        "peak_sustained_speed_kmh": round(peak_speed * 3.6, 2),
        "top_speed_mps": round(peak_speed, 3),
        "top_speed_kmh": round(peak_speed * 3.6, 2),
        "raw_segment_top_speed_mps": round(raw_segment_top_speed, 3),
        "raw_segment_top_speed_kmh": round(raw_segment_top_speed * 3.6, 2),
        "detected_coverage": round(detected_coverage, 4),
        "estimated_distance_ratio": round(estimated_ratio, 4),
        "distance_quality": distance_quality,
        "speed_quality": speed_quality,
        "speed_window_sec": STATS_PEAK_SPEED_MIN_WINDOW_SEC,
        "samples_used": detected_frames,
        "active_frames": active_frames,
        "detected_frames": detected_frames,
        "missing_frames": missing_frames,
        "ambiguous_frames": ambiguous_frames,
        "predicted_frames": predicted_frames,
        "observed_segments": observed_segments,
        "estimated_gap_segments": estimated_gap_segments,
        "skipped_outlier_segments": skipped_outlier_segments,
        "skipped_speed_outlier_segments": skipped_outlier_segments,
        "skipped_long_gap_segments": skipped_long_gap_segments,
        "sustained_speed_windows": sustained_windows,
        "intensity": intensity,
        "stats_note": "distance uses trusted detected pitch positions; top_speed is peak_sustained_speed over a conservative window; short gaps are counted separately as estimated_gap_distance_m; sprint/high-intensity metrics use trusted short-gap detected segments only",
    }


def _peak_sustained_speed_mps(
    detected_rows: list[dict[str, Any]],
    speed_segments: list[dict[str, Any]],
    fps: float,
) -> tuple[float, int]:
    best = 0.0
    windows = 0
    segment_by_pair = {
        (int(segment["start_frame"]), int(segment["end_frame"])): segment
        for segment in speed_segments
    }
    for start_index, start in enumerate(detected_rows):
        start_frame = int(start.get("frame") or 0)
        start_time = float(start.get("time_sec") or start_frame / fps)
        start_point = start.get("pitch_m")
        previous = start
        for end in detected_rows[start_index + 1 :]:
            end_frame = int(end.get("frame") or 0)
            end_time = float(end.get("time_sec") or end_frame / fps)
            previous_frame = int(previous.get("frame") or 0)
            segment = segment_by_pair.get((previous_frame, end_frame))
            if not segment or float(segment.get("dt") or 0.0) > STATS_PEAK_SPEED_MAX_SEGMENT_GAP_SEC:
                break
            if float(segment.get("speed") or 0.0) > MAX_STATS_SUSTAINED_SPEED_MPS:
                break
            dt = end_time - start_time
            if dt < STATS_PEAK_SPEED_MIN_WINDOW_SEC:
                previous = end
                continue
            if dt > STATS_PEAK_SPEED_MAX_WINDOW_SEC:
                break
            distance = _distance_m(start_point, end.get("pitch_m"))
            if distance is None:
                previous = end
                continue
            speed = distance / max(dt, 0.001)
            windows += 1
            if speed <= MAX_STATS_SUSTAINED_SPEED_MPS:
                best = max(best, speed)
            previous = end
    return best, windows


def _intensity_metrics(speed_segments: list[dict[str, Any]], total_distance_m: float) -> dict[str, Any]:
    high_threshold_mps = HIGH_INTENSITY_THRESHOLD_KMH / 3.6
    sprint_threshold_mps = SPRINT_THRESHOLD_KMH / 3.6
    trusted_segments = [
        segment
        for segment in speed_segments
        if int(segment.get("frame_gap") or 0) <= STATS_OBSERVED_GAP_FRAMES
        and float(segment.get("dt") or 0.0) <= STATS_PEAK_SPEED_MAX_SEGMENT_GAP_SEC
    ]
    rejected_gap_candidates = [
        {
            "start_frame": int(segment.get("start_frame") or 0),
            "end_frame": int(segment.get("end_frame") or 0),
            "start_time_sec": float(segment.get("start_time") or 0.0),
            "end_time_sec": float(segment.get("end_time") or 0.0),
            "time_sec": float(segment.get("dt") or 0.0),
            "distance_m": float(segment.get("distance") or 0.0),
            "max_speed_mps": float(segment.get("speed") or 0.0),
            "reason": "gap_too_large",
        }
        for segment in speed_segments
        if float(segment.get("speed") or 0.0) >= sprint_threshold_mps
        and (
            int(segment.get("frame_gap") or 0) > STATS_OBSERVED_GAP_FRAMES
            or float(segment.get("dt") or 0.0) > STATS_PEAK_SPEED_MAX_SEGMENT_GAP_SEC
        )
    ]

    high_time = 0.0
    high_distance = 0.0
    high_segments = 0
    for segment in trusted_segments:
        if float(segment.get("speed") or 0.0) < high_threshold_mps:
            continue
        high_time += float(segment.get("dt") or 0.0)
        high_distance += float(segment.get("distance") or 0.0)
        high_segments += 1

    sprint_runs: list[dict[str, Any]] = []
    current_run: dict[str, Any] | None = None
    previous_end_frame: int | None = None
    for segment in trusted_segments:
        speed = float(segment.get("speed") or 0.0)
        start_frame = int(segment.get("start_frame") or 0)
        end_frame = int(segment.get("end_frame") or 0)
        continues_previous = previous_end_frame is not None and start_frame == previous_end_frame
        if speed >= sprint_threshold_mps:
            if current_run is None or not continues_previous:
                if current_run is not None:
                    sprint_runs.append(current_run)
                current_run = {
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "start_time_sec": float(segment.get("start_time") or 0.0),
                    "end_time_sec": float(segment.get("end_time") or 0.0),
                    "time_sec": 0.0,
                    "distance_m": 0.0,
                    "max_speed_mps": 0.0,
                    "reason": "accepted",
                }
            current_run["time_sec"] += float(segment.get("dt") or 0.0)
            current_run["distance_m"] += float(segment.get("distance") or 0.0)
            current_run["max_speed_mps"] = max(current_run["max_speed_mps"], speed)
            current_run["end_frame"] = end_frame
            current_run["end_time_sec"] = float(segment.get("end_time") or current_run["end_time_sec"])
        elif current_run is not None:
            sprint_runs.append(current_run)
            current_run = None
        previous_end_frame = end_frame
    if current_run is not None:
        sprint_runs.append(current_run)

    valid_sprints = [run for run in sprint_runs if float(run.get("time_sec") or 0.0) >= SPRINT_MIN_DURATION_SEC]
    rejected_sprints = [
        {**run, "reason": "too_short"}
        for run in sprint_runs
        if float(run.get("time_sec") or 0.0) < SPRINT_MIN_DURATION_SEC
    ] + rejected_gap_candidates
    sprint_candidates = [*valid_sprints, *rejected_sprints]
    sprint_time = sum(float(run.get("time_sec") or 0.0) for run in valid_sprints)
    sprint_distance = sum(float(run.get("distance_m") or 0.0) for run in valid_sprints)
    longest = max(valid_sprints, key=lambda run: float(run.get("distance_m") or 0.0), default={})
    max_sprint_speed = max([float(run.get("max_speed_mps") or 0.0) for run in valid_sprints] or [0.0])
    best_candidate = max(
        sprint_candidates,
        key=lambda run: (
            float(run.get("max_speed_mps") or 0.0),
            float(run.get("time_sec") or 0.0),
            float(run.get("distance_m") or 0.0),
        ),
        default={},
    )
    best_rejected = max(
        rejected_sprints,
        key=lambda run: (
            float(run.get("max_speed_mps") or 0.0),
            float(run.get("time_sec") or 0.0),
            float(run.get("distance_m") or 0.0),
        ),
        default={},
    )

    def serialize_candidate(run: dict[str, Any]) -> dict[str, Any]:
        if not run:
            return {}
        return {
            "start_frame": int(run.get("start_frame") or 0),
            "end_frame": int(run.get("end_frame") or 0),
            "start_time_sec": round(float(run.get("start_time_sec") or 0.0), 3),
            "end_time_sec": round(float(run.get("end_time_sec") or 0.0), 3),
            "duration_sec": round(float(run.get("time_sec") or 0.0), 3),
            "distance_m": round(float(run.get("distance_m") or 0.0), 2),
            "max_speed_kmh": round(float(run.get("max_speed_mps") or 0.0) * 3.6, 2),
            "reason": str(run.get("reason") or "accepted"),
        }

    return {
        "high_intensity_threshold_kmh": HIGH_INTENSITY_THRESHOLD_KMH,
        "sprint_threshold_kmh": SPRINT_THRESHOLD_KMH,
        "min_sprint_duration_sec": SPRINT_MIN_DURATION_SEC,
        "high_intensity_time_sec": round(high_time, 3),
        "high_intensity_distance_m": round(high_distance, 2),
        "high_intensity_segments": high_segments,
        "high_intensity_distance_ratio": round(high_distance / total_distance_m, 4) if total_distance_m > 0 else 0.0,
        "sprint_count": len(valid_sprints),
        "sprint_time_sec": round(sprint_time, 3),
        "sprint_distance_m": round(sprint_distance, 2),
        "sprint_distance_ratio": round(sprint_distance / total_distance_m, 4) if total_distance_m > 0 else 0.0,
        "longest_sprint_time_sec": round(float(longest.get("time_sec") or 0.0), 3),
        "longest_sprint_distance_m": round(float(longest.get("distance_m") or 0.0), 2),
        "max_sprint_speed_kmh": round(max_sprint_speed * 3.6, 2),
        "trusted_speed_segments": len(trusted_segments),
        "sprint_candidate_count": len(sprint_candidates),
        "rejected_sprint_candidate_count": len(rejected_sprints),
        "best_sprint_candidate_speed_kmh": round(float(best_candidate.get("max_speed_mps") or 0.0) * 3.6, 2),
        "best_sprint_candidate_duration_sec": round(float(best_candidate.get("time_sec") or 0.0), 3),
        "best_sprint_candidate_distance_m": round(float(best_candidate.get("distance_m") or 0.0), 2),
        "best_sprint_candidate_reason": str(best_candidate.get("reason") or "none") if best_candidate else "none",
        "best_rejected_sprint_candidate": serialize_candidate(best_rejected),
        "rejected_sprint_candidates": [serialize_candidate(run) for run in rejected_sprints[:5]],
    }


def _speed_quality(
    detected_coverage: float,
    peak_speed_mps: float,
    raw_segment_top_speed_mps: float,
    skipped_outlier_segments: int,
    sustained_windows: int,
    detected_time_sec: float,
) -> str:
    if detected_time_sec >= STATS_PEAK_SPEED_MIN_WINDOW_SEC and sustained_windows == 0:
        return "low"
    if skipped_outlier_segments > 2:
        return "low"
    if peak_speed_mps <= 0.0 and raw_segment_top_speed_mps > 0.0:
        return "low"
    if raw_segment_top_speed_mps - peak_speed_mps > 2.0:
        return "medium"
    if skipped_outlier_segments > 0 or detected_coverage < 0.7 or sustained_windows < 2:
        return "medium"
    return "high"


def _movement_quality(detected_coverage: float, estimated_ratio: float, skipped_outlier_segments: int) -> str:
    if skipped_outlier_segments > 3 or detected_coverage < 0.45 or estimated_ratio > 0.45:
        return "low"
    if skipped_outlier_segments > 0 or detected_coverage < 0.7 or estimated_ratio > 0.25:
        return "medium"
    return "high"


def _slot_confidence(slot: SlotState) -> float:
    detected = slot.detected_frames
    predicted = slot.predicted_frames
    missing = slot.missing_frames
    ambiguous = slot.ambiguous_frames
    total = max(1, detected + predicted + missing + ambiguous)
    coverage = detected / total
    team_score = _mean(slot.team_confidences) or 0.35
    detection_score = _mean(slot.detection_confidences) or 0.35
    penalty = min(0.35, len(slot.suspicious_assignments) * 0.05 + slot.blocked_team_switches * 0.08)
    return max(0.0, min(1.0, coverage * 0.45 + team_score * 0.35 + detection_score * 0.2 - penalty))


def _average_rgb(samples: list[list[float]]) -> list[float] | None:
    if not samples:
        return None
    return [round(_mean([sample[idx] for sample in samples]) or 0.0, 2) for idx in range(3)]


def _longest_consecutive_source(history: list[dict[str, Any]], source: str) -> int:
    longest = 0
    current = 0
    previous_frame: int | None = None
    for row in sorted(history, key=lambda item: int(item.get("frame") or 0)):
        frame = int(row.get("frame") or 0)
        if row.get("source") == source and (previous_frame is None or frame == previous_frame + 1):
            current += 1
        elif row.get("source") == source:
            current = 1
        else:
            current = 0
        longest = max(longest, current)
        previous_frame = frame
    return longest


def _downsample_trajectory(history: list[dict[str, Any]], max_points: int = 180) -> list[dict[str, Any]]:
    rows = [row for row in history if row.get("pitch_m")]
    if not rows:
        return []
    if len(rows) <= max_points:
        sampled = rows
    else:
        indices = sorted({round(idx * (len(rows) - 1) / (max_points - 1)) for idx in range(max_points)})
        sampled = [rows[index] for index in indices]
    return [
        {
            "frame": int(row.get("frame") or 0),
            "time_sec": round(float(row.get("time_sec") or 0.0), 3),
            "pitch_m": _round_point(row.get("pitch_m")),
            "source": row.get("source") or "detected",
            "status": row.get("status") or row.get("source") or "detected",
        }
        for row in sampled
    ]


def _overlay_positions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "frame": int(row.get("frame") or 0),
            "time_sec": round(float(row.get("time_sec") or 0.0), 3),
            "bbox_xyxy": row.get("bbox_xyxy"),
            "pitch_m": row.get("pitch_m"),
            "tracklet_id": row.get("tracklet_id"),
            "raw_track_id": row.get("raw_track_id"),
            "confidence": row.get("confidence"),
            "source": row.get("source") or "detected",
            "status": row.get("status") or row.get("source") or "detected",
            "visual_trusted": row.get("visual_trusted"),
            "ambiguous_reason": row.get("ambiguous_reason"),
            "repair_reason": row.get("repair_reason"),
            "repaired_from": row.get("repaired_from"),
            "candidate_tracklet_id": row.get("candidate_tracklet_id"),
            "candidate_raw_track_id": row.get("candidate_raw_track_id"),
            "stint_id": row.get("stint_id"),
        }
        for row in rows
    ]


def _team_counts(slots: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for slot in slots:
        label = str(slot.get("team_label") or "U")
        counts[label] = counts.get(label, 0) + 1
    return counts


def build_stable_players_from_global_identity(identity_doc: dict[str, Any]) -> dict[str, Any]:
    players = []
    resolver_version = str(identity_doc.get("resolver_version") or "conservative_identity_v2")
    for slot in identity_doc.get("slots", []):
        player = dict(slot)
        player["source"] = resolver_version
        players.append(player)
    summary = dict(identity_doc.get("summary") or {})
    summary["stable_player_candidates"] = len(players)
    summary["suppressed_extra_candidates"] = 0
    summary["interpolated_frames"] = int(summary.get("predicted_frames") or 0)
    summary["interpolated_gaps"] = sum(int(player.get("stint_count") or 0) for player in players)
    summary["skipped_interpolation_gaps"] = 0
    summary["players_with_interpolation"] = sum(1 for player in players if int(player.get("predicted_frames") or 0) > 0)
    summary["longest_interpolated_gap_frames"] = 0
    return {
        "schema_version": "0.2.0",
        "generated_at": identity_doc.get("generated_at") or now_iso(),
        "source": resolver_version,
        "identity_semantics": "stint_first",
        "pitch_dimensions_m": identity_doc.get("pitch_dimensions_m"),
        "players": sorted(players, key=lambda item: item["stable_player_id"]),
        "suppressed_candidates": [],
        "unmatched_observations": identity_doc.get("unmatched_observations") or [],
        "summary": summary,
    }


def build_frame_detection_counts_from_global_identity(
    identity_doc: dict[str, Any],
    *,
    fps: float,
    target_players: int = TARGET_ACTIVE_PLAYERS,
) -> dict[str, Any]:
    frames = []
    for frame in identity_doc.get("frames", []):
        raw = int(frame.get("raw_detections") or 0)
        slot_detected = int(frame.get("slot_detected") or 0)
        slot_predicted = int(frame.get("slot_predicted") or 0)
        slot_missing = int(frame.get("slot_missing") or 0)
        slot_ambiguous = int(frame.get("slot_ambiguous") or 0)
        active_slots = int(frame.get("active_slots") or 0)
        visible_stable_boxes = int(frame.get("visible_stable_boxes") or slot_detected)
        predicted_visible_boxes = int(frame.get("predicted_visible_boxes") or 0)
        stable_total = visible_stable_boxes
        frames.append(
            {
                **frame,
                "time_sec": round(float(frame.get("frame") or 0) / max(fps, 0.001), 3),
                "stable_detected": visible_stable_boxes,
                "stable_interpolated": predicted_visible_boxes,
                "stable_total": stable_total,
                "trusted_detected": visible_stable_boxes,
                "visible_stable_boxes": visible_stable_boxes,
                "predicted_visible_boxes": predicted_visible_boxes,
                "ambiguous_slots": slot_ambiguous,
                "missing_slots": slot_missing,
                "raw_missing_vs_target": max(0, target_players - raw),
                "stable_missing_vs_target": max(0, target_players - stable_total),
                "slot_missing_vs_target": max(0, target_players - active_slots),
                "raw_extra_vs_target": max(0, raw - target_players),
            }
        )
    raw_values = [int(frame.get("raw_detections") or 0) for frame in frames]
    stable_values = [int(frame.get("stable_total") or 0) for frame in frames]
    active_values = [int(frame.get("active_slots") or 0) for frame in frames]
    predicted_values = [int(frame.get("slot_predicted") or 0) for frame in frames]
    missing_values = [int(frame.get("slot_missing") or 0) for frame in frames]
    ambiguous_values = [int(frame.get("slot_ambiguous") or 0) for frame in frames]
    predicted_visible_values = [int(frame.get("predicted_visible_boxes") or 0) for frame in frames]
    return {
        "schema_version": "0.2.0",
        "generated_at": now_iso(),
        "source": identity_doc.get("resolver_version") or "conservative_identity_v2",
        "target_players": target_players,
        "summary": {
            "frames": len(frames),
            "raw_min": min(raw_values) if raw_values else 0,
            "raw_max": max(raw_values) if raw_values else 0,
            "raw_avg": round(_mean([float(value) for value in raw_values]) or 0.0, 3),
            "stable_min": min(stable_values) if stable_values else 0,
            "stable_max": max(stable_values) if stable_values else 0,
            "stable_avg": round(_mean([float(value) for value in stable_values]) or 0.0, 3),
            "active_slots_min": min(active_values) if active_values else 0,
            "active_slots_max": max(active_values) if active_values else 0,
            "active_slots_avg": round(_mean([float(value) for value in active_values]) or 0.0, 3),
            "predicted_max": max(predicted_values) if predicted_values else 0,
            "missing_max": max(missing_values) if missing_values else 0,
            "ambiguous_max": max(ambiguous_values) if ambiguous_values else 0,
            "predicted_visible_boxes": sum(predicted_visible_values),
            "ghost_bbox_count": sum(predicted_visible_values),
            "raw_frames_below_target": sum(1 for value in raw_values if value < target_players),
            "stable_frames_below_target": sum(1 for value in stable_values if value < target_players),
            "active_slots_frames_below_target": sum(1 for value in active_values if value < target_players),
            "raw_frames_at_or_above_target": sum(1 for value in raw_values if value >= target_players),
            "stable_frames_at_or_above_target": sum(1 for value in stable_values if value >= target_players),
            "active_slots_frames_at_or_above_target": sum(1 for value in active_values if value >= target_players),
            "frames_with_predictions": sum(1 for value in predicted_values if value > 0),
            "frames_with_missing_slots": sum(1 for value in missing_values if value > 0),
            "frames_with_ambiguous_slots": sum(1 for value in ambiguous_values if value > 0),
        },
        "frames": frames,
    }


def build_global_identity_report(
    identity_doc: dict[str, Any],
    frame_detection_counts: dict[str, Any],
    *,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    summary = dict(identity_doc.get("summary") or {})
    frame_summary = frame_detection_counts.get("summary") if isinstance(frame_detection_counts.get("summary"), dict) else {}
    rejected_start_candidates = identity_doc.get("rejected_start_candidates")
    if not isinstance(rejected_start_candidates, list):
        rejected_start_candidates = []
    problem_frames = [
        {
            "frame": frame.get("frame"),
            "time_sec": frame.get("time_sec"),
            "raw_detections": frame.get("raw_detections"),
            "active_slots": frame.get("active_slots"),
            "slot_detected": frame.get("slot_detected"),
            "slot_predicted": frame.get("slot_predicted"),
            "slot_missing": frame.get("slot_missing"),
            "slot_ambiguous": frame.get("slot_ambiguous"),
            "visible_stable_boxes": frame.get("visible_stable_boxes"),
            "predicted_visible_boxes": frame.get("predicted_visible_boxes"),
        }
        for frame in frame_detection_counts.get("frames", [])
        if int(frame.get("visible_stable_boxes") or 0) < TARGET_ACTIVE_PLAYERS
        or int(frame.get("slot_missing") or 0) > 0
        or int(frame.get("slot_ambiguous") or 0) > 0
    ][:300]
    risky_slots = [
        {
            "slot_id": slot.get("slot_id"),
            "team_label": slot.get("team_label"),
            "confidence": slot.get("confidence"),
            "blocked_team_switches": slot.get("blocked_team_switches"),
            "blocked_identity_switches": slot.get("blocked_identity_switches"),
            "suspicious_assignments": slot.get("suspicious_assignments"),
            "rejected_candidates": slot.get("rejected_candidates"),
            "detected_frames": slot.get("detected_frames"),
            "predicted_frames": slot.get("predicted_frames"),
            "missing_frames": slot.get("missing_frames"),
            "ambiguous_frames": slot.get("ambiguous_frames"),
        }
        for slot in identity_doc.get("slots", [])
        if slot.get("confidence") == "low"
        or slot.get("blocked_team_switches")
        or slot.get("blocked_identity_switches")
        or slot.get("suspicious_assignments")
        or int(slot.get("missing_frames") or 0) > 0
        or int(slot.get("ambiguous_frames") or 0) > 0
    ]
    blocked_switches = [
        {"slot_id": slot.get("slot_id"), **event}
        for slot in identity_doc.get("slots", [])
        for event in (slot.get("rejected_candidates") or [])
    ]
    ambiguous_frames = [
        int(frame.get("frame") or 0)
        for frame in frame_detection_counts.get("frames", [])
        if int(frame.get("slot_ambiguous") or 0) > 0
    ]
    low_visible_frames = [
        {
            "frame": frame.get("frame"),
            "time_sec": frame.get("time_sec"),
            "visible_stable_boxes": frame.get("visible_stable_boxes"),
            "raw_detections": frame.get("raw_detections"),
            "slot_ambiguous": frame.get("slot_ambiguous"),
            "slot_missing": frame.get("slot_missing"),
        }
        for frame in frame_detection_counts.get("frames", [])
        if int(frame.get("visible_stable_boxes") or 0) < TARGET_ACTIVE_PLAYERS
    ][:300]
    return {
        "schema_version": "0.1.0",
        "generated_at": now_iso(),
        "status": "completed",
        "resolver_version": identity_doc.get("resolver_version"),
        "identity_semantics": identity_doc.get("identity_semantics"),
        "parameters": parameters,
        "summary": {
            **summary,
            "raw_frames_below_target": frame_summary.get("raw_frames_below_target"),
            "active_slots_frames_below_target": frame_summary.get("active_slots_frames_below_target"),
            "frames_with_predictions": frame_summary.get("frames_with_predictions"),
            "frames_with_missing_slots": frame_summary.get("frames_with_missing_slots"),
            "frames_with_ambiguous_slots": frame_summary.get("frames_with_ambiguous_slots"),
            "predicted_visible_boxes": frame_summary.get("predicted_visible_boxes"),
            "ghost_bbox_count": frame_summary.get("ghost_bbox_count"),
        },
        "frame_detection_summary": frame_summary,
        "problem_frames": problem_frames,
        "low_visible_frames": low_visible_frames,
        "ambiguous_frame_ranges": _frame_ranges(ambiguous_frames),
        "blocked_switches": blocked_switches[:500],
        "rejected_candidates": blocked_switches[:500],
        "rejected_start_candidates": rejected_start_candidates[:500],
        "visible_bbox_count_per_frame": [
            {
                "frame": frame.get("frame"),
                "time_sec": frame.get("time_sec"),
                "visible_stable_boxes": frame.get("visible_stable_boxes"),
            }
            for frame in frame_detection_counts.get("frames", [])
        ],
        "risky_slots": risky_slots,
    }


def _frame_ranges(frames: list[int]) -> list[dict[str, int]]:
    if not frames:
        return []
    ranges: list[dict[str, int]] = []
    start = frames[0]
    previous = frames[0]
    for frame in frames[1:]:
        if frame == previous + 1:
            previous = frame
            continue
        ranges.append({"start_frame": start, "end_frame": previous})
        start = frame
        previous = frame
    ranges.append({"start_frame": start, "end_frame": previous})
    return ranges
