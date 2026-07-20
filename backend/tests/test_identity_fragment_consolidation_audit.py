from __future__ import annotations

import unittest

from app.services.identity_fragment_consolidation_audit import (
    build_identity_fragment_consolidation_audit_manifest,
)


class IdentityFragmentConsolidationAuditTests(unittest.TestCase):
    def test_builds_reviewable_manifest_with_video_offset(self) -> None:
        manifest = build_identity_fragment_consolidation_audit_manifest(
            {
                "algorithm": {"name": "consolidation"},
                "proposals": [
                    {
                        "proposal_key": "link:1",
                        "source_candidate_subject_id": "source-subject",
                        "target_candidate_subject_id": "target-subject",
                        "source_candidate_player_id": "A01",
                        "target_candidate_player_id": "A01~2",
                        "source_team_label": "A",
                        "target_team_label": "A",
                        "source_end_frame": 8100,
                        "target_start_frame": 8131,
                        "gap_frames": 30,
                        "gap_seconds": 1.0,
                        "confidence": 0.8,
                        "endpoint_distance_m": 2.0,
                        "required_speed_mps": 2.0,
                        "shared_production_anchor": "slot-A01",
                        "evidence": ["same_team"],
                        "reason_codes": [],
                        "source_endpoint": {
                            "frame": 8100,
                            "pitch_m": [1.0, 2.0],
                            "bbox_xyxy": [10, 20, 30, 60],
                            "confidence": 0.9,
                        },
                        "target_endpoint": {
                            "frame": 8131,
                            "pitch_m": [2.0, 3.0],
                            "bbox_xyxy": [11, 21, 31, 61],
                            "confidence": 0.8,
                        },
                    }
                ],
            },
            benchmark_id="hard",
            benchmark_label="hard3m",
            video_path="clip.mp4",
            video_time_offset_sec=270.0,
            generated_at="fixed",
        )

        self.assertEqual(manifest["summary"]["review_items"], 1)
        item = manifest["items"][0]
        self.assertEqual(item["source"]["tracklet_id"], "A01")
        self.assertAlmostEqual(item["source"]["video_time_sec"], 0.0)
        self.assertAlmostEqual(item["target"]["video_time_sec"], 1.033, places=3)
        self.assertEqual(item["manual_review"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
