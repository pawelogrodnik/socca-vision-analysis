from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import numpy as np


INSIDE_PLAY = "inside_play"
BOUNDARY_TRANSIENT = "boundary_transient"
OUTSIDE_PLAY = "outside_play"
PLAY_AREA_TRACK_BIRTH_POLICY_VERSION = "inside_birth_boundary_continuation_v1"


def _telemetry_label(status: Any) -> str:
    return {
        INSIDE_PLAY: "inside",
        BOUNDARY_TRANSIENT: "boundary",
        OUTSIDE_PLAY: "outside",
    }.get(status, "missing_status")


def can_detection_start_track(detection: dict[str, Any]) -> bool:
    """Only trusted on-pitch evidence may establish a production track."""
    return detection.get("play_area_status") == INSIDE_PLAY


def can_detection_continue_track(
    detection: dict[str, Any],
    *,
    allow_outside_continuation: bool,
) -> bool:
    status = detection.get("play_area_status")
    if status in {INSIDE_PLAY, BOUNDARY_TRANSIENT}:
        return True
    return bool(allow_outside_continuation and status == OUTSIDE_PLAY)


@dataclass
class Track:
    id: int
    last_centroid: np.ndarray
    missing: int = 0
    positions: list[dict[str, Any]] = field(default_factory=list)


class CentroidTracker:
    """Small centroid tracker used by motion and high-recall YOLO adapters.

    Motion analysis keeps the legacy input contract. Production YOLO enables
    ``play_area_aware`` so the wide detection ROI can preserve diagnostics
    without allowing off-pitch observations to establish player tracks.
    """

    def __init__(
        self,
        max_distance_px: float = 80,
        max_missing: int = 10,
        *,
        play_area_aware: bool = False,
        allow_outside_continuation: bool = False,
    ) -> None:
        self.max_distance_px = float(max_distance_px)
        self.max_missing = int(max_missing)
        self.play_area_aware = bool(play_area_aware)
        self.allow_outside_continuation = bool(allow_outside_continuation)
        self.next_id = 1
        self.tracks: dict[int, Track] = {}
        self.finished: list[Track] = []
        self._telemetry: Counter[str] = Counter()

    def update(self, detections: list[dict[str, Any]], frame_idx: int, time_sec: float) -> list[dict[str, Any]]:
        self._record_ineligible_births(detections)
        association_detections = self._association_detections(detections)
        centroids = np.array(
            [d.get("tracking_footpoint") or d["footpoint"] for d in association_detections],
            dtype=np.float32,
        )
        unmatched_dets = set(range(len(association_detections)))

        for track in self.tracks.values():
            track.missing += 1

        if len(centroids) and self.tracks:
            track_items = list(self.tracks.items())
            distances = np.zeros((len(track_items), len(centroids)), dtype=np.float32)
            for i, (_, track) in enumerate(track_items):
                distances[i] = np.linalg.norm(centroids - track.last_centroid, axis=1)

            used_tracks: set[int] = set()
            while True:
                i, j = np.unravel_index(np.argmin(distances), distances.shape)
                if distances[i, j] > self.max_distance_px:
                    break
                track_id, track = track_items[i]
                if track_id in used_tracks or j not in unmatched_dets:
                    distances[i, j] = np.inf
                    if not np.isfinite(distances).any():
                        break
                    continue
                detection = association_detections[j]
                self._assign(track, detection, centroids[j], frame_idx, time_sec)
                self._telemetry[f"association_{_telemetry_label(detection.get('play_area_status'))}"] += 1
                used_tracks.add(track_id)
                unmatched_dets.remove(j)
                distances[i, :] = np.inf
                distances[:, j] = np.inf
                if not unmatched_dets or not np.isfinite(distances).any():
                    break

        for j in list(unmatched_dets):
            detection = association_detections[j]
            if self.play_area_aware and not can_detection_start_track(detection):
                self._record_rejected_birth(detection)
                continue
            self._start_track(detection, centroids[j], frame_idx, time_sec)

        self._retire_missing()
        return [t.positions[-1] for t in self.tracks.values() if t.positions and t.positions[-1]["frame"] == frame_idx]

    def _start_track(self, det: dict[str, Any], centroid: np.ndarray, frame_idx: int, time_sec: float) -> None:
        track = Track(id=self.next_id, last_centroid=centroid)
        self.next_id += 1
        self._assign(track, det, centroid, frame_idx, time_sec)
        self.tracks[track.id] = track
        label = _telemetry_label(det.get("play_area_status")) if self.play_area_aware else "legacy"
        self._telemetry[f"track_birth_{label}"] += 1

    def _association_detections(self, detections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.play_area_aware:
            return detections
        eligible: list[dict[str, Any]] = []
        for detection in detections:
            if can_detection_continue_track(
                detection,
                allow_outside_continuation=self.allow_outside_continuation,
            ):
                eligible.append(detection)
                continue
            status = detection.get("play_area_status")
            self._telemetry[f"continuation_rejected_{_telemetry_label(status)}"] += 1
        return eligible

    def _record_rejected_birth(self, detection: dict[str, Any]) -> None:
        status = detection.get("play_area_status")
        self._telemetry[f"track_birth_rejected_{_telemetry_label(status)}"] += 1

    def _record_ineligible_births(self, detections: list[dict[str, Any]]) -> None:
        if not self.play_area_aware:
            return
        for detection in detections:
            if detection.get("play_area_status") != BOUNDARY_TRANSIENT and not can_detection_start_track(detection):
                self._record_rejected_birth(detection)

    def _assign(self, track: Track, det: dict[str, Any], centroid: np.ndarray, frame_idx: int, time_sec: float) -> None:
        row = dict(det)
        row.update({"track_id": track.id, "frame": frame_idx, "time_sec": round(float(time_sec), 3)})
        track.positions.append(row)
        track.last_centroid = centroid
        track.missing = 0

    def _retire_missing(self) -> None:
        for track_id in list(self.tracks):
            if self.tracks[track_id].missing > self.max_missing:
                self.finished.append(self.tracks.pop(track_id))

    def all_tracks(self) -> list[Track]:
        return self.finished + list(self.tracks.values())

    def telemetry(self) -> dict[str, int]:
        return dict(sorted(self._telemetry.items()))
