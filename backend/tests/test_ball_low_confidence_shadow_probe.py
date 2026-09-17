from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from evaluation.ball_low_confidence_shadow_probe import (
    compact_probe_report,
    normalize_thresholds,
    probe_low_confidence,
    render_probe_markdown,
)
from scripts.probe_ball_low_confidence import _file_sha256, _production_model_identity


def _anchor(*, shot_id: str = "s", time_sec: float = 10.0, source_match_id: str = "m") -> dict:
    return {
        "canonical_shot_id": shot_id,
        "published_id": "p",
        "source_match_id": source_match_id,
        "source_time_sec": time_sec,
        "logical_frame_time_sec": time_sec,
        "x_px": 100.0,
        "y_px": 100.0,
        "frame_width": 1920.0,
        "frame_height": 1080.0,
    }


def _candidate(
    candidate_id: str,
    *,
    x: float = 100.0,
    y: float = 100.0,
    confidence: float = 0.1,
    reason: str | None = None,
) -> dict:
    row = {
        "candidate_id": candidate_id,
        "position_px": [x, y],
        "position_m": [x / 10, y / 10],
        "width_px": 10,
        "height_px": 10,
        "confidence": confidence,
    }
    if reason:
        row["reason"] = reason
    return row


def _source(*, include_control: bool = False, ball_conf: float = .03) -> dict:
    frames = [{"frame": 100, "time_sec": 10.0, "raw_predictions": [], "candidates": []}]
    if include_control:
        frames.append({"frame": 200, "time_sec": 20.0, "raw_predictions": [{}], "candidates": [_candidate("control")]})
    return {
        "fps": 10.0,
        "ball_selection_policy": "ball-selection:v1",
        "ball_candidates": {"parameters": {"ball_conf": ball_conf}, "frames": frames},
        "ball_tracks": {"positions": []},
    }


def _run_frame(
    *,
    raw: list[dict] | None = None,
    accepted: list[dict] | None = None,
    rejected: list[dict] | None = None,
    frame: int = 100,
) -> dict:
    return {
        "fps": 10.0,
        "frames": [{
            "frame": frame,
            "time_sec": frame / 10,
            "raw_predictions": len(raw or []),
            "raw_prediction_rows": raw or [],
            "candidates": accepted or [],
            "rejected_candidates": rejected or [],
        }],
    }


class LowConfidenceShadowProbeTests(unittest.TestCase):
    def test_normalizes_thresholds_and_includes_production(self) -> None:
        self.assertEqual(normalize_thresholds([.01, .005], production_conf=.03), [.03, .01, .005])
        with self.assertRaises(ValueError):
            normalize_thresholds([0], production_conf=.03)

    def test_raw_recovery_keeps_filter_outcome(self) -> None:
        def runner(_source: dict, threshold: float, _window: float) -> dict:
            raw = [] if threshold >= .01 else [_candidate("raw", confidence=.008)]
            return _run_frame(raw=raw)

        report = probe_low_confidence([_anchor()], {"m": _source()}, thresholds=[.03, .01, .005], runner=runner)
        recovery = report["anchors"][0]["recovery"]
        self.assertEqual(
            (recovery["classification"], recovery["first_recovery_threshold"], recovery["recovered_prediction_confidence"]),
            ("RECOVERED_RAW_BUT_FILTERED", .005, .008),
        )

    def test_accepted_local_continuity_and_ambiguity_are_distinct(self) -> None:
        def continuity_runner(_source: dict, threshold: float, _window: float) -> dict:
            if threshold == .03:
                return _run_frame()
            accepted = [_candidate("accepted", confidence=.005)]
            result = _run_frame(raw=[_candidate("raw", confidence=.005)], accepted=accepted)
            result["frames"].append({"frame": 101, "time_sec": 10.1, "raw_predictions": 1, "raw_prediction_rows": [], "candidates": [_candidate("next", x=102, confidence=.004)], "rejected_candidates": []})
            return result

        continuity = probe_low_confidence([_anchor()], {"m": _source()}, thresholds=[.03, .005], runner=continuity_runner)
        self.assertEqual(continuity["anchors"][0]["recovery"]["classification"], "RECOVERED_ACCEPTED_WITH_LOCAL_CONTINUITY")

        def ambiguous_runner(_source: dict, threshold: float, _window: float) -> dict:
            if threshold == .03:
                return _run_frame()
            rows = [_candidate("one", confidence=.005), _candidate("two", x=102, confidence=.004)]
            return _run_frame(raw=rows, accepted=rows)

        ambiguous = probe_low_confidence([_anchor()], {"m": _source()}, thresholds=[.03, .005], runner=ambiguous_runner)
        self.assertEqual(ambiguous["anchors"][0]["recovery"]["classification"], "AMBIGUOUS_LOW_CONFIDENCE_RECOVERY")

    def test_impossible_nearby_pitch_positions_are_not_continuity(self) -> None:
        def runner(_source: dict, threshold: float, _window: float) -> dict:
            if threshold == .03:
                return _run_frame()
            result = _run_frame(raw=[_candidate("raw", confidence=.005)], accepted=[_candidate("seed", confidence=.005)])
            result["frames"].append({"frame": 101, "time_sec": 10.1, "raw_predictions": 1, "raw_prediction_rows": [], "candidates": [_candidate("far", x=1000, y=1000, confidence=.005)], "rejected_candidates": []})
            return result

        report = probe_low_confidence([_anchor()], {"m": _source()}, thresholds=[.03, .005], runner=runner)
        threshold = report["anchors"][0]["thresholds"][1]
        self.assertFalse(threshold["local_continuity"]["plausible"])
        self.assertEqual(report["anchors"][0]["recovery"]["classification"], "RECOVERED_ACCEPTED_ISOLATED")

    def test_control_replay_mismatch_is_explicit(self) -> None:
        report = probe_low_confidence(
            [_anchor()],
            {"m": _source()},
            thresholds=[.03],
            runner=lambda _source, _threshold, _window: _run_frame(raw=[_candidate("unexpected")]),
        )
        self.assertEqual(report["anchors"][0]["recovery"]["classification"], "CONTROL_RUNTIME_MISMATCH")

    def test_controls_are_swept_but_not_relabelled_as_detector_misses(self) -> None:
        calls: list[tuple[float, float]] = []

        def runner(source: dict, threshold: float, _window: float) -> dict:
            calls.append((source["_anchor_time"], threshold))
            return _run_frame(frame=int(source["_anchor_time"] * 10))

        report = probe_low_confidence(
            [_anchor(), _anchor(shot_id="control", time_sec=20.0)],
            {"m": _source(include_control=True)},
            thresholds=[.03, .01],
            runner=runner,
        )
        self.assertEqual(len(calls), 4)
        self.assertEqual([row["role"] for row in report["anchors"]], ["raw_detector_miss", "control"])
        self.assertIsNone(report["anchors"][1]["recovery"])

    def test_controls_alone_still_mark_inference_as_invoked(self) -> None:
        source = _source(include_control=True)
        report = probe_low_confidence(
            [_anchor(shot_id="control", time_sec=20.0)],
            {"m": source},
            thresholds=[.03],
            runner=lambda _source, _threshold, _window: _run_frame(frame=200),
        )
        self.assertEqual(report["anchors"][0]["role"], "control")
        self.assertTrue(report["inference_invoked"])

    def test_source_specific_production_confidence_and_cost_multipliers(self) -> None:
        def runner(source: dict, threshold: float, _window: float) -> dict:
            rows = [_candidate(f"raw-{threshold}", confidence=threshold)]
            return _run_frame(raw=rows, accepted=rows)

        report = probe_low_confidence(
            [_anchor(source_match_id="m"), _anchor(shot_id="s2", source_match_id="n")],
            {"m": _source(ball_conf=.03), "n": _source(ball_conf=.05)},
            thresholds=[.01],
            runner=runner,
        )
        self.assertEqual(report["production_ball_conf_by_source"], {"m": .03, "n": .05})
        self.assertEqual([row["production_ball_conf"] for row in report["anchors"]], [.03, .05])
        summary = {row["threshold"]: row for row in report["threshold_summary"]}
        self.assertEqual(summary[.03]["raw_candidate_multiplier_vs_production"], 1.0)
        self.assertIn("control_anchor_count", summary[.01])
        self.assertIn("frames_with_more_than_three_accepted_candidates", summary[.01])
        self.assertTrue(report["inference_invoked"])

    def test_compact_and_markdown_are_stable_review_outputs(self) -> None:
        report = probe_low_confidence(
            [_anchor()],
            {"m": _source()},
            thresholds=[.03],
            runner=lambda _source, _threshold, _window: _run_frame(),
        )
        compact = compact_probe_report(report)
        self.assertNotIn("nearest_raw_prediction", str(compact))
        self.assertIn("NOT_RECOVERED", render_probe_markdown(report))

    def test_model_identity_requires_persisted_run_metadata_and_fingerprints_weights(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "match.json").write_text(json.dumps({"latest_analysis_run_id": "run-one"}))
            (root / "analysis_report.json").write_text(json.dumps({"run_id": "run-one"}))
            with self.assertRaisesRegex(ValueError, "production_ball_model_identity_unavailable"):
                _production_model_identity(root)

            manifest = root / "analysis_runs" / "run-one"
            manifest.mkdir(parents=True)
            (manifest / "run_metadata.json").write_text(json.dumps({"parameters": {"ball_yolo_model": "models/best.pt"}}))
            identity = _production_model_identity(root)
            self.assertEqual(identity["persisted_ball_yolo_model"], "models/best.pt")
            self.assertTrue(identity["model_file_exists"])
            self.assertEqual(identity["model_sha256"], _file_sha256(Path(identity["resolved_local_path"])))

    def test_model_identity_rejects_missing_local_weights(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "match.json").write_text(json.dumps({"latest_analysis_run_id": "run-one"}))
            (root / "analysis_report.json").write_text(json.dumps({"run_id": "run-one"}))
            manifest = root / "analysis_runs" / "run-one"
            manifest.mkdir(parents=True)
            (manifest / "run_metadata.json").write_text(json.dumps({"parameters": {"ball_yolo_model": "models/missing-ball.pt"}}))
            with self.assertRaisesRegex(FileNotFoundError, "production_ball_model_file_unavailable"):
                _production_model_identity(root)

    def test_probe_does_not_mutate_source_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "ball_candidates.json"
            artifact.write_text('{"preserved":true}', encoding="utf-8")
            before = artifact.read_bytes()
            source = _source()
            source["match_root"] = str(root)
            probe_low_confidence(
                [_anchor()],
                {"m": source},
                thresholds=[.03],
                runner=lambda _source, _threshold, _window: _run_frame(),
            )
            self.assertEqual(artifact.read_bytes(), before)
