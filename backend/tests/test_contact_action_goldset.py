from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from evaluation.contact_action_goldset import summarize_contact_action_goldset, validate_contact_action_goldset


FIXTURES = Path(__file__).parent / "fixtures"


def _goldset() -> dict:
    return json.loads((FIXTURES / "contact_action_goldset_v1.json").read_text(encoding="utf-8"))


def _canonical_shots() -> list[dict]:
    return json.loads((FIXTURES / "shot_goldset_v3.json").read_text(encoding="utf-8"))["shots"]


class ContactActionGoldsetTests(unittest.TestCase):
    def test_v1_fixture_is_valid_against_canonical_shot_review_snapshot(self) -> None:
        self.assertEqual(validate_contact_action_goldset(_goldset(), _canonical_shots()), [])

    def test_summary_counts_only_manual_actions_and_keeps_shots_as_references(self) -> None:
        summary = summarize_contact_action_goldset(_goldset())

        self.assertEqual(summary["windows"], 6)
        self.assertEqual(summary["events"], 113)
        self.assertEqual(summary["canonical_shots"], 11)
        self.assertEqual(summary["possible_missing_manual_events"], 0)

    def test_rejects_duplicate_event_id_and_canonical_shot_drift(self) -> None:
        document = copy.deepcopy(_goldset())
        document["events"][1]["event_id"] = document["events"][0]["event_id"]
        shot = next(event for event in document["events"] if event.get("event_type") == "SHOT_REFERENCE")
        shot["outcome"] = "off_target"

        errors = validate_contact_action_goldset(document, _canonical_shots())

        self.assertTrue(any("duplicated" in error for error in errors))
        self.assertTrue(any("canonical shot outcome differs" in error for error in errors))

    def test_requires_context_only_for_sequence_completion_outside_window(self) -> None:
        document = copy.deepcopy(_goldset())
        event = next(event for event in document["events"] if event["event_id"] == "W6-E021")
        event.pop("context_only")

        self.assertIn("W6-E021: timestamp is outside its window", validate_contact_action_goldset(document, _canonical_shots()))

    def test_rejects_invalid_action_vocabulary(self) -> None:
        document = copy.deepcopy(_goldset())
        document["events"][0]["action"] = "SHOT"

        self.assertIn("W1-E001: invalid action SHOT", validate_contact_action_goldset(document, _canonical_shots()))


if __name__ == "__main__":
    unittest.main()
