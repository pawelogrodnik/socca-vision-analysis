from __future__ import annotations

import copy
import unittest

from evaluation.targeted_ball_gap_recovery import (
    AMBIGUOUS,
    INCONCLUSIVE,
    NO_BRIDGE,
    NO_LOW_CONF_CANDIDATES,
    UNIQUE_BRIDGE,
    evaluate_operator_after_recovery,
    find_target_gap_boundaries,
    recover_targeted_gap,
)


def _track(frame: int, source: str, x: float | None) -> dict:
    return {
        "frame": frame,
        "time_sec": frame / 10,
        "source": source,
        "candidate_id": f"base-{frame}" if x is not None else None,
        "position_m": [x, 0.0] if x is not None else None,
        "position_px": [x * 10, 100.0] if x is not None else None,
        "confidence": .8 if x is not None else 0.0,
    }


def _tracks(rows: list[dict]) -> dict:
    return {"parameters": {"max_link_speed_mps": 22.0, "min_start_conf": .08, "ball_selection_policy": "ball-selection:v1"}, "positions": rows}


def _candidate(frame: int, x: float, candidate_id: str, confidence: float = .2) -> dict:
    return {"frame": frame, "time_sec": frame / 10, "candidate_id": candidate_id, "position_m": [x, 0.0], "position_px": [x * 10, 100.0], "confidence": confidence, "width_px": 10.0, "height_px": 10.0}


def _frame(frame: int, candidates: list[dict], *, raw: list[dict] | None = None) -> dict:
    return {"frame": frame, "time_sec": frame / 10, "candidates": candidates, "raw_prediction_rows": candidates if raw is None else raw, "rejected_candidates": []}


class TargetedBallGapRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tracks = _tracks([_track(0, "detected", 0), _track(1, "interpolated", 1), _track(2, "unknown", None), _track(3, "interpolated", 3), _track(4, "detected", 4)])

    def test_finds_detected_boundaries_around_target_region(self) -> None:
        boundaries = find_target_gap_boundaries(self.tracks, target_time_sec=.2)
        self.assertEqual((boundaries["left_boundary"]["frame"], boundaries["right_boundary"]["frame"]), (0, 4))
        self.assertEqual((boundaries["start_frame"], boundaries["end_frame"]), (1, 3))
        self.assertEqual(boundaries["duration_sec"], .4)

    def test_missing_boundary_is_inconclusive(self) -> None:
        tracks = _tracks([_track(0, "unknown", None), _track(1, "unknown", None), _track(2, "detected", 2)])
        result = recover_targeted_gap(tracks, target_time_sec=.1, low_confidence_frames=[], fps=10)
        self.assertEqual(result["outcome"], INCONCLUSIVE)

    def test_unique_two_sided_chain_is_selected_without_anchor(self) -> None:
        evidence = [_frame(1, [_candidate(1, 1, "a")]), _frame(2, [_candidate(2, 2, "b")]), _frame(3, [_candidate(3, 3, "c")])]
        result = recover_targeted_gap(self.tracks, target_time_sec=.2, low_confidence_frames=evidence, fps=10)
        self.assertEqual(result["outcome"], UNIQUE_BRIDGE)
        self.assertEqual(result["bridge"]["candidate_ids"], ["a", "b", "c"])
        self.assertGreater(result["bridge"]["max_speed_mps"], 0)

    def test_impossible_speed_chain_is_not_a_bridge(self) -> None:
        evidence = [_frame(1, [_candidate(1, 100, "far")])]
        result = recover_targeted_gap(self.tracks, target_time_sec=.2, low_confidence_frames=evidence, fps=10)
        self.assertEqual(result["outcome"], NO_BRIDGE)

    def test_two_directionally_plausible_but_different_chains_are_ambiguous(self) -> None:
        evidence = [_frame(1, [_candidate(1, 1, "forward", confidence=.2), _candidate(1, 1.1, "backward", confidence=.2)])]
        result = recover_targeted_gap(self.tracks, target_time_sec=.2, low_confidence_frames=evidence, fps=10)
        self.assertEqual(result["outcome"], AMBIGUOUS)

    def test_no_accepted_low_confidence_candidate_is_reported(self) -> None:
        result = recover_targeted_gap(self.tracks, target_time_sec=.2, low_confidence_frames=[_frame(1, [], raw=[_candidate(1, 1, "raw")])], fps=10)
        self.assertEqual(result["outcome"], NO_LOW_CONF_CANDIDATES)

    def test_operator_coordinates_cannot_change_boundaries_or_bridge_selection(self) -> None:
        evidence = [_frame(1, [_candidate(1, 1, "a")]), _frame(2, [_candidate(2, 2, "b")]), _frame(3, [_candidate(3, 3, "c")])]
        first = recover_targeted_gap(copy.deepcopy(self.tracks), target_time_sec=.2, low_confidence_frames=copy.deepcopy(evidence), fps=10)
        second = recover_targeted_gap(copy.deepcopy(self.tracks), target_time_sec=.2, low_confidence_frames=copy.deepcopy(evidence), fps=10)
        self.assertEqual(first["boundaries"], second["boundaries"])
        self.assertEqual(first["bridge"]["candidate_ids"], second["bridge"]["candidate_ids"])
        real = evaluate_operator_after_recovery(first, {"source_time_sec": .2, "x_px": 20, "y_px": 100})
        absurd = evaluate_operator_after_recovery(second, {"source_time_sec": .2, "x_px": -9999, "y_px": 9999})
        self.assertNotEqual(real["distance_px_to_operator"], absurd["distance_px_to_operator"])
        self.assertEqual(first["bridge"]["candidate_ids"], second["bridge"]["candidate_ids"])

    def test_result_is_deterministic_and_does_not_mutate_tracks(self) -> None:
        original = copy.deepcopy(self.tracks)
        evidence = [_frame(1, [_candidate(1, 1, "a")]), _frame(2, [_candidate(2, 2, "b")]), _frame(3, [_candidate(3, 3, "c")])]
        first = recover_targeted_gap(self.tracks, target_time_sec=.2, low_confidence_frames=evidence, fps=10)
        second = recover_targeted_gap(self.tracks, target_time_sec=.2, low_confidence_frames=evidence, fps=10)
        self.assertEqual(first, second)
        self.assertEqual(self.tracks, original)
