from __future__ import annotations

from copy import deepcopy
import unittest

from app.services.identity_fragment_visual_content import (
    build_endpoint_key,
    build_identity_fragment_visual_content_evidence,
)


class IdentityFragmentVisualContentTests(unittest.TestCase):
    def test_endpoint_key_is_stable_and_side_specific(self) -> None:
        proposal = _proposal()

        first = build_endpoint_key(proposal, side="source")
        second = build_endpoint_key(deepcopy(proposal), side="source")
        target = build_endpoint_key(proposal, side="target")

        self.assertEqual(first, second)
        self.assertNotEqual(first, target)

    def test_missing_reviews_never_support_automatic_merge(self) -> None:
        result = build_identity_fragment_visual_content_evidence(
            _document(), generated_at="fixed"
        )
        document = result["identity_fragment_visual_content"]

        self.assertEqual(document["summary"]["endpoint_status_counts"], {"unavailable": 2})
        self.assertEqual(document["pairs"][0]["quality"], "unavailable")
        self.assertFalse(document["pairs"][0]["safe_for_automatic_identity_merge"])

    def test_not_person_endpoint_blocks_pair_without_mutating_identity(self) -> None:
        proposal = _proposal()
        source_key = build_endpoint_key(proposal, side="source")
        target_key = build_endpoint_key(proposal, side="target")
        audit = _audit(
            [
                (source_key, "not_person"),
                (target_key, "person"),
            ]
        )
        result = build_identity_fragment_visual_content_evidence(
            _document(), reviewed_audits=[audit], generated_at="fixed"
        )
        document = result["identity_fragment_visual_content"]

        self.assertEqual(document["pairs"][0]["quality"], "invalid_content")
        self.assertTrue(document["pairs"][0]["blocks_automatic_identity_merge"])
        self.assertFalse(document["safety"]["mutates_candidate_identity"])
        self.assertTrue(
            result["identity_fragment_visual_content_report"]["gates"][
                "identity_outputs_untouched"
            ]
        )

    def test_person_and_partial_person_are_content_support_only(self) -> None:
        proposal = _proposal()
        audit = _audit(
            [
                (build_endpoint_key(proposal, side="source"), "person"),
                (build_endpoint_key(proposal, side="target"), "partial_person"),
            ]
        )
        result = build_identity_fragment_visual_content_evidence(
            _document(), reviewed_audits=[audit]
        )
        pair = result["identity_fragment_visual_content"]["pairs"][0]

        self.assertEqual(pair["quality"], "person_content_supported")
        self.assertTrue(pair["person_content_supported"])
        self.assertFalse(pair["safe_for_automatic_identity_merge"])

    def test_conflicting_reviews_are_rejected(self) -> None:
        key = build_endpoint_key(_proposal(), side="source")

        with self.assertRaisesRegex(ValueError, "Conflicting manual reviews"):
            build_identity_fragment_visual_content_evidence(
                _document(),
                reviewed_audits=[_audit([(key, "person")]), _audit([(key, "not_person")])],
            )


def _proposal() -> dict:
    return {
        "proposal_key": "proposal-1",
        "source_candidate_subject_id": "subject-source",
        "target_candidate_subject_id": "subject-target",
        "source_candidate_player_id": "A01",
        "target_candidate_player_id": "A01~2",
        "source_team_label": "A",
        "target_team_label": "A",
        "source_endpoint": {
            "frame": 100,
            "bbox_xyxy": [10.0, 20.0, 30.0, 80.0],
        },
        "target_endpoint": {
            "frame": 105,
            "bbox_xyxy": [12.0, 20.0, 32.0, 80.0],
        },
    }


def _document() -> dict:
    return {
        "algorithm": {"name": "consolidation", "version": "test"},
        "proposals": [_proposal()],
    }


def _audit(rows: list[tuple[str, str]]) -> dict:
    return {
        "audit_kind": "fragment_endpoint_content",
        "items": [
            {
                "endpoint_key": key,
                "manual_review": {
                    "status": status,
                    "reviewed_at": "fixed",
                    "notes": "",
                },
            }
            for key, status in rows
        ],
    }


if __name__ == "__main__":
    unittest.main()
