from __future__ import annotations

import unittest
from typing import Any

from app.services.identity_jersey_number_assistant_audit_dataset import (
    ASSISTANT_ANNOTATION_SOURCE,
    build_assistant_audit_dataset_source,
)
from app.services.identity_jersey_number_common import canonical_digest


class AssistantAuditDatasetTests(unittest.TestCase):
    def test_builds_dataset_compatible_cards_and_assistant_provenance(self) -> None:
        review = _review()
        result = build_assistant_audit_dataset_source(
            review, _audit(review), source_match_key="match-1", roster_reference=_roster_reference()
        )

        card = result["cards_doc"]["cards"][0]
        observation = result["reviewed_observations_doc"]["observations"][0]
        self.assertEqual(card["anchor_crop_id"], "crop-1")
        self.assertEqual(card["team_id"], "reference-team-a")
        self.assertEqual(observation["state"], "number_confirmed")
        self.assertEqual(observation["number"], "10")
        self.assertEqual(observation["annotation_source"], ASSISTANT_ANNOTATION_SOURCE)
        self.assertNotIn("digit_visibility", observation)
        self.assertEqual(observation["provenance"]["label_scope"], "anchor_crop_only")
        self.assertTrue(observation["provenance"]["diagnostic_only"])
        self.assertEqual(observation["provenance"]["subject_review_digest"], canonical_digest(review))

    def test_rejects_digest_mismatch_and_duplicate_labels(self) -> None:
        review = _review()
        mismatch = _audit(review)
        mismatch["source"]["subject_review_digest"] = "wrong"
        with self.assertRaises(ValueError):
            build_assistant_audit_dataset_source(
                review, mismatch, source_match_key="match-1", roster_reference=_roster_reference()
            )
        duplicate = _audit(review)
        duplicate["observations"].append(dict(duplicate["observations"][0]))
        result = build_assistant_audit_dataset_source(
            review, duplicate, source_match_key="match-1", roster_reference=_roster_reference()
        )
        self.assertEqual(result["provenance"]["raw_diagnostic_exclusion_counts"], {"duplicate_anchor_crop_label": 1})

    def test_never_emits_identity_fields_or_unauthorized_artifacts(self) -> None:
        review = _review()
        unauthorized = _audit(review)
        unauthorized["observations"][0]["diagnostic_training_authorized"] = False
        with self.assertRaises(ValueError):
            build_assistant_audit_dataset_source(
                review, unauthorized, source_match_key="match-1", roster_reference=_roster_reference()
            )
        result = build_assistant_audit_dataset_source(
            review, _audit(review), source_match_key="match-1", roster_reference=_roster_reference()
        )
        rendered = str(result)
        for field in ("player_id", "player_name", "assigned_player_id"):
            self.assertNotIn(field, rendered)

    def test_conflicting_subject_crop_labels_remain_diagnostic_and_are_reported(self) -> None:
        review = _review()
        review["cards"][0]["visual_evidence"]["anchor_crops"].append(
            {
                "anchor_crop_id": "crop-2",
                "torso_artifact": "torso/crop-2.jpg",
                "frame": 110,
                "tracklet_id": "tracklet-1",
            }
        )
        audit = _audit(review)
        audit["observations"].append(
            {
                **audit["observations"][0],
                "anchor_crop_id": "crop-2",
                "artifact": "torso/crop-2.jpg",
                "jersey_number": "15",
            }
        )

        result = build_assistant_audit_dataset_source(
            review, audit, source_match_key="match-1", roster_reference=_roster_reference()
        )

        self.assertEqual(result["provenance"]["subject_label_conflict_count"], 1)
        self.assertEqual(
            result["provenance"]["subject_label_conflicts"][0]["jersey_numbers"],
            ["10", "15"],
        )
        self.assertTrue(result["provenance"]["crop_local_labels_only"])
        self.assertTrue(
            all(
                row["provenance"]["label_scope"] == "anchor_crop_only"
                for row in result["reviewed_observations_doc"]["observations"]
            )
        )

    def test_preserves_only_explicit_assistant_quality_fields(self) -> None:
        review = _review()
        audit = _audit(review)
        audit["observations"][0]["blur_level"] = "mild"

        observation = build_assistant_audit_dataset_source(
            review, audit, source_match_key="match-1", roster_reference=_roster_reference()
        )["reviewed_observations_doc"]["observations"][0]

        self.assertEqual(observation["blur_level"], "mild")
        self.assertNotIn("view", observation)
        self.assertNotIn("digit_visibility", observation)
        self.assertEqual(observation["provenance"]["explicit_quality_fields"], ["blur_level"])

    def test_excludes_selected_unknown_team_crop_without_blocking_known_crop(self) -> None:
        review = _review()
        review["cards"].append(
            {
                "team_label": "U",
                "visual_evidence": {"anchor_crops": [{"anchor_crop_id": "crop-unknown"}]},
            }
        )
        result = build_assistant_audit_dataset_source(
            review, _audit(review), source_match_key="match-1", roster_reference=_roster_reference()
        )
        self.assertEqual(len(result["cards_doc"]["cards"]), 1)
        selected_unknown = _audit(review)
        selected_unknown["observations"].append(
            {
                **selected_unknown["observations"][0],
                "anchor_crop_id": "crop-unknown",
                "artifact": "",
            }
        )
        excluded = build_assistant_audit_dataset_source(
            review, selected_unknown, source_match_key="match-1", roster_reference=_roster_reference()
        )
        self.assertEqual([row["anchor_crop_id"] for row in excluded["cards_doc"]["cards"]], ["crop-1"])
        self.assertEqual(excluded["provenance"]["raw_diagnostic_exclusion_count"], 1)
        self.assertEqual(
            excluded["provenance"]["raw_diagnostic_exclusions"][0]["reason"], "unknown_or_missing_team_scope"
        )

    def test_team_b_and_non_roster_team_a_labels_are_excluded(self) -> None:
        review = _review()
        review["cards"].append(
            {
                "candidate_subject_id": "subject-b",
                "team_label": "B",
                "visual_evidence": {"anchor_crops": [{"anchor_crop_id": "crop-b", "artifact": "crop-b.jpg"}]},
            }
        )
        audit = _audit(review)
        audit["observations"][0]["jersey_number"] = "99"
        audit["observations"].append(
            {**audit["observations"][0], "anchor_crop_id": "crop-b", "artifact": "crop-b.jpg", "jersey_number": "10"}
        )

        result = build_assistant_audit_dataset_source(
            review, audit, source_match_key="match-1", roster_reference=_roster_reference()
        )

        self.assertEqual(result["cards_doc"]["cards"], [])
        self.assertEqual(
            result["provenance"]["raw_diagnostic_exclusion_counts"],
            {"number_not_in_roster_reference": 1, "team_b_scope": 1},
        )

    def test_duplicate_roster_number_is_excluded(self) -> None:
        review = _review()

        result = build_assistant_audit_dataset_source(
            review, _audit(review), source_match_key="match-1", roster_reference=_roster_reference(duplicate=True)
        )

        self.assertEqual(result["cards_doc"]["cards"], [])
        self.assertEqual(
            result["provenance"]["raw_diagnostic_exclusion_counts"],
            {"number_not_unique_in_roster_reference": 1},
        )


def _review() -> dict[str, Any]:
    return {
        "cards": [
            {
                "candidate_subject_id": "subject-1",
                "team_label": "A",
                "visual_evidence": {
                    "anchor_crops": [
                        {
                            "anchor_crop_id": "crop-1",
                            "torso_artifact": "torso/crop-1.jpg",
                            "frame": 100,
                            "tracklet_id": "tracklet-1",
                            "bbox_xyxy": [1, 2, 3, 4],
                        }
                    ]
                },
            }
        ]
    }


def _audit(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "diagnostic_training_only",
        "source": {"subject_review_digest": canonical_digest(review)},
        "safety": {
            "diagnostic_training_authorized": True,
            "writes_player_identity_assignments": False,
        },
        "observations": [
            {
                "anchor_crop_id": "crop-1",
                "artifact": "torso/crop-1.jpg",
                "jersey_number": "10",
                "confidence_tier": "high",
                "confidence": 0.99,
                "diagnostic_training_authorized": True,
            }
        ],
    }


def _roster_reference(*, duplicate: bool = False) -> dict[str, Any]:
    players = [
        {"player_id": "reference-player-10", "jersey_number": "10"},
        {"player_id": "reference-player-15", "jersey_number": "15"},
    ]
    if duplicate:
        players.append({"player_id": "reference-player-10b", "jersey_number": "10"})
    return {"team_id": "reference-team-a", "players": players}


if __name__ == "__main__":
    unittest.main()
