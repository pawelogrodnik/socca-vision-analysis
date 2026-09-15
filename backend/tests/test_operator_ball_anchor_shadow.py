from __future__ import annotations

import copy
import unittest

from evaluation.operator_ball_anchor_shadow import (
    evaluate_operator_ball_anchors,
    extract_operator_ball_anchors,
)


def _candidate(candidate_id: str, frame: int, x: float, y: float, *, confidence: float = 0.7) -> dict:
    return {
        "candidate_id": candidate_id,
        "frame": frame,
        "time_sec": frame / 10,
        "position_px": [x, y],
        "position_m": [x / 10, y / 10],
        "width_px": 10,
        "height_px": 10,
        "confidence": confidence,
    }


def _source(frames: list[dict], positions: list[dict]) -> dict:
    return {"fps": 10.0, "ball_candidates": {"frames": frames}, "ball_tracks": {"positions": positions}}


def _anchor(*, source_time_sec: float = 10.0, x: float = 100.0, y: float = 100.0) -> dict:
    return {
        "canonical_shot_id": "shot-one",
        "published_id": "published-merged-one",
        "source_match_id": "source-two",
        "source_time_sec": source_time_sec,
        "logical_frame_time_sec": 42.0,
        "x_px": x,
        "y_px": y,
        "frame_width": 1920.0,
        "frame_height": 1080.0,
    }


class OperatorBallAnchorShadowTests(unittest.TestCase):
    def test_extracts_only_valid_internal_frame_corrections_and_keeps_source_time(self) -> None:
        document = {
            "published_id": "published-merged-one",
            "canonical_shots": [
                {"shot_id": "valid", "time_sec": 40.0, "location_correction_provenance": {
                    "source_match_id": "source-two", "source_time_sec": 10.2,
                    "logical_frame_time_sec": 40.1, "x_px": 20, "y_px": 30,
                    "frame_width": 100, "frame_height": 80,
                }},
                {"shot_id": "missing", "location_correction_provenance": {"source_match_id": "source-two"}},
                {"shot_id": "invalid", "location_correction_provenance": {
                    "source_match_id": "source-two", "source_time_sec": 1,
                    "logical_frame_time_sec": 1, "x_px": 101, "y_px": 1,
                    "frame_width": 100, "frame_height": 80,
                }},
            ],
        }
        self.assertEqual(extract_operator_ball_anchors(document), [{
            "canonical_shot_id": "valid", "published_id": "published-merged-one",
            "source_match_id": "source-two", "source_time_sec": 10.2,
            "logical_frame_time_sec": 40.1, "x_px": 20.0, "y_px": 30.0,
            "frame_width": 100.0, "frame_height": 80.0,
        }])

    def test_uses_persisted_physical_source_time_not_logical_shot_time(self) -> None:
        anchor = _anchor(source_time_sec=10.0)
        frames = [{"frame": 100, "time_sec": 10.0, "candidates": [_candidate("actual", 100, 100, 100)]}]
        report = evaluate_operator_ball_anchors([anchor], {"source-two": _source(frames, [])})
        row = report["anchors"][0]
        self.assertEqual((row["anchor"]["source_match_id"], row["anchor"]["source_time_sec"]), ("source-two", 10.0))
        self.assertEqual(row["anchored"]["anchor_candidate_id"], "actual")

    def test_multi_ball_anchor_recovers_the_matching_candidate_and_propagates_both_directions(self) -> None:
        foreign_before = _candidate("foreign-before", 99, 701, 701)
        actual_before = _candidate("actual-before", 99, 99, 100)
        foreign = _candidate("foreign", 100, 700, 700)
        actual = _candidate("actual", 100, 100, 100)
        foreign_after = _candidate("foreign-after", 101, 699, 699)
        actual_after = _candidate("actual-after", 101, 101, 100)
        frames = [
            {"frame": 99, "time_sec": 9.9, "candidates": [foreign_before, actual_before]},
            {"frame": 100, "time_sec": 10.0, "candidates": [foreign, actual]},
            {"frame": 101, "time_sec": 10.1, "candidates": [foreign_after, actual_after]},
        ]
        tracks = [
            {"frame": 99, "time_sec": 9.9, "candidate_id": "foreign-before", "position_px": [701, 701], "source": "detected", "confidence": .8},
            {"frame": 100, "time_sec": 10.0, "candidate_id": "foreign", "position_px": [700, 700], "source": "detected", "confidence": .8},
            {"frame": 101, "time_sec": 10.1, "candidate_id": "foreign-after", "position_px": [699, 699], "source": "detected", "confidence": .8},
        ]
        report = evaluate_operator_ball_anchors([_anchor()], {"source-two": _source(frames, tracks)}, window_sec=.2)
        row = report["anchors"][0]
        self.assertEqual(row["classification"], "wrong_candidate_recovered")
        self.assertEqual(row["anchored"]["anchor_candidate_id"], "actual")
        self.assertEqual([point["candidate_id"] for point in row["anchored"]["local_shadow_path"]], ["actual-before", "actual", "actual-after"])
        self.assertEqual((row["comparison"]["frames_changed"], row["comparison"]["shadow_anchor_error_px"]), (3, 0.0))

    def test_stale_track_is_recovered_and_already_correct_track_is_left_as_correct(self) -> None:
        actual = _candidate("actual", 100, 100, 100)
        after = _candidate("after", 101, 101, 100)
        frames = [{"frame": 100, "time_sec": 10.0, "candidates": [actual]}, {"frame": 101, "time_sec": 10.1, "candidates": [after]}]
        stale = evaluate_operator_ball_anchors([_anchor()], {"source-two": _source(frames, [{"frame": 100, "time_sec": 10, "candidate_id": None, "position_px": None, "source": "unknown", "confidence": 0}])}, window_sec=.2)
        correct = evaluate_operator_ball_anchors([_anchor()], {"source-two": _source(frames, [{"frame": 100, "time_sec": 10, "candidate_id": "actual", "position_px": [100, 100], "source": "detected", "confidence": .7}])}, window_sec=.2)
        self.assertEqual(stale["anchors"][0]["classification"], "stale_track_recovered")
        self.assertEqual(correct["anchors"][0]["classification"], "already_correct")

    def test_distant_or_missing_candidate_is_detector_miss_without_fake_path(self) -> None:
        distant = _candidate("distant", 100, 300, 300)
        report = evaluate_operator_ball_anchors([_anchor()], {"source-two": _source([{"frame": 100, "time_sec": 10, "candidates": [distant]}], [])})
        no_candidate = evaluate_operator_ball_anchors([_anchor()], {"source-two": _source([{"frame": 100, "time_sec": 10, "candidates": []}], [])})
        self.assertEqual((report["anchors"][0]["classification"], report["anchors"][0]["anchored"]["local_shadow_path"]), ("detector_miss", []))
        self.assertEqual((no_candidate["summary"]["detector_miss_count"], no_candidate["anchors"][0]["anchored"]["anchor_candidate_id"]), (1, None))

    def test_implausible_jump_stops_the_path_without_interpolation_and_is_deterministic(self) -> None:
        actual = _candidate("actual", 100, 100, 100)
        impossible = _candidate("impossible", 101, 1000, 1000)
        source = _source([{"frame": 100, "time_sec": 10, "candidates": [actual]}, {"frame": 101, "time_sec": 10.1, "candidates": [impossible]}], [])
        before = copy.deepcopy(source)
        first = evaluate_operator_ball_anchors([_anchor()], {"source-two": source}, window_sec=.2)
        second = evaluate_operator_ball_anchors([_anchor()], {"source-two": source}, window_sec=.2)
        row = first["anchors"][0]
        self.assertEqual(row["classification"], "anchor_without_plausible_path")
        self.assertEqual([point["candidate_id"] for point in row["anchored"]["local_shadow_path"]], ["actual"])
        self.assertEqual(row["anchored"]["continuity"]["forward_stop_reason"], "no_plausible_candidate")
        self.assertEqual(source, before)
        self.assertEqual(first, second)

    def test_aggregate_metrics_are_derived_from_cases(self) -> None:
        actual = _candidate("actual", 100, 100, 100)
        frames = [{"frame": 100, "time_sec": 10, "candidates": [actual]}]
        report = evaluate_operator_ball_anchors([_anchor(), {**_anchor(), "canonical_shot_id": "shot-two", "x_px": 500.0}], {"source-two": _source(frames, [{"frame": 100, "time_sec": 10, "candidate_id": "actual", "position_px": [100, 100], "source": "detected", "confidence": .7}])})
        self.assertEqual(report["summary"]["classification_counts"], {"already_correct": 1, "detector_miss": 1})
        self.assertEqual((report["summary"]["matched_anchor_candidate_count"], report["summary"]["detector_miss_count"]), (1, 1))

