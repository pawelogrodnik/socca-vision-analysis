from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.services.resolved_ball_tracks import (
    RESOLVED_BALL_TRACKS_FILENAME,
    resolve_ball_tracks_document,
    write_resolved_ball_tracks_artifact,
)


def _candidate(candidate_id: str, frame: int, x: float, y: float, *, confidence: float = .7) -> dict:
    return {
        "candidate_id": candidate_id,
        "frame": frame,
        "time_sec": frame / 10,
        "position_px": [x, y],
        "position_m": [x / 10, y / 10],
        "bbox_xyxy": [x - 5, y - 5, x + 5, y + 5],
        "width_px": 10,
        "height_px": 10,
        "confidence": confidence,
    }


def _position(candidate: dict | None, frame: int) -> dict:
    if candidate is None:
        return {"frame": frame, "time_sec": frame / 10, "position_px": None, "position_m": None, "bbox_xyxy": None, "source": "unknown", "confidence": 0.0}
    return {
        "frame": frame,
        "time_sec": frame / 10,
        "candidate_id": candidate["candidate_id"],
        "position_px": candidate["position_px"],
        "position_m": candidate["position_m"],
        "bbox_xyxy": candidate["bbox_xyxy"],
        "source": "detected",
        "confidence": candidate["confidence"],
    }


def _automatic(positions: list[dict]) -> dict:
    return {
        "schema_version": "0.1.0",
        "generated_at": "fixed",
        "parameters": {
            "ball_selection_policy": "ball-selection:v1",
            "max_link_speed_mps": 22.0,
            "min_start_conf": .08,
            "max_interpolation_gap_sec": .5,
            "max_interpolation_speed_mps": 22.0,
        },
        "summary": {},
        "positions": positions,
        "interpolation_gaps": [],
    }


def _candidates(frames: list[dict]) -> dict:
    return {"parameters": {"ball_selection_policy": "ball-selection:v1", "max_link_speed_mps": 22.0, "min_start_conf": .08}, "frames": frames}


def _anchor(*, shot_id: str = "shot", time_sec: float = 10.0, x: float = 100.0, y: float = 100.0) -> dict:
    return {
        "canonical_shot_id": shot_id,
        "published_id": "published-one",
        "source_match_id": "source-one",
        "source_time_sec": time_sec,
        "logical_frame_time_sec": time_sec,
        "x_px": x,
        "y_px": y,
        "frame_width": 1920.0,
        "frame_height": 1080.0,
    }


class ResolvedBallTracksTests(unittest.TestCase):
    def _resolve(self, automatic: dict, frames: list[dict], anchors: list[dict]) -> dict:
        return resolve_ball_tracks_document("source-one", automatic, _candidates(frames), fps=10.0, anchors=anchors)

    def test_no_anchors_is_an_exact_baseline_copy_with_resolution_metadata(self) -> None:
        baseline = _automatic([_position(_candidate("base", 100, 100, 100), 100)])
        resolved = self._resolve(baseline, [], [])

        self.assertEqual(resolved["positions"], baseline["positions"])
        self.assertEqual(resolved["resolution"]["applied_anchor_count"], 0)
        self.assertNotIn("resolution", baseline)

    def test_wrong_candidate_recovery_replaces_only_real_local_candidates_and_keeps_raw_unchanged(self) -> None:
        foreign_before, foreign, foreign_after = (_candidate("foreign-before", 99, 700, 700), _candidate("foreign", 100, 700, 700), _candidate("foreign-after", 101, 700, 700))
        actual_before, actual, actual_after = (_candidate("actual-before", 99, 99, 100), _candidate("actual", 100, 100, 100), _candidate("actual-after", 101, 101, 100))
        baseline = _automatic([_position(foreign_before, 99), _position(foreign, 100), _position(foreign_after, 101)])
        before = copy.deepcopy(baseline)
        frames = [
            {"frame": 99, "time_sec": 9.9, "candidates": [foreign_before, actual_before]},
            {"frame": 100, "time_sec": 10.0, "candidates": [foreign, actual]},
            {"frame": 101, "time_sec": 10.1, "candidates": [foreign_after, actual_after]},
        ]
        resolved = self._resolve(baseline, frames, [_anchor()])

        self.assertEqual(baseline, before)
        self.assertEqual([row.get("candidate_id") for row in resolved["positions"]], ["actual-before", "actual", "actual-after"])
        self.assertTrue(all(row.get("resolution_source") == "operator_anchor_reassociated" for row in resolved["positions"]))
        self.assertEqual(resolved["resolution"]["applied_anchor_count"], 1)
        self.assertEqual(resolved["resolution"]["applied_corrections"][0]["classification"], "wrong_candidate_recovered")

    def test_already_correct_detector_miss_and_weak_stale_path_leave_rows_unchanged(self) -> None:
        actual = _candidate("actual", 100, 100, 100)
        adjacent = _candidate("adjacent", 101, 100, 100)
        correct = _automatic([_position(actual, 100), _position(adjacent, 101)])
        correct_resolved = self._resolve(copy.deepcopy(correct), [{"frame": 100, "time_sec": 10, "candidates": [actual]}, {"frame": 101, "time_sec": 10.1, "candidates": [adjacent]}], [_anchor()])
        missed = _automatic([_position(None, 100), _position(adjacent, 101)])
        missed_resolved = self._resolve(copy.deepcopy(missed), [{"frame": 100, "time_sec": 10, "candidates": []}, {"frame": 101, "time_sec": 10.1, "candidates": [adjacent]}], [_anchor()])
        weak_stale = _automatic([_position(None, 100)])
        weak_resolved = self._resolve(copy.deepcopy(weak_stale), [{"frame": 100, "time_sec": 10, "candidates": [actual]}], [_anchor()])

        self.assertEqual(correct_resolved["positions"], correct["positions"])
        self.assertEqual(missed_resolved["positions"], missed["positions"])
        self.assertEqual(weak_resolved["positions"], weak_stale["positions"])
        self.assertEqual(missed_resolved["resolution"]["skipped_anchors"][0]["reason"], "detector_miss")
        self.assertEqual(weak_resolved["resolution"]["skipped_anchors"][0]["reason"], "anchor_without_plausible_path")

    def test_stale_track_recovery_requires_and_applies_a_real_continuation(self) -> None:
        seed, following = _candidate("seed", 100, 100, 100), _candidate("following", 101, 101, 100)
        baseline = _automatic([_position(None, 100), _position(None, 101)])
        resolved = self._resolve(baseline, [{"frame": 100, "time_sec": 10, "candidates": [seed]}, {"frame": 101, "time_sec": 10.1, "candidates": [following]}], [_anchor()])

        self.assertEqual([row.get("candidate_id") for row in resolved["positions"]], ["seed", "following"])
        self.assertEqual(resolved["resolution"]["applied_corrections"][0]["classification"], "stale_track_recovered")

    def test_continuity_failure_stops_the_local_correction_without_synthetic_extension(self) -> None:
        foreign, foreign_next, foreign_later = (_candidate("foreign", 100, 700, 700), _candidate("foreign-next", 101, 700, 700), _candidate("foreign-later", 102, 700, 700))
        seed, following, impossible = (_candidate("seed", 100, 100, 100), _candidate("following", 101, 101, 100), _candidate("impossible", 102, 1000, 1000, confidence=.1))
        baseline = _automatic([_position(foreign, 100), _position(foreign_next, 101), _position(foreign_later, 102)])
        frames = [
            {"frame": 100, "time_sec": 10, "candidates": [foreign, seed]},
            {"frame": 101, "time_sec": 10.1, "candidates": [foreign_next, following]},
            {"frame": 102, "time_sec": 10.2, "candidates": [foreign_later, impossible]},
        ]
        resolved = self._resolve(baseline, frames, [_anchor()])

        self.assertEqual([row.get("candidate_id") for row in resolved["positions"]], ["seed", "following", "foreign-later"])
        self.assertEqual(resolved["resolution"]["applied_corrections"][0]["frames"], [100, 101])

    def test_unsupported_policy_is_inconclusive_and_cannot_modify_the_baseline(self) -> None:
        seed, following = _candidate("seed", 100, 100, 100), _candidate("following", 101, 101, 100)
        baseline = _automatic([_position(None, 100), _position(None, 101)])
        baseline["parameters"]["ball_selection_policy"] = "unsupported"
        resolved = self._resolve(baseline, [{"frame": 100, "time_sec": 10, "candidates": [seed]}, {"frame": 101, "time_sec": 10.1, "candidates": [following]}], [_anchor()])

        self.assertEqual(resolved["positions"], baseline["positions"])
        self.assertEqual(resolved["resolution"]["skipped_anchors"][0]["reason"], "inconclusive")

    def test_shadow_regression_is_fail_closed(self) -> None:
        seed = _candidate("seed", 100, 100, 100, confidence=.7)
        current_one, shadow_one = _candidate("current-one", 101, 101, 100, confidence=0), _candidate("shadow-one", 101, 105, 100, confidence=.99)
        current_two, shadow_two = _candidate("current-two", 102, 102, 100, confidence=0), _candidate("shadow-two", 102, 110, 100, confidence=.99)
        baseline = _automatic([_position(seed, 100), _position(current_one, 101), _position(current_two, 102)])
        frames = [
            {"frame": 100, "time_sec": 10, "candidates": [seed]},
            {"frame": 101, "time_sec": 10.1, "candidates": [current_one, shadow_one]},
            {"frame": 102, "time_sec": 10.2, "candidates": [current_two, shadow_two]},
        ]
        resolved = self._resolve(baseline, frames, [_anchor()])

        self.assertEqual(resolved["positions"], baseline["positions"])
        self.assertEqual(resolved["resolution"]["skipped_anchors"][0]["reason"], "shadow_regression")

    def test_overlapping_incompatible_anchors_fail_closed_and_duplicate_physical_anchor_is_deduplicated(self) -> None:
        foreign, foreign_next = _candidate("foreign", 100, 700, 700), _candidate("foreign-next", 101, 700, 700)
        left, right = _candidate("left", 100, 100, 100), _candidate("right", 100, 150, 100)
        left_next, right_next = _candidate("left-next", 101, 101, 100), _candidate("right-next", 101, 151, 100)
        baseline = _automatic([_position(foreign, 100), _position(foreign_next, 101)])
        frames = [
            {"frame": 100, "time_sec": 10, "candidates": [foreign, left, right]},
            {"frame": 101, "time_sec": 10.1, "candidates": [foreign_next, left_next, right_next]},
        ]
        incompatible = self._resolve(copy.deepcopy(baseline), frames, [_anchor(shot_id="left", x=100), _anchor(shot_id="right", x=150)])
        duplicate = self._resolve(copy.deepcopy(baseline), frames, [_anchor(shot_id="one", x=100), _anchor(shot_id="two", x=100)])

        self.assertEqual(incompatible["positions"], baseline["positions"])
        self.assertEqual(incompatible["resolution"]["skipped_anchor_count"], 2)
        self.assertEqual(duplicate["resolution"]["valid_anchor_count"], 1)
        self.assertEqual(duplicate["resolution"]["applied_anchor_count"], 1)

    def test_overlapping_compatible_anchors_share_one_candidate_path_deterministically(self) -> None:
        foreign, foreign_next = _candidate("foreign", 100, 700, 700), _candidate("foreign-next", 101, 700, 700)
        actual, actual_next = _candidate("actual", 100, 100, 100), _candidate("actual-next", 101, 101, 100)
        baseline = _automatic([_position(foreign, 100), _position(foreign_next, 101)])
        frames = [
            {"frame": 100, "time_sec": 10, "candidates": [foreign, actual]},
            {"frame": 101, "time_sec": 10.1, "candidates": [foreign_next, actual_next]},
        ]
        first = self._resolve(copy.deepcopy(baseline), frames, [_anchor(shot_id="one"), _anchor(shot_id="two", time_sec=10.1, x=101)])
        second = self._resolve(copy.deepcopy(baseline), frames, [_anchor(shot_id="one"), _anchor(shot_id="two", time_sec=10.1, x=101)])

        self.assertEqual(first, second)
        self.assertEqual(first["resolution"]["applied_anchor_count"], 2)
        self.assertEqual([row.get("candidate_id") for row in first["positions"]], ["actual", "actual-next"])

    def test_reassociation_uses_current_geometry_not_historical_candidate_id_and_does_not_leak_anchor_pixels(self) -> None:
        foreign, foreign_next = _candidate("foreign", 100, 700, 700), _candidate("foreign-next", 101, 700, 700)
        initial_actual = _candidate("old-id", 100, 100, 100)
        rebuilt_actual = _candidate("new-id", 100, 100, 100)
        initial_next, rebuilt_next = _candidate("old-next", 101, 101, 100), _candidate("new-next", 101, 101, 100)
        baseline = _automatic([_position(foreign, 100), _position(foreign_next, 101)])
        initial = self._resolve(copy.deepcopy(baseline), [
            {"frame": 100, "time_sec": 10, "candidates": [foreign, initial_actual]},
            {"frame": 101, "time_sec": 10.1, "candidates": [foreign_next, initial_next]},
        ], [_anchor()])
        rebuilt = self._resolve(copy.deepcopy(baseline), [
            {"frame": 100, "time_sec": 10, "candidates": [foreign, rebuilt_actual]},
            {"frame": 101, "time_sec": 10.1, "candidates": [foreign_next, rebuilt_next]},
        ], [_anchor()])

        self.assertEqual(initial["positions"][0]["candidate_id"], "old-id")
        self.assertEqual(rebuilt["positions"][0]["candidate_id"], "new-id")
        self.assertNotIn("x_px", str(rebuilt["resolution"]))

    def test_durable_sidecar_rebuild_writes_only_the_separate_resolved_artifact(self) -> None:
        foreign, foreign_next = _candidate("foreign", 100, 700, 700), _candidate("foreign-next", 101, 700, 700)
        actual, actual_next = _candidate("actual", 100, 100, 100), _candidate("actual-next", 101, 101, 100)
        automatic = _automatic([_position(foreign, 100), _position(foreign_next, 101)])
        candidates = _candidates([
            {"frame": 100, "time_sec": 10, "candidates": [foreign, actual]},
            {"frame": 101, "time_sec": 10.1, "candidates": [foreign_next, actual_next]},
        ])
        document = {"published_id": "published-one", "canonical_shots": [{"shot_id": "shot", "location_correction_provenance": _anchor()}]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            match_dir, editorial = root / "matches" / "source-one", root / "editorial" / "shots"
            match_dir.mkdir(parents=True)
            editorial.mkdir(parents=True)
            (match_dir / "match.json").write_text(json.dumps({"video": {"fps": 10.0}}), encoding="utf-8")
            (match_dir / "ball_tracks.json").write_text(json.dumps(automatic), encoding="utf-8")
            (match_dir / "ball_candidates.json").write_text(json.dumps(candidates), encoding="utf-8")
            sidecar = editorial / "published-one.json"
            sidecar.write_text(json.dumps(document), encoding="utf-8")
            raw_before, sidecar_before = (match_dir / "ball_tracks.json").read_bytes(), sidecar.read_bytes()
            with patch.object(config, "STORAGE_DIR", root):
                result = write_resolved_ball_tracks_artifact(match_dir)

            self.assertIsNotNone(result)
            self.assertEqual((match_dir / "ball_tracks.json").read_bytes(), raw_before)
            self.assertEqual(sidecar.read_bytes(), sidecar_before)
            resolved = json.loads((match_dir / RESOLVED_BALL_TRACKS_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(resolved["positions"][0]["candidate_id"], "actual")
