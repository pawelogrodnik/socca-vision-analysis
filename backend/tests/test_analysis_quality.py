from __future__ import annotations

import unittest

from app.services.analysis_quality import build_analysis_quality_report


class AnalysisQualityTests(unittest.TestCase):
    def test_quality_report_marks_clean_analysis_high_quality(self) -> None:
        frames = [
            {
                "frame": frame,
                "time_sec": frame / 30,
                "raw_detections": 14,
                "visible_stable_boxes": 14,
                "trusted_detected": 14,
                "visual_interpolated_boxes": 0,
                "predicted_visible_boxes": 0,
                "slot_missing": 0,
                "slot_ambiguous": 0,
            }
            for frame in range(10)
        ]

        report = build_analysis_quality_report(
            frame_detection_counts={"target_players": 14, "summary": {"frames": 10}, "frames": frames},
            stable_players={
                "summary": {
                    "stable_players": 14,
                    "team_counts": {"A": 7, "B": 7},
                    "low_confidence_players": 0,
                }
            },
            global_identity_report={"summary": {"blocked_identity_switches": 0}},
            tracking_quality_report={"summary": {"frames_with_team_over_cap": 0}},
            movement_stats={"summary": {"players_low_quality": 0, "players_medium_quality": 0}},
            player_stats={"summary": {"estimated_distance_ratio": 0.0}},
            team_stats={"teams": [{"team_label": "A", "locked": True}, {"team_label": "B", "locked": True}]},
        )

        self.assertEqual(report["schema_version"], "0.1.0")
        self.assertEqual(report["quality"], "high")
        self.assertGreaterEqual(report["score"], 85)
        self.assertEqual(report["components"]["tracking"]["quality"], "high")
        self.assertEqual(report["summary"]["visible_avg"], 14.0)
        self.assertEqual(report["summary"]["ghost_bbox_count"], 0)
        self.assertEqual(report["warnings"], [])

    def test_quality_report_flags_low_visible_and_ghost_boxes(self) -> None:
        frames = [
            {
                "frame": frame,
                "time_sec": frame / 30,
                "raw_detections": 8,
                "visible_stable_boxes": 6,
                "trusted_detected": 5,
                "visual_interpolated_boxes": 1,
                "predicted_visible_boxes": 2,
                "slot_missing": 3,
                "slot_ambiguous": 1 if frame in {3, 4} else 0,
            }
            for frame in range(10)
        ]

        report = build_analysis_quality_report(
            frame_detection_counts={
                "target_players": 14,
                "summary": {"frames": 10, "ghost_bbox_count": 20},
                "frames": frames,
            },
            stable_players={
                "summary": {
                    "stable_players": 12,
                    "team_counts": {"A": 7, "B": 4, "U": 1},
                    "low_confidence_players": 2,
                }
            },
            global_identity_report={"summary": {"blocked_identity_switches": 2, "rejected_candidates": 5}},
            tracking_quality_report={"summary": {"frames_with_team_over_cap": 2}},
            movement_stats={"summary": {"players_low_quality": 1, "players_medium_quality": 3}},
            player_stats={"summary": {"estimated_distance_ratio": 0.35, "rejected_sprint_candidate_count": 4}},
            team_stats={"teams": [{"team_label": "A", "locked": False}, {"team_label": "B", "locked": True}]},
        )

        self.assertEqual(report["quality"], "low")
        self.assertLess(report["score"], 60)
        self.assertEqual(report["components"]["tracking"]["quality"], "low")
        self.assertEqual(report["summary"]["low_visible_frames"], 10)
        self.assertEqual(report["summary"]["predicted_visible_boxes"], 20)
        self.assertEqual(report["summary"]["ghost_bbox_count"], 20)
        self.assertTrue(report["frame_ranges"]["ambiguous"])
        self.assertTrue(report["top_problem_frames"])
        self.assertTrue(any("Low average visible stable boxes" in warning for warning in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
