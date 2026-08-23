from __future__ import annotations

import copy
import json
import unittest

from app.services.identity_reviewed_response_shaping import (
    correction_response_decision,
)


class CorrectionResponseDecisionTests(unittest.TestCase):
    def test_material_continuity_payload_omits_frame_lists_but_keeps_identity(self) -> None:
        saved = {
            "scope_kind": "material_continuity",
            "continuity_group_id": "continuity:A07:18836-20922",
            "continuity_subject_ids": ["shadow-a-1", "shadow-a-2"],
            "owned_observations": [
                {"tracklet_id": "600011:2", "frame": frame} for frame in range(18836, 20837)
            ],
            "decision": {
                "action": "assign_roster_player",
                "player_id": "p9",
                "source_ownership_digest": "digest-1",
                "owned_observations": [{"tracklet_id": "600011:2", "frame": 18836}],
            },
        }
        original = copy.deepcopy(saved)

        shaped = correction_response_decision(saved)

        self.assertEqual(saved, original)
        raw = json.dumps(shaped)
        self.assertLess(len(raw), 2000)
        self.assertNotIn("owned_observations", shaped)
        self.assertNotIn("owned_observations", shaped["decision"])
        self.assertIn("owned_observations_count", shaped)
        self.assertIn("owned_observations_count", shaped["decision"])
        self.assertEqual(shaped["owned_observations_count"], 2001)
        self.assertEqual(shaped["decision"]["owned_observations_count"], 1)
        self.assertEqual(shaped["continuity_subject_ids"], ["shadow-a-1", "shadow-a-2"])
        self.assertEqual(shaped["decision"]["source_ownership_digest"], "digest-1")

    def test_payload_without_frame_lists_passes_through(self) -> None:
        saved = {"action": "unresolved", "candidate_subject_id": "s1"}
        self.assertEqual(
            correction_response_decision(saved),
            {"action": "unresolved", "candidate_subject_id": "s1"},
        )


if __name__ == "__main__":
    unittest.main()
