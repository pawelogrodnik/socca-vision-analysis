from __future__ import annotations

import unittest

from app.services.artifact_lineage import canonical_json_sha256
from app.services.candidate_keys import (
    contact_candidate_key,
    regular_pass_candidate_key,
    restart_candidate_key,
)
from app.services.pass_candidates import apply_existing_pass_reviews


class CandidateKeysTests(unittest.TestCase):
    def test_canonical_hash_ignores_technical_timestamps(self) -> None:
        first = {"value": 1, "generated_at": "a", "nested": {"updated_at": "b", "x": 2}}
        second = {"nested": {"x": 2, "updated_at": "changed"}, "generated_at": "changed", "value": 1}
        self.assertEqual(canonical_json_sha256(first), canonical_json_sha256(second))

    def test_candidate_keys_are_independent_from_display_ids(self) -> None:
        contact = {
            "candidate_id": "contact-0001",
            "start_frame": 10,
            "end_frame": 20,
            "stable_subject_id": "A01",
            "team_label": "A",
        }
        renamed = {**contact, "candidate_id": "contact-9999"}
        self.assertEqual(contact_candidate_key(contact), contact_candidate_key(renamed))
        self.assertEqual(
            regular_pass_candidate_key("contact-key-a", "contact-key-b"),
            regular_pass_candidate_key("contact-key-a", "contact-key-b"),
        )
        self.assertTrue(restart_candidate_key({"setup_start_frame": 1, "release_frame": 4, "boundary_line": "left"}).startswith("restart:v1:"))

    def test_ambiguous_review_migration_requires_review(self) -> None:
        candidate = {
            "candidate_id": "pass-0001",
            "candidate_key": "pass:v1:same",
            "pass_type": "same_team_pass",
            "outcome": "completed_pass",
        }
        existing = {
            "candidates": [
                {"candidate_id": "old-1", "candidate_key": "pass:v1:same", "review_status": "accepted", "review_source": "manual"},
                {"candidate_id": "old-2", "candidate_key": "pass:v1:same", "review_status": "rejected", "review_source": "manual"},
            ]
        }
        document = {"summary": {}, "candidates": [candidate]}
        apply_existing_pass_reviews(document, existing)
        self.assertEqual(candidate["review_status"], "needs_review")
        self.assertEqual(candidate["review_migration_status"], "ambiguous_existing_review")
        self.assertFalse(candidate["final_stat_eligible"])


if __name__ == "__main__":
    unittest.main()
