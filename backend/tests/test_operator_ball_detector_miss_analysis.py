from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from evaluation.operator_ball_detector_miss_analysis import (
    ACCEPTED_CANDIDATES_ONLY_FAR_FROM_ANCHOR,
    CANDIDATE_FILTERED,
    FRAME_NOT_PROCESSED,
    INCONSISTENT_EVIDENCE,
    RAW_DETECTOR_MISS,
    REJECTED_CANDIDATES_ONLY_FAR_FROM_ANCHOR,
    analyze_operator_ball_detector_misses,
    compact_operator_ball_detector_miss_analysis,
    export_detector_miss_evidence,
)


def _candidate(candidate_id: str, x: float, y: float, *, confidence: float = .7, reason: str | None = None) -> dict:
    result = {"candidate_id": candidate_id, "position_px": [x, y], "position_m": [x / 10, y / 10], "confidence": confidence, "width_px": 10, "height_px": 10}
    if reason:
        result["reason"] = reason
    return result


def _frame(frame: int = 100, *, raw: int | None = 0, accepted: list[dict] | None = None, rejected: list[dict] | None = None) -> dict:
    return {"frame": frame, "time_sec": frame / 10, "raw_predictions": ([] if raw == 0 else ([{}] * raw if raw is not None else None)), "candidates": accepted or [], "rejected_candidates": rejected or []}


def _anchor() -> dict:
    return {"canonical_shot_id": "shot-one", "published_id": "published-one", "source_match_id": "source-one", "source_time_sec": 10.0, "logical_frame_time_sec": 10.0, "x_px": 100.0, "y_px": 100.0, "frame_width": 1920.0, "frame_height": 1080.0}


def _source(frames: list[dict]) -> dict:
    return {"fps": 10.0, "ball_selection_policy": "ball-selection:v1", "video_path": "/missing/video.mp4", "ball_candidates": {"parameters": {"detector": "test-ball", "ball_conf": .03, "imgsz": 960, "frame_stride": 1}, "frames": frames}, "ball_tracks": {"positions": []}}


def _report(frame: dict) -> dict:
    return analyze_operator_ball_detector_misses([_anchor()], {"source-one": _source([frame])})


class OperatorBallDetectorMissAnalysisTests(unittest.TestCase):
    def test_non_miss_is_excluded(self) -> None:
        report = _report(_frame(raw=1, accepted=[_candidate("close", 100, 100)]))
        self.assertEqual((report["detector_miss_count"], report["misses"]), (0, []))

    def test_frame_absent_is_frame_not_processed(self) -> None:
        report = _report(_frame(102, raw=0))
        self.assertEqual(report["misses"][0]["primary_category"], FRAME_NOT_PROCESSED)

    def test_zero_raw_evidence_is_raw_detector_miss(self) -> None:
        row = _report(_frame(raw=0))["misses"][0]
        self.assertEqual((row["primary_category"], row["exact_frame"]["raw_prediction_count"]), (RAW_DETECTOR_MISS, 0))

    def test_near_rejected_candidate_is_filtered_and_preserves_reason(self) -> None:
        row = _report(_frame(raw=1, rejected=[_candidate("rejected", 102, 100, reason="too_small")]))["misses"][0]
        self.assertEqual(row["primary_category"], CANDIDATE_FILTERED)
        self.assertEqual(row["exact_frame"]["nearest_rejected_candidate"]["rejection_reason"], "too_small")
        self.assertEqual(row["exact_frame"]["rejection_reasons"], ["too_small"])

    def test_accepted_or_rejected_far_evidence_is_not_raw_miss(self) -> None:
        accepted = _report(_frame(raw=1, accepted=[_candidate("far", 400, 400)]))["misses"][0]
        rejected = _report(_frame(raw=1, rejected=[_candidate("far", 400, 400, reason="outside_pitch")]))["misses"][0]
        self.assertEqual(accepted["primary_category"], ACCEPTED_CANDIDATES_ONLY_FAR_FROM_ANCHOR)
        self.assertEqual(rejected["primary_category"], REJECTED_CANDIDATES_ONLY_FAR_FROM_ANCHOR)

    def test_near_accepted_evidence_is_inconsistent_with_upstream_miss(self) -> None:
        # Force an upstream miss by retaining an invalid accepted candidate shape
        # in the shadow evaluator while forensic evidence can still see its bbox.
        candidate = {"candidate_id": "bbox-only", "bbox_xyxy": [95, 95, 105, 105], "confidence": .8}
        row = _report(_frame(raw=1, accepted=[candidate]))["misses"][0]
        self.assertEqual(row["primary_category"], INCONSISTENT_EVIDENCE)
        self.assertEqual(row["reason"], "accepted_candidate_within_authoritative_anchor_distance")

    def test_nearest_is_deterministic_and_context_does_not_reclassify(self) -> None:
        source = _source([
            _frame(99, raw=1, accepted=[_candidate("context-close", 100, 100)]),
            _frame(100, raw=1, accepted=[_candidate("same-distance-b", 120, 200, confidence=.2), _candidate("same-distance-a", 80, 200, confidence=.2)]),
            _frame(101, raw=1, rejected=[_candidate("after", 100, 100, reason="too_small")]),
        ])
        row = analyze_operator_ball_detector_misses([_anchor()], {"source-one": source})["misses"][0]
        self.assertEqual(row["primary_category"], ACCEPTED_CANDIDATES_ONLY_FAR_FROM_ANCHOR)
        self.assertEqual(row["exact_frame"]["nearest_accepted_candidate"]["candidate_id"], "same-distance-a")
        self.assertEqual(row["local_context"]["accepted_candidate_count"], 3)

    def test_compact_is_stable_and_source_is_not_mutated(self) -> None:
        source = _source([_frame(raw=1, rejected=[_candidate("far", 300, 300, reason="outside_pitch")])])
        original = copy.deepcopy(source)
        first = analyze_operator_ball_detector_misses([_anchor()], {"source-one": source})
        second = analyze_operator_ball_detector_misses([_anchor()], {"source-one": source})
        self.assertEqual(source, original)
        self.assertEqual(compact_operator_ball_detector_miss_analysis(first), compact_operator_ball_detector_miss_analysis(second))
        self.assertEqual(first["summary"]["rejection_reason_counts"], {"outside_pitch": 1})

    def test_analysis_leaves_source_artifact_directory_byte_for_byte_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source-match"
            root.mkdir()
            candidates = root / "ball_candidates.json"
            tracks = root / "ball_tracks.json"
            candidates.write_text(json.dumps(_source([_frame(raw=0)])["ball_candidates"]), encoding="utf-8")
            tracks.write_text(json.dumps({"positions": []}), encoding="utf-8")
            before = {path.name: path.read_bytes() for path in root.iterdir()}
            source = _source([_frame(raw=0)])
            source["ball_candidates"] = json.loads(candidates.read_text(encoding="utf-8"))
            source["ball_tracks"] = json.loads(tracks.read_text(encoding="utf-8"))
            analyze_operator_ball_detector_misses([_anchor()], {"source-one": source})
            self.assertEqual({path.name: path.read_bytes() for path in root.iterdir()}, before)

    def test_missing_video_evidence_is_reported_only_in_requested_directory(self) -> None:
        report, source = _report(_frame(raw=0)), _source([_frame(raw=0)])
        with tempfile.TemporaryDirectory() as temporary:
            evidence_dir = Path(temporary) / "operator-evidence"
            result = export_detector_miss_evidence(report, {"source-one": source}, evidence_dir)
            self.assertEqual(result[0]["status"], "unavailable")
            self.assertEqual(result[0]["reason"], "video_unavailable")
            self.assertTrue(evidence_dir.is_dir())
            self.assertEqual(list(evidence_dir.iterdir()), [])
