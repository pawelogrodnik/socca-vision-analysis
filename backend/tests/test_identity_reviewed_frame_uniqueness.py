from __future__ import annotations

import unittest

from app.services.identity_reviewed_frame_uniqueness import (
    build_frame_slot_demotions,
)


class ReviewedFrameUniquenessPlayAreaTests(unittest.TestCase):
    def test_noninside_claim_cannot_demote_matching_inside_claim(self) -> None:
        for play_area_status in ("outside_play", "boundary_transient"):
            with self.subTest(play_area_status=play_area_status):
                tracklets = {
                    "inside": _tracklet("inside", "inside_play"),
                    "off_pitch": _tracklet("off_pitch", play_area_status),
                }
                assignments = [
                    _confirmed_assignment("inside"),
                    _confirmed_assignment("off_pitch"),
                ]

                demotions, diagnostics = build_frame_slot_demotions(
                    tracklets, assignments
                )

                self.assertEqual(demotions, [])
                self.assertEqual(
                    diagnostics["duplicate_stable_slot_claim_groups"], 0
                )
                self.assertEqual(
                    diagnostics["duplicate_canonical_player_claim_groups"], 0
                )
                self.assertEqual(diagnostics["demoted_observation_claims"], 0)


def _tracklet(tracklet_id: str, play_area_status: str) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "team_label": "B",
        "positions_m": [
            {
                "frame": 10,
                "status": "detected",
                "source": "detected",
                "play_area_status": play_area_status,
            }
        ],
    }


def _confirmed_assignment(tracklet_id: str) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "team_label": "B",
        "stable_anonymous_slot_id": "B03",
        "stable_anchor_source": "global_identity",
        "identity_status": "confirmed",
        "canonical_player_id": "player-b03",
    }


if __name__ == "__main__":
    unittest.main()
