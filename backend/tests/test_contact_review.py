from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.contact_review import load_contact_candidates_review, save_contact_candidate_reviews


def write_contact_candidates(path: Path) -> None:
    document = {
        "schema_version": "0.1.0",
        "summary": {"contact_candidates": 1},
        "candidates": [
            {
                "candidate_id": "contact-0001",
                "stable_player_id": "A01",
                "status": "needs_review",
                "interpolated_player_frames": 2,
            }
        ],
    }
    (path / "contact_candidates.json").write_text(json.dumps(document), encoding="utf-8")


class ContactReviewTests(unittest.TestCase):
    def test_load_normalizes_review_status_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            match_path = Path(temp_dir)
            write_contact_candidates(match_path)

            document = load_contact_candidates_review(match_path)

            self.assertEqual(document["candidates"][0]["review_status"], "needs_review")
            self.assertEqual(document["summary"]["needs_review_candidates"], 1)
            self.assertEqual(document["summary"]["candidates_with_interpolated_player_positions"], 1)

    def test_save_review_updates_status_notes_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            match_path = Path(temp_dir)
            write_contact_candidates(match_path)

            document = save_contact_candidate_reviews(
                match_path,
                [
                    {
                        "candidate_id": "contact-0001",
                        "review_status": "accepted",
                        "notes": "visible touch",
                    }
                ],
            )

            candidate = document["candidates"][0]
            self.assertEqual(candidate["review_status"], "accepted")
            self.assertEqual(candidate["status"], "accepted")
            self.assertEqual(candidate["review_notes"], "visible touch")
            self.assertEqual(document["summary"]["accepted_candidates"], 1)
            self.assertEqual(document["summary"]["needs_review_candidates"], 0)

    def test_save_review_rejects_unknown_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            match_path = Path(temp_dir)
            write_contact_candidates(match_path)

            with self.assertRaises(ValueError):
                save_contact_candidate_reviews(
                    match_path,
                    [{"candidate_id": "contact-9999", "review_status": "accepted"}],
                )


if __name__ == "__main__":
    unittest.main()
