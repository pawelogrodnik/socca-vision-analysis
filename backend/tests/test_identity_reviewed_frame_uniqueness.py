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

    def test_propagation_conflicted_slot_allows_distinct_players_in_same_frame(
        self,
    ) -> None:
        tracklets = {
            "first": _tracklet("first", "inside_play"),
            "second": _tracklet("second", "inside_play"),
        }
        assignments = [
            _confirmed_assignment(
                "first",
                team_label="A",
                stable_slot_id="A03",
                player_id="player-kuba",
                propagation_diagnostics=["stable_slot_propagation_conflicted"],
            ),
            _confirmed_assignment(
                "second",
                team_label="A",
                stable_slot_id="A03",
                player_id="player-patryk",
                propagation_diagnostics=["stable_slot_propagation_conflicted"],
            ),
        ]

        demotions, diagnostics = build_frame_slot_demotions(tracklets, assignments)

        self.assertEqual(demotions, [])
        self.assertEqual(diagnostics["duplicate_stable_slot_claim_groups"], 0)
        self.assertEqual(diagnostics["duplicate_canonical_player_claim_groups"], 0)

    def test_propagation_conflicted_slot_still_blocks_duplicate_player_in_same_frame(
        self,
    ) -> None:
        tracklets = {
            "first": _tracklet("first", "inside_play"),
            "second": _tracklet("second", "inside_play"),
        }
        assignments = [
            _confirmed_assignment(
                tracklet_id,
                team_label="A",
                stable_slot_id="A03",
                player_id="player-kuba",
                propagation_diagnostics=["stable_slot_propagation_conflicted"],
            )
            for tracklet_id in tracklets
        ]

        demotions, diagnostics = build_frame_slot_demotions(tracklets, assignments)

        self.assertEqual(diagnostics["duplicate_stable_slot_claim_groups"], 0)
        self.assertEqual(diagnostics["duplicate_canonical_player_claim_groups"], 1)
        self.assertTrue(demotions)
        self.assertTrue(
            all(
                any(
                    conflict["code"] == "duplicate_canonical_player_in_frame"
                    for conflict in demotion["conflicts"]
                )
                for demotion in demotions
            )
        )

    def test_nonconflicted_slot_still_blocks_duplicate_slot_in_same_frame(self) -> None:
        tracklets = {
            "first": _tracklet("first", "inside_play"),
            "second": _tracklet("second", "inside_play"),
        }
        assignments = [
            _confirmed_assignment(
                "first",
                team_label="A",
                stable_slot_id="A04",
                player_id="player-kuba",
            ),
            _confirmed_assignment(
                "second",
                team_label="A",
                stable_slot_id="A04",
                player_id="player-patryk",
            ),
        ]

        demotions, diagnostics = build_frame_slot_demotions(tracklets, assignments)

        self.assertEqual(diagnostics["duplicate_stable_slot_claim_groups"], 1)
        self.assertEqual(diagnostics["duplicate_canonical_player_claim_groups"], 0)
        self.assertTrue(demotions)
        self.assertTrue(
            all(
                any(
                    conflict["code"] == "duplicate_stable_slot_in_frame"
                    for conflict in demotion["conflicts"]
                )
                for demotion in demotions
            )
        )


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


def _confirmed_assignment(
    tracklet_id: str,
    *,
    team_label: str = "B",
    stable_slot_id: str = "B03",
    player_id: str = "player-b03",
    propagation_diagnostics: list[str] | None = None,
) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "team_label": team_label,
        "stable_anonymous_slot_id": stable_slot_id,
        "stable_anchor_source": "global_identity",
        "identity_status": "confirmed",
        "canonical_player_id": player_id,
        "propagation_diagnostics": propagation_diagnostics or [],
    }


if __name__ == "__main__":
    unittest.main()
