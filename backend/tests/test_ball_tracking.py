from __future__ import annotations

import unittest

import numpy as np

from app.services.ball_tracking import (
    BALL_CUSTOM_SOURCE,
    build_ball_quality_report,
    build_ball_positions,
    build_ball_tracks_document,
    extract_ball_candidates,
    refine_ball_tracks_against_players,
    reprocess_ball_candidates_document,
    select_ball_detections,
    _ball_result,
    _draw_ball_position,
    _resolve_ball_model_classes,
)


def candidate(frame: int, x: float, y: float, confidence: float = 0.7) -> dict:
    return {
        "candidate_id": f"c-{frame}",
        "frame": frame,
        "time_sec": round(frame / 30, 3),
        "bbox_xyxy": [x - 2, y - 2, x + 2, y + 2],
        "position_px": [x, y],
        "position_m": [x, y],
        "confidence": confidence,
        "source": "detected",
    }


def named_candidate(candidate_id: str, frame: int, x: float, y: float, confidence: float = 0.7) -> dict:
    return {**candidate(frame, x, y, confidence), "candidate_id": candidate_id}


class FakeCameraMotion:
    enabled = True
    reference_frame = 0

    def transform_point(self, _frame_idx: int, point: list[float]) -> list[float]:
        return [round(float(point[0]) - 20.0, 2), round(float(point[1]), 2)]

    def metadata_for_frame(self, _frame_idx: int) -> dict:
        return {"camera_motion_status": "ok", "camera_motion_inlier_ratio": 0.9}


class BallTrackingTests(unittest.TestCase):
    def test_ball_result_skips_overlay_artifact_when_disabled(self) -> None:
        calls: list[str] = []

        result = _ball_result({}, {}, {}, {}, include_overlay=False, overlay_writer=lambda: calls.append("overlay"))

        self.assertEqual(calls, [])
        self.assertNotIn("ball_overlay_preview", result["artifacts"])

    def test_draw_ball_position_draws_bbox_when_available(self) -> None:
        frame = np.zeros((40, 40, 3), dtype=np.uint8)

        _draw_ball_position(
            frame,
            {
                "position_px": [15, 17],
                "bbox_xyxy": [10, 12, 20, 22],
                "source": "detected",
                "confidence": 0.7,
            },
        )

        self.assertGreater(int(frame[12, 10].sum()), 0)

    def test_resolve_ball_model_classes_accepts_one_class_custom_model(self) -> None:
        model = type("Model", (), {"names": {0: "ball"}})()

        resolved = _resolve_ball_model_classes(model)

        self.assertEqual(resolved["source"], BALL_CUSTOM_SOURCE)
        self.assertEqual(resolved["class_ids"], [0])
        self.assertEqual(resolved["class_names"], ["ball"])
        self.assertEqual(resolved["resolution"], "class_name_match")

    def test_extract_ball_candidates_keeps_small_inside_pitch_box(self) -> None:
        pitch_polygon = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=np.float32)
        candidates, rejected = extract_ball_candidates(
            np.array([[8, 8, 12, 12]], dtype=np.float32),
            np.array([0.42], dtype=np.float32),
            class_ids=np.array([0], dtype=np.float32),
            class_names={0: "ball"},
            frame_idx=3,
            fps=30,
            pitch_polygon=pitch_polygon,
            homography=np.eye(3, dtype=np.float32),
            frame_size=(100, 100),
        )

        self.assertEqual(len(rejected), 0)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source"], "detected")
        self.assertEqual(candidates[0]["position_px"], [10.0, 10.0])
        self.assertEqual(candidates[0]["position_m"], [10.0, 10.0])
        self.assertEqual(candidates[0]["class_id"], 0)
        self.assertEqual(candidates[0]["class_name"], "ball")

    def test_extract_ball_candidates_rejects_outside_pitch(self) -> None:
        pitch_polygon = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=np.float32)
        candidates, rejected = extract_ball_candidates(
            np.array([[108, 8, 112, 12]], dtype=np.float32),
            np.array([0.5], dtype=np.float32),
            frame_idx=3,
            fps=30,
            pitch_polygon=pitch_polygon,
            homography=np.eye(3, dtype=np.float32),
            frame_size=(160, 120),
        )

        self.assertEqual(candidates, [])
        self.assertEqual(rejected[0]["reason"], "outside_pitch")

    def test_extract_ball_candidates_uses_calibrated_center_for_pitch_position(self) -> None:
        pitch_polygon = np.array([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=np.float32)
        candidates, rejected = extract_ball_candidates(
            np.array([[108, 8, 112, 12]], dtype=np.float32),
            np.array([0.5], dtype=np.float32),
            frame_idx=3,
            fps=30,
            pitch_polygon=pitch_polygon,
            homography=np.eye(3, dtype=np.float32),
            frame_size=(160, 120),
            camera_motion=FakeCameraMotion(),
        )

        self.assertEqual(rejected, [])
        self.assertEqual(candidates[0]["position_px"], [110.0, 10.0])
        self.assertEqual(candidates[0]["calibrated_position_px"], [90.0, 10.0])
        self.assertEqual(candidates[0]["position_m"], [90.0, 10.0])

    def test_reprocess_ball_candidates_recovers_previously_rejected_candidate(self) -> None:
        doc = {
            "frames": [
                {
                    "frame": 0,
                    "candidates": [],
                    "rejected_candidates": [
                        {
                            "candidate_id": "ball-f000000-c00",
                            "frame": 0,
                            "time_sec": 0.0,
                            "bbox_xyxy": [105.0, 8.0, 109.0, 12.0],
                            "position_px": [107.0, 10.0],
                            "confidence": 0.81,
                            "width_px": 4.0,
                            "height_px": 4.0,
                            "area_px": 16.0,
                            "reason": "outside_pitch",
                        }
                    ],
                }
            ],
            "parameters": {},
        }

        reprocessed = reprocess_ball_candidates_document(
            doc,
            pitch_polygon=np.asarray([[0, 0], [100, 0], [100, 100], [0, 100]], dtype=np.float32),
            homography=np.eye(3, dtype=np.float32),
            frame_size=(120, 120),
            fps=30.0,
            camera_motion=FakeCameraMotion(),
        )

        frame = reprocessed["frames"][0]
        self.assertEqual(len(frame["candidates"]), 1)
        self.assertEqual(frame["candidates"][0]["original_filter_status"], "rejected")
        self.assertEqual(frame["candidates"][0]["reprocessed_filter_status"], "accepted")
        self.assertEqual(frame["candidates"][0]["calibrated_position_px"], [87.0, 10.0])

    def test_ball_positions_interpolate_short_gap(self) -> None:
        selected = {
            0: candidate(0, 10.0, 10.0),
            12: candidate(12, 11.2, 10.0),
        }
        positions, gaps = build_ball_positions(
            selected,
            processed_frames=[0, 6, 12],
            fps=30,
            max_interpolation_gap_sec=0.5,
            max_interpolation_speed_mps=35.0,
        )

        self.assertEqual([item["source"] for item in positions], ["detected", "interpolated", "detected"])
        self.assertEqual(positions[1]["position_m"], [10.6, 10.0])
        self.assertEqual(len(gaps), 1)

    def test_ball_positions_do_not_interpolate_long_gap(self) -> None:
        selected = {
            0: candidate(0, 10.0, 10.0),
            30: candidate(30, 12.0, 10.0),
        }
        positions, gaps = build_ball_positions(
            selected,
            processed_frames=[0, 15, 30],
            fps=30,
            max_interpolation_gap_sec=0.5,
            max_interpolation_speed_mps=35.0,
        )

        self.assertEqual([item["source"] for item in positions], ["detected", "unknown", "detected"])
        self.assertEqual(gaps, [])

    def test_select_ball_detections_rejects_impossible_jump(self) -> None:
        selected = select_ball_detections(
            [
                {"frame": 0, "candidates": [candidate(0, 0.0, 0.0)]},
                {"frame": 1, "candidates": [candidate(1, 40.0, 0.0)]},
            ],
            fps=30,
            max_link_speed_mps=35.0,
            min_start_conf=0.02,
        )

        self.assertIn(0, selected)
        self.assertNotIn(1, selected)

    def test_ball_tracks_filters_short_recovery_segment_after_gap(self) -> None:
        doc = build_ball_tracks_document(
            [
                {"frame": 0, "candidates": [candidate(0, 0.0, 0.0, confidence=0.7)]},
                {"frame": 45, "candidates": [candidate(45, 10.0, 10.0, confidence=0.3)]},
                {"frame": 46, "candidates": [candidate(46, 10.1, 10.0, confidence=0.3)]},
            ],
            processed_frames=[0, 45, 46],
            fps=30,
            parameters={
                "max_link_speed_mps": 2.0,
                "min_start_conf": 0.08,
                "max_interpolation_gap_sec": 0.5,
                "max_interpolation_speed_mps": 22.0,
                "recovery_segment_min_detections": 3,
                "recovery_segment_min_duration_sec": 0.15,
            },
        )

        self.assertEqual([item["source"] for item in doc["positions"]], ["detected", "unknown", "unknown"])

    def test_select_ball_detections_restarts_after_low_confidence_hijack(self) -> None:
        selected = select_ball_detections(
            [
                {"frame": 0, "candidates": [candidate(0, 0.0, 0.0, confidence=0.1)]},
                {"frame": 1, "candidates": [candidate(1, 0.1, 0.0, confidence=0.1)]},
                {"frame": 2, "candidates": [candidate(2, 0.2, 0.0, confidence=0.1)]},
                {
                    "frame": 3,
                    "candidates": [
                        candidate(3, 0.3, 0.0, confidence=0.1),
                        candidate(3, 50.0, 10.0, confidence=0.79),
                    ],
                },
                {"frame": 4, "candidates": [candidate(4, 50.2, 10.0, confidence=0.8)]},
                {"frame": 5, "candidates": [candidate(5, 50.4, 10.0, confidence=0.81)]},
            ],
            fps=30,
            max_link_speed_mps=35.0,
            min_start_conf=0.08,
        )

        self.assertEqual(selected[3]["position_m"], [50.0, 10.0])
        self.assertEqual(selected[3]["segment_start_reason"], "after_low_confidence_hijack")

    def test_v2_keeps_short_low_confidence_continuation_on_active_path(self) -> None:
        selected = select_ball_detections(
            [
                {"frame": 0, "candidates": [candidate(0, 0.0, 0.0, confidence=0.8)]},
                {"frame": 1, "candidates": [candidate(1, 0.4, 0.0, confidence=0.8)]},
                {"frame": 2, "candidates": [candidate(2, 0.8, 0.0, confidence=0.2)]},
                {"frame": 3, "candidates": [candidate(3, 1.2, 0.0, confidence=0.8)]},
            ],
            fps=30,
            max_link_speed_mps=22.0,
            min_start_conf=0.08,
            policy_version="ball-selection:v2",
        )

        self.assertEqual([selected[frame]["position_m"] for frame in sorted(selected)], [[0.0, 0.0], [0.4, 0.0], [0.8, 0.0], [1.2, 0.0]])
        self.assertEqual(selected[2]["selection_reason"], "temporal_active_continuation")

    def test_v2_rejects_low_confidence_spatial_hijack(self) -> None:
        selected = select_ball_detections(
            [
                {"frame": 0, "candidates": [candidate(0, 0.0, 0.0, confidence=0.8)]},
                {"frame": 1, "candidates": [candidate(1, 0.4, 0.0, confidence=0.8)]},
                {"frame": 2, "candidates": [candidate(2, 10.0, 0.0, confidence=0.2)]},
                {"frame": 3, "candidates": [candidate(3, 1.2, 0.0, confidence=0.8)]},
            ],
            fps=30,
            max_link_speed_mps=22.0,
            min_start_conf=0.08,
            policy_version="ball-selection:v2",
        )

        self.assertNotIn(2, selected)
        self.assertEqual(selected[3]["position_m"], [1.2, 0.0])

    def test_v2_keeps_active_motion_before_coherent_high_confidence_restart(self) -> None:
        frames = [
            {"frame": 0, "candidates": [named_candidate("active-0", 0, 0.0, 0.0, 0.8)]},
            {"frame": 1, "candidates": [named_candidate("active-1", 1, 0.4, 0.0, 0.8)]},
            {"frame": 2, "candidates": [named_candidate("active-low", 2, 0.8, 0.0, 0.12)]},
            {
                "frame": 3,
                "candidates": [
                    named_candidate("other-stationary", 3, 0.4, 0.0, 0.9),
                    named_candidate("active-3", 3, 1.2, 0.0, 0.35),
                ],
            },
            {
                "frame": 4,
                "candidates": [
                    named_candidate("other-stationary", 4, 0.4, 0.0, 0.9),
                    named_candidate("active-4", 4, 1.6, 0.0, 0.35),
                ],
            },
            {
                "frame": 5,
                "candidates": [
                    named_candidate("other-stationary", 5, 0.4, 0.0, 0.9),
                    named_candidate("active-5", 5, 2.0, 0.0, 0.35),
                ],
            },
        ]

        v1_selected = select_ball_detections(
            frames,
            fps=30,
            max_link_speed_mps=22.0,
            min_start_conf=0.08,
            policy_version="ball-selection:v1",
        )
        v2_selected = select_ball_detections(
            frames,
            fps=30,
            max_link_speed_mps=22.0,
            min_start_conf=0.08,
            policy_version="ball-selection:v2",
        )

        self.assertEqual(v1_selected[3]["candidate_id"], "other-stationary")
        self.assertEqual(v2_selected[3]["candidate_id"], "active-3")
        self.assertEqual(v2_selected[3]["selection_reason"], "temporal_active_continuation")

    def test_v2_trusted_prediction_is_not_dragged_by_low_confidence_last_row(self) -> None:
        selected = select_ball_detections(
            [
                {"frame": 0, "candidates": [named_candidate("active-0", 0, 0.0, 0.0, 0.8)]},
                {"frame": 1, "candidates": [named_candidate("active-1", 1, 0.4, 0.0, 0.8)]},
                {"frame": 2, "candidates": [named_candidate("active-low-off", 2, 0.95, 0.0, 0.12)]},
                {"frame": 3, "candidates": [named_candidate("active-3", 3, 1.2, 0.0, 0.6)]},
            ],
            fps=30,
            max_link_speed_mps=22.0,
            min_start_conf=0.08,
            policy_version="ball-selection:v2",
        )

        self.assertEqual(selected[3]["candidate_id"], "active-3")
        self.assertEqual(selected[3]["selection_details"]["prediction_error_m"], 0.0)

    def test_v2_keeps_legitimately_stationary_active_ball_without_competitor(self) -> None:
        selected = select_ball_detections(
            [
                {"frame": 0, "candidates": [candidate(0, 0.0, 0.0, confidence=0.7)]},
                {"frame": 1, "candidates": [candidate(1, 0.4, 0.0, confidence=0.7)]},
                {"frame": 2, "candidates": [candidate(2, 0.4, 0.0, confidence=0.7)]},
                {"frame": 3, "candidates": [candidate(3, 0.4, 0.0, confidence=0.7)]},
            ],
            fps=30,
            max_link_speed_mps=22.0,
            min_start_conf=0.08,
            policy_version="ball-selection:v2",
        )

        self.assertEqual(selected[3]["position_m"], [0.4, 0.0])

    def test_v2_retains_high_confidence_restart_after_real_gap(self) -> None:
        selected = select_ball_detections(
            [
                {"frame": 0, "candidates": [candidate(0, 0.0, 0.0, confidence=0.8)]},
                {"frame": 1, "candidates": [candidate(1, 0.4, 0.0, confidence=0.8)]},
                {"frame": 60, "candidates": [candidate(60, 100.0, 0.0, confidence=0.8)]},
                {"frame": 61, "candidates": [candidate(61, 100.2, 0.0, confidence=0.8)]},
                {"frame": 62, "candidates": [candidate(62, 100.4, 0.0, confidence=0.8)]},
            ],
            fps=30,
            max_link_speed_mps=22.0,
            min_start_conf=0.08,
            policy_version="ball-selection:v2",
        )

        self.assertEqual(selected[60]["segment_start_reason"], "after_impossible_high_conf_run")

    def test_v2_does_not_bridge_explicit_unknown_gap_beyond_existing_limit(self) -> None:
        doc = build_ball_tracks_document(
            [
                {"frame": 0, "candidates": [candidate(0, 0.0, 0.0, confidence=0.8)]},
                {"frame": 15, "candidates": []},
                {"frame": 30, "candidates": [candidate(30, 1.0, 0.0, confidence=0.8)]},
            ],
            processed_frames=[0, 15, 30],
            fps=30,
            parameters={"ball_selection_policy": "ball-selection:v2", "max_link_speed_mps": 22.0, "min_start_conf": 0.08},
        )

        self.assertEqual(doc["positions"][1]["source"], "unknown")

    def test_v2_interpolation_stays_bounded_inside_existing_legal_gap(self) -> None:
        doc = build_ball_tracks_document(
            [
                {"frame": 0, "candidates": [candidate(0, 0.0, 0.0, confidence=0.8)]},
                {"frame": 6, "candidates": []},
                {"frame": 12, "candidates": [candidate(12, 1.2, 0.0, confidence=0.8)]},
            ],
            processed_frames=[0, 6, 12],
            fps=30,
            parameters={"ball_selection_policy": "ball-selection:v2", "max_link_speed_mps": 22.0, "min_start_conf": 0.08},
        )

        self.assertEqual([row["source"] for row in doc["positions"]], ["detected", "interpolated", "detected"])

    def test_ball_tracks_document_records_versioned_selection_policy(self) -> None:
        doc = build_ball_tracks_document(
            [{"frame": 0, "candidates": [candidate(0, 0.0, 0.0, confidence=0.8)]}],
            processed_frames=[0],
            fps=30,
            parameters={"ball_selection_policy": "ball-selection:v2"},
        )

        self.assertEqual(doc["parameters"]["ball_selection_policy"], "ball-selection:v2")
        self.assertEqual(doc["selection_diagnostics"]["policy_version"], "ball-selection:v2")
        self.assertEqual(doc["selection_diagnostics"]["method"], "temporal_active_ball_selection_v2")
        self.assertEqual(doc["positions"][0]["selection_reason"], "initial_or_after_gap")

    def test_v2_has_deterministic_candidate_tie_breaking(self) -> None:
        frames = [
            {"frame": 0, "candidates": [named_candidate("start", 0, 0.0, 0.0, 0.8)]},
            {"frame": 1, "candidates": [named_candidate("next", 1, 0.4, 0.0, 0.8)]},
            {
                "frame": 2,
                "candidates": [
                    named_candidate("z-candidate", 2, 0.8, 0.0, 0.6),
                    named_candidate("a-candidate", 2, 0.8, 0.0, 0.6),
                ],
            },
        ]

        first = select_ball_detections(frames, fps=30, max_link_speed_mps=22.0, min_start_conf=0.08, policy_version="ball-selection:v2")
        second = select_ball_detections(frames, fps=30, max_link_speed_mps=22.0, min_start_conf=0.08, policy_version="ball-selection:v2")

        self.assertEqual(first[2]["candidate_id"], "a-candidate")
        self.assertEqual(first[2]["candidate_id"], second[2]["candidate_id"])

    def test_refine_ball_tracks_suppresses_player_bbox_false_positive_after_gap(self) -> None:
        ball_doc = {
            "parameters": {
                "max_interpolation_gap_sec": 0.5,
                "max_interpolation_speed_mps": 22.0,
                "recovery_segment_min_detections": 1,
                "recovery_segment_min_duration_sec": 0.0,
            },
            "summary": {},
            "positions": [
                {**candidate(0, 0.0, 0.0, confidence=0.7), "selection_reason": "temporal_active_continuation"},
                candidate(30, 10.0, 10.0, confidence=0.3),
            ],
            "interpolation_gaps": [],
        }
        stable_doc = {
            "players": [
                {
                    "stable_player_id": "B01",
                    "team_label": "B",
                    "overlay_positions": [
                        {"frame": 30, "bbox_xyxy": [8, 8, 12, 14], "source": "detected"}
                    ],
                }
            ]
        }

        refined = refine_ball_tracks_against_players(ball_doc, stable_doc, fps=30)

        self.assertEqual([item["source"] for item in refined["positions"]], ["detected", "unknown"])
        self.assertEqual(refined["summary"]["player_overlap_suppressed_detections"], 1)
        self.assertEqual(refined["positions"][0]["selection_reason"], "temporal_active_continuation")

    def test_ball_tracks_document_exports_required_position_fields(self) -> None:
        doc = build_ball_tracks_document(
            [
                {"frame": 0, "candidates": [candidate(0, 0.0, 0.0)]},
                {"frame": 1, "candidates": []},
            ],
            processed_frames=[0, 1],
            fps=30,
            parameters={
                "max_link_speed_mps": 35.0,
                "min_start_conf": 0.02,
                "max_interpolation_gap_sec": 0.5,
                "max_interpolation_speed_mps": 35.0,
            },
        )

        self.assertEqual(doc["schema_version"], "0.1.0")
        self.assertEqual(doc["summary"]["processed_frames"], 2)
        for position in doc["positions"]:
            self.assertIn("time_sec", position)
            self.assertIn("position_px", position)
            self.assertIn("position_m", position)
            self.assertIn(position["source"], {"detected", "interpolated", "predicted", "unknown"})
            self.assertIn("confidence", position)

    def test_quality_report_recommends_custom_dataset_for_low_coverage(self) -> None:
        tracks_doc = {
            "summary": {
                "processed_frames": 60,
                "detected_frames": 12,
                "interpolated_frames": 3,
                "known_coverage": 0.25,
                "detected_coverage": 0.2,
                "mean_detected_confidence": 0.12,
                "candidate_count": 14,
                "frames_with_candidates": 12,
                "rejected_candidate_count": 0,
            },
            "positions": [
                {"frame": frame, "time_sec": frame / 30, "source": "detected" if frame < 12 else "unknown"}
                for frame in range(60)
            ],
        }
        report = build_ball_quality_report(
            tracks_doc,
            {"frames": [{"frame": frame, "candidates": [candidate(frame, 1, 1)]} for frame in range(12)]},
            {"warnings": []},
        )

        self.assertEqual(report["recommendation"]["decision"], "custom_dataset_likely_needed")
        self.assertTrue(report["recommendation"]["custom_dataset_recommended"])
        self.assertGreater(report["summary"]["longest_unknown_streak_frames"], 30)

    def test_quality_report_allows_coco_for_high_coverage(self) -> None:
        tracks_doc = {
            "summary": {
                "processed_frames": 80,
                "detected_frames": 68,
                "interpolated_frames": 4,
                "known_coverage": 0.9,
                "detected_coverage": 0.85,
                "mean_detected_confidence": 0.32,
                "candidate_count": 70,
                "frames_with_candidates": 68,
                "rejected_candidate_count": 2,
            },
            "positions": [
                {"frame": frame, "time_sec": frame / 30, "source": "detected" if frame < 68 else "interpolated"}
                for frame in range(80)
            ],
        }
        report = build_ball_quality_report(
            tracks_doc,
            {"frames": [{"frame": frame, "candidates": [candidate(frame, 1, 1)]} for frame in range(68)]},
            {"warnings": []},
        )

        self.assertEqual(report["recommendation"]["decision"], "ball_detector_usable_for_next_experiments")
        self.assertFalse(report["recommendation"]["custom_dataset_recommended"])


if __name__ == "__main__":
    unittest.main()
