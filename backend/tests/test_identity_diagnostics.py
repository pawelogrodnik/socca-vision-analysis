from __future__ import annotations

from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.identity_diagnostics import build_identity_diagnostics
from app.services.stabilization import _build_identity_diagnostics_safely


FPS = 30.0


def position(frame: int, x: float, y: float, *, bbox: list[int] | None = None, confidence: float = 0.9) -> dict:
    return {
        "frame": frame,
        "time_sec": frame / FPS,
        "bbox_xyxy": bbox or [int(x * 10), int(y * 10), int(x * 10) + 20, int(y * 10) + 50],
        "pitch_m": [x, y],
        "confidence": confidence,
        "play_area_status": "inside_play",
    }


def tracklet(
    tracklet_id: str,
    frames: range,
    *,
    team: str = "A",
    team_confidence: float = 0.95,
    confidence: float = 0.9,
    x: float = 5.0,
    y: float = 10.0,
    bbox: list[int] | None = None,
) -> dict:
    positions = [position(frame, x + (frame - frames.start) * 0.01, y, bbox=bbox, confidence=confidence) for frame in frames]
    return {
        "tracklet_id": tracklet_id,
        "source_track_id": int(tracklet_id.split(":", 1)[0]),
        "segment_index": 1,
        "start_time_sec": frames.start / FPS,
        "end_time_sec": (frames.stop - 1) / FPS,
        "duration_sec": max(0.0, (len(positions) - 1) / FPS),
        "positions_count": len(positions),
        "mean_confidence": confidence,
        "first_pitch_m": positions[0]["pitch_m"],
        "last_pitch_m": positions[-1]["pitch_m"],
        "positions": positions,
        "team_label": team,
        "team_confidence": team_confidence,
        "appearance_quality": 0.8,
        "appearance_samples": 6,
        "appearance_feature": [1.0, 2.0, 3.0],
    }


def global_identity(*, duplicate_rows: list[dict] | None = None, slots: list[dict] | None = None) -> dict:
    return {
        "slots": slots or [],
        "suppressed_duplicate_observations": duplicate_rows or [],
        "unmatched_observations": [],
    }


class IdentityDiagnosticsTests(unittest.TestCase):
    def test_classifies_all_five_tracklet_quality_classes(self) -> None:
        trusted = tracklet("1:1", range(0, 91))
        recoverable = tracklet("2:1", range(100, 119), x=8.0)
        continuation = tracklet("3:1", range(121, 151), x=8.2)
        ambiguous = tracklet("4:1", range(200, 230), team="U", team_confidence=0.0, x=20.0)
        duplicate = tracklet("5:1", range(300, 330), x=12.0)
        rejected = tracklet("6:1", range(400, 403), confidence=0.05)
        duplicate_rows = [
            {"tracklet_id": "5:1", "frame": frame}
            for frame in range(300, 325)
        ]

        docs = build_identity_diagnostics(
            [trusted, recoverable, continuation, ambiguous, duplicate],
            [rejected],
            global_identity(duplicate_rows=duplicate_rows),
            fps=FPS,
            generated_at="fixed",
        )

        classes = {
            row["tracklet_id"]: row["quality_class"]
            for row in docs["identity_tracklet_quality"]["tracklets"]
        }
        self.assertEqual(classes["1:1"], "trusted")
        self.assertEqual(classes["2:1"], "recoverable")
        self.assertEqual(classes["4:1"], "ambiguous")
        self.assertEqual(classes["5:1"], "duplicate")
        self.assertEqual(classes["6:1"], "noise")

    def test_groups_contiguous_overlap_into_one_occlusion_event(self) -> None:
        first = tracklet("1:1", range(0, 20), bbox=[10, 10, 30, 70])
        second = tracklet("2:1", range(5, 15), team="B", bbox=[15, 12, 35, 72])

        docs = build_identity_diagnostics(
            [first, second],
            [],
            global_identity(),
            fps=FPS,
            generated_at="fixed",
        )

        events = docs["identity_occlusion_events"]["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start_frame"], 5)
        self.assertEqual(events[0]["end_frame"], 14)
        self.assertEqual(events[0]["tracklet_ids"], ["1:1", "2:1"])

    def test_unreliable_footpoint_is_reported_without_mutating_inputs(self) -> None:
        first = tracklet("1:1", range(0, 30), bbox=[10, 10, 30, 70])
        second = tracklet("2:1", range(10, 20), team="B", bbox=[15, 12, 35, 72])
        original_tracklets = deepcopy([first, second])
        identity = global_identity()
        original_identity = deepcopy(identity)

        docs = build_identity_diagnostics(
            [first, second],
            [],
            identity,
            fps=FPS,
            generated_at="fixed",
        )

        quality = next(
            row
            for row in docs["identity_tracklet_quality"]["tracklets"]
            if row["tracklet_id"] == "1:1"
        )
        self.assertLess(quality["footpoint_reliable_ratio"], 1.0)
        self.assertTrue(quality["unreliable_footpoint_ranges"])
        self.assertEqual([first, second], original_tracklets)
        self.assertEqual(identity, original_identity)

    def test_documents_are_deterministic_with_fixed_timestamp(self) -> None:
        rows = [tracklet("1:1", range(0, 90))]

        first = build_identity_diagnostics(rows, [], global_identity(), fps=FPS, generated_at="fixed")
        second = build_identity_diagnostics(rows, [], global_identity(), fps=FPS, generated_at="fixed")

        self.assertEqual(first, second)

    def test_shadow_failure_returns_warning_and_no_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.services.stabilization.build_identity_diagnostics", side_effect=RuntimeError("boom")):
                documents, warning = _build_identity_diagnostics_safely(
                    Path(directory),
                    [],
                    [],
                    global_identity(),
                    fps=FPS,
                    enabled=True,
                )

        self.assertEqual(documents, {})
        self.assertIn("boom", warning or "")

    def test_disabled_shadow_layer_is_silent(self) -> None:
        documents, warning = _build_identity_diagnostics_safely(
            Path("."),
            [],
            [],
            global_identity(),
            fps=FPS,
            enabled=False,
        )

        self.assertEqual(documents, {})
        self.assertIsNone(warning)


if __name__ == "__main__":
    unittest.main()
