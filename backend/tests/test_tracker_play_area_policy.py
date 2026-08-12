from __future__ import annotations

import unittest

from app.services.tracker import CentroidTracker


def detection(x: float, status: str | None, *, y: float = 20.0) -> dict:
    row = {
        "bbox_xyxy": [x - 2.0, y - 8.0, x + 2.0, y],
        "footpoint": [x, y],
        "tracking_footpoint": [x, y],
        "confidence": 0.9,
    }
    if status is not None:
        row["play_area_status"] = status
    return row


def production_tracker(*, max_missing: int = 2) -> CentroidTracker:
    return CentroidTracker(
        max_distance_px=20.0,
        max_missing=max_missing,
        play_area_aware=True,
        allow_outside_continuation=False,
    )


class PlayAreaAwareTrackPolicyTests(unittest.TestCase):
    def test_inside_detection_starts_track(self) -> None:
        tracker = production_tracker()

        rows = tracker.update([detection(10, "inside_play")], 1, 0.04)

        self.assertEqual(len(rows), 1)
        self.assertEqual(len(tracker.all_tracks()), 1)
        self.assertEqual(tracker.telemetry()["track_birth_inside"], 1)

    def test_boundary_only_person_never_starts_track(self) -> None:
        tracker = production_tracker()

        for frame in range(1, 4):
            tracker.update([detection(10, "boundary_transient")], frame, frame / 25)

        self.assertEqual(tracker.all_tracks(), [])
        self.assertEqual(tracker.telemetry()["track_birth_rejected_boundary"], 3)

    def test_outside_only_person_never_starts_track(self) -> None:
        tracker = production_tracker()

        for frame in range(1, 4):
            tracker.update([detection(10, "outside_play")], frame, frame / 25)

        self.assertEqual(tracker.all_tracks(), [])
        self.assertEqual(tracker.telemetry()["track_birth_rejected_outside"], 3)
        self.assertEqual(tracker.telemetry()["continuation_rejected_outside"], 3)

    def test_inside_boundary_inside_keeps_one_track(self) -> None:
        tracker = production_tracker()
        statuses = [
            "inside_play",
            "inside_play",
            "boundary_transient",
            "boundary_transient",
            "inside_play",
            "inside_play",
        ]

        returned_ids = []
        for frame, status in enumerate(statuses, start=1):
            rows = tracker.update([detection(10 + frame, status)], frame, frame / 25)
            returned_ids.append(rows[0]["track_id"])

        self.assertEqual(returned_ids, [1] * len(statuses))
        self.assertEqual(len(tracker.all_tracks()), 1)
        self.assertEqual(tracker.telemetry().get("track_birth_rejected_boundary", 0), 0)
        self.assertEqual(tracker.telemetry()["association_boundary"], 2)

    def test_substitute_starts_only_after_first_inside_observation(self) -> None:
        tracker = production_tracker()
        statuses = ["outside_play", "outside_play", "boundary_transient", "inside_play", "inside_play"]
        track_counts = []

        for frame, status in enumerate(statuses, start=1):
            tracker.update([detection(10, status)], frame, frame / 25)
            track_counts.append(len(tracker.all_tracks()))

        self.assertEqual(track_counts, [0, 0, 0, 1, 1])
        track = tracker.all_tracks()[0]
        self.assertEqual([row["frame"] for row in track.positions], [4, 5])
        self.assertEqual(track.positions[0]["play_area_status"], "inside_play")

    def test_persistent_bench_person_never_creates_lineage(self) -> None:
        tracker = production_tracker()

        for frame in range(1, 101):
            status = "boundary_transient" if frame % 2 else "outside_play"
            tracker.update([detection(10, status)], frame, frame / 25)

        self.assertEqual(tracker.all_tracks(), [])
        telemetry = tracker.telemetry()
        self.assertEqual(telemetry["track_birth_rejected_boundary"], 50)
        self.assertEqual(telemetry["track_birth_rejected_outside"], 50)

    def test_existing_player_leaving_field_is_not_followed_outside(self) -> None:
        tracker = production_tracker(max_missing=1)
        statuses = ["inside_play", "inside_play", "boundary_transient", "outside_play", "outside_play"]

        for frame, status in enumerate(statuses, start=1):
            tracker.update([detection(10 + frame, status)], frame, frame / 25)

        tracks = tracker.all_tracks()
        self.assertEqual(len(tracks), 1)
        self.assertEqual(
            [row["play_area_status"] for row in tracks[0].positions],
            ["inside_play", "inside_play", "boundary_transient"],
        )
        self.assertEqual(tracker.telemetry()["continuation_rejected_outside"], 2)
        self.assertEqual(tracker.telemetry()["track_birth_rejected_outside"], 2)

    def test_two_touchline_players_remain_separate(self) -> None:
        tracker = production_tracker()

        tracker.update(
            [detection(10, "inside_play"), detection(60, "inside_play")],
            1,
            0.04,
        )
        rows = tracker.update(
            [detection(12, "boundary_transient"), detection(58, "boundary_transient")],
            2,
            0.08,
        )

        self.assertEqual({row["track_id"] for row in rows}, {1, 2})
        self.assertEqual(len(tracker.all_tracks()), 2)
        self.assertTrue(all(track.positions[0]["play_area_status"] == "inside_play" for track in tracker.all_tracks()))

    def test_missing_status_fails_closed_for_play_area_aware_tracker(self) -> None:
        tracker = production_tracker()

        tracker.update([detection(10, None)], 1, 0.04)

        self.assertEqual(tracker.all_tracks(), [])
        self.assertEqual(tracker.telemetry()["track_birth_rejected_missing_status"], 1)
        self.assertEqual(tracker.telemetry()["continuation_rejected_missing_status"], 1)

    def test_legacy_motion_tracker_accepts_detection_without_status(self) -> None:
        tracker = CentroidTracker(max_distance_px=20.0, max_missing=2)

        tracker.update([detection(10, None)], 1, 0.04)

        self.assertEqual(len(tracker.all_tracks()), 1)
        self.assertEqual(tracker.telemetry()["track_birth_legacy"], 1)

    def test_inside_player_and_off_pitch_person_produce_only_player_track(self) -> None:
        tracker = production_tracker()

        for frame in range(1, 8):
            tracker.update(
                [
                    detection(10 + frame, "inside_play"),
                    detection(100, "boundary_transient" if frame < 4 else "outside_play"),
                ],
                frame,
                frame / 25,
            )

        tracks = tracker.all_tracks()
        self.assertEqual(len(tracks), 1)
        self.assertEqual(len(tracks[0].positions), 7)
        self.assertTrue(all(row["play_area_status"] == "inside_play" for row in tracks[0].positions))


if __name__ == "__main__":
    unittest.main()
