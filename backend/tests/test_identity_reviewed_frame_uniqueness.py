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
                propagation_conflicted_stable_slot_ids=["A03"],
            ),
            _confirmed_assignment(
                "second",
                team_label="A",
                stable_slot_id="A03",
                player_id="player-patryk",
                propagation_conflicted_stable_slot_ids=["A03"],
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
                propagation_conflicted_stable_slot_ids=["A03"],
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

    def test_segment_override_inherits_conflicted_slot_diagnostic(self) -> None:
        tracklets = {
            "explicit": _tracklet("explicit", "inside_play"),
            "segment": _tracklet("segment", "inside_play"),
        }
        assignments = [
            _confirmed_assignment(
                "explicit",
                team_label="A",
                stable_slot_id="A03",
                player_id="player-kuba",
                propagation_conflicted_stable_slot_ids=["A03"],
            ),
            _conflicted_assignment("segment"),
        ]
        segment_overrides = [
            _segment_assignment("segment", player_id="player-roman"),
        ]

        demotions, diagnostics = build_frame_slot_demotions(
            tracklets,
            assignments,
            segment_overrides=segment_overrides,
        )

        self.assertEqual(demotions, [])
        self.assertEqual(diagnostics["duplicate_stable_slot_claim_groups"], 0)
        self.assertEqual(diagnostics["duplicate_canonical_player_claim_groups"], 0)

    def test_segment_override_on_conflicted_slot_still_blocks_same_player(
        self,
    ) -> None:
        tracklets = {
            "explicit": _tracklet("explicit", "inside_play"),
            "segment": _tracklet("segment", "inside_play"),
        }
        assignments = [
            _confirmed_assignment(
                "explicit",
                team_label="A",
                stable_slot_id="A03",
                player_id="player-kuba",
                propagation_conflicted_stable_slot_ids=["A03"],
            ),
            _conflicted_assignment("segment"),
        ]
        segment_overrides = [
            _segment_assignment("segment", player_id="player-kuba"),
        ]

        demotions, diagnostics = build_frame_slot_demotions(
            tracklets,
            assignments,
            segment_overrides=segment_overrides,
        )

        self.assertEqual(diagnostics["duplicate_stable_slot_claim_groups"], 0)
        self.assertEqual(diagnostics["duplicate_canonical_player_claim_groups"], 1)
        self.assertTrue(
            all(
                any(
                    conflict["code"] == "duplicate_canonical_player_in_frame"
                    for conflict in demotion["conflicts"]
                )
                for demotion in demotions
            )
        )

    def test_healthy_slot_override_still_enforces_stable_slot_uniqueness(
        self,
    ) -> None:
        tracklets = {
            "segment": _tracklet("segment", "inside_play"),
            "healthy": _tracklet("healthy", "inside_play"),
        }
        assignments = [
            _conflicted_assignment("segment"),
            _confirmed_assignment(
                "healthy",
                team_label="A",
                stable_slot_id="A04",
                player_id="player-patryk",
            ),
        ]
        segment_overrides = [
            _stable_slot_segment_assignment(
                "segment",
                stable_slot_id="A04",
            )
        ]

        demotions, diagnostics = build_frame_slot_demotions(
            tracklets,
            assignments,
            segment_overrides=segment_overrides,
        )

        self.assertEqual(diagnostics["duplicate_stable_slot_claim_groups"], 1)
        self.assertEqual(diagnostics["duplicate_canonical_player_claim_groups"], 0)
        self.assertTrue(
            any(
                any(
                    conflict["code"] == "duplicate_stable_slot_in_frame"
                    for conflict in demotion["conflicts"]
                )
                for demotion in demotions
            )
        )

    def test_new_slot_override_still_enforces_stable_slot_uniqueness(self) -> None:
        tracklets = {
            "segment": _tracklet("segment", "inside_play"),
            "new": _tracklet("new", "inside_play"),
        }
        assignments = [
            _conflicted_assignment("segment"),
            _confirmed_assignment(
                "new",
                team_label="A",
                stable_slot_id="A10",
                player_id="player-patryk",
            ),
        ]
        segment_overrides = [
            _stable_slot_segment_assignment(
                "segment",
                stable_slot_id="A10",
            )
        ]

        demotions, diagnostics = build_frame_slot_demotions(
            tracklets,
            assignments,
            segment_overrides=segment_overrides,
        )

        self.assertEqual(diagnostics["duplicate_stable_slot_claim_groups"], 1)
        self.assertTrue(demotions)

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
    propagation_conflicted_stable_slot_ids: list[str] | None = None,
) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "team_label": team_label,
        "stable_anonymous_slot_id": stable_slot_id,
        "stable_anchor_source": "global_identity",
        "identity_status": "confirmed",
        "canonical_player_id": player_id,
        "propagation_conflicted_stable_slot_ids": (
            propagation_conflicted_stable_slot_ids or []
        ),
    }


def _conflicted_assignment(tracklet_id: str) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "team_label": "A",
        "stable_anonymous_slot_id": "A03",
        "identity_status": "conflicted",
        "canonical_player_id": None,
        "propagation_conflicted_stable_slot_ids": ["A03"],
    }


def _segment_assignment(tracklet_id: str, *, player_id: str) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "frame": 10,
        "team_label": "A",
        "stable_anonymous_slot_id": "A03",
        "identity_status": "confirmed",
        "canonical_player_id": player_id,
        "identity_source": "manual_segment_review",
    }


def _stable_slot_segment_assignment(
    tracklet_id: str,
    *,
    stable_slot_id: str,
) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "frame": 10,
        "team_label": "A",
        "stable_anonymous_slot_id": stable_slot_id,
        "identity_status": "stable_anonymous",
        "canonical_player_id": None,
        "identity_source": "manual_segment_review",
    }


if __name__ == "__main__":
    unittest.main()
