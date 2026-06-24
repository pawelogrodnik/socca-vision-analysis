from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class Track:
    id: int
    last_centroid: np.ndarray
    missing: int = 0
    positions: list[dict[str, Any]] = field(default_factory=list)


class CentroidTracker:
    """Small fallback tracker for the motion adapter.

    It is not meant to replace BoT-SORT/ByteTrack. It only keeps the app usable without YOLO.
    """

    def __init__(self, max_distance_px: float = 80, max_missing: int = 10) -> None:
        self.max_distance_px = float(max_distance_px)
        self.max_missing = int(max_missing)
        self.next_id = 1
        self.tracks: dict[int, Track] = {}
        self.finished: list[Track] = []

    def update(self, detections: list[dict[str, Any]], frame_idx: int, time_sec: float) -> list[dict[str, Any]]:
        centroids = np.array([d["footpoint"] for d in detections], dtype=np.float32)
        unmatched_dets = set(range(len(detections)))

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
                self._assign(track, detections[j], centroids[j], frame_idx, time_sec)
                used_tracks.add(track_id)
                unmatched_dets.remove(j)
                distances[i, :] = np.inf
                distances[:, j] = np.inf
                if not unmatched_dets or not np.isfinite(distances).any():
                    break

        for j in list(unmatched_dets):
            self._start_track(detections[j], centroids[j], frame_idx, time_sec)

        self._retire_missing()
        return [t.positions[-1] for t in self.tracks.values() if t.positions and t.positions[-1]["frame"] == frame_idx]

    def _start_track(self, det: dict[str, Any], centroid: np.ndarray, frame_idx: int, time_sec: float) -> None:
        track = Track(id=self.next_id, last_centroid=centroid)
        self.next_id += 1
        self._assign(track, det, centroid, frame_idx, time_sec)
        self.tracks[track.id] = track

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
