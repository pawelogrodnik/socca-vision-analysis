from __future__ import annotations

import unittest

from app.services.identity_review_queue_diagnostics import summarize_review_queue


class ReviewQueueDiagnosticsTests(unittest.TestCase):
    def test_summarizes_segment_fragmentation_and_whole_subject_reasons(self) -> None:
        progress = {
            "match_id": "m1",
            "review_units": [
                _segment("target-1", 1, 2),
                _segment("target-2", 4, 4),
                {
                    "candidate_subject_id": "whole",
                    "priority": "high",
                    "scope_kind": None,
                    "reason_codes": [
                        "review_card_conflict",
                        "semantic_identity_conflict",
                    ],
                },
                {"candidate_subject_id": "optional", "priority": "optional"},
            ],
        }
        review = {
            "targets": [
                _target("target-1", [1, 2]),
                _target("target-2", [4]),
            ]
        }
        cards = {
            "cards": [
                {
                    "candidate_subject_id": "whole",
                    "blockers": ["identity_conflict"],
                    "quality_flags": ["production_anchor_team_mismatch"],
                }
            ]
        }

        result = summarize_review_queue(
            {"id": "m1", "video": {"fps": 10.0}},
            progress,
            review,
            cards,
        )

        self.assertEqual(result["queue"]["high_priority_units"], 3)
        segments = result["high_priority_segments"]
        self.assertEqual(segments["total"], 2)
        self.assertEqual(segments["adjacent_gap_frames"], {"1": 1})
        self.assertEqual(segments["case_size_counts"]["one_frame"], 1)
        self.assertEqual(segments["unique_subject_tracklet_slot_team_groups"], 1)
        whole = result["high_priority_whole_subjects"]
        self.assertEqual(whole["total"], 1)
        self.assertEqual(whole["blocker_counts"], {"identity_conflict": 1})
        self.assertEqual(
            whole["quality_flag_counts"],
            {"production_anchor_team_mismatch": 1},
        )


def _segment(target_id: str, start: int, end: int) -> dict:
    return {
        "candidate_subject_id": "subject",
        "review_target_id": target_id,
        "scope_kind": "canonical_segment",
        "tracklet_ids": ["tracklet"],
        "stable_slot_id": "A03",
        "source_team_label": "A",
        "frame_start": start,
        "frame_end": end,
        "detected_frame_count": end - start + 1,
        "priority": "high",
    }


def _target(target_id: str, frames: list[int]) -> dict:
    return {
        "review_target_id": target_id,
        "candidate_subject_id": "subject",
        "tracklet_ids": ["tracklet"],
        "stable_slot_id": "A03",
        "source_team_label": "A",
        "owned_frames": frames,
    }


if __name__ == "__main__":
    unittest.main()
