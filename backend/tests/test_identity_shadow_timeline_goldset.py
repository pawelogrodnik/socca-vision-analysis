from __future__ import annotations

import unittest

from app.services.identity_shadow_timeline_goldset import (
    build_shadow_timeline_goldset,
    evaluate_shadow_timeline_goldset,
)


def reviewed_manifest() -> dict:
    return {
        "benchmark": {"benchmark_id": "easy", "label": "easy"},
        "items": [
            {
                "audit_key": "event-1",
                "audit_kind": "accepted_transition",
                "shadow_subject_id": "subject-1",
                "timeline_state": {"status": "missing", "start_frame": 2, "end_frame": 3},
                "manual_review": {
                    "identity_continuity": "same_person",
                    "state_assessment": "should_be_predicted",
                },
            },
            {
                "audit_key": "gap-1",
                "audit_kind": "internal_gap",
                "shadow_subject_id": "subject-2",
                "timeline_state": {"status": "missing", "start_frame": 5, "end_frame": 6},
                "manual_review": {
                    "identity_continuity": "different_people",
                    "state_assessment": "identity_link_invalid",
                },
            },
        ],
    }


class IdentityShadowTimelineGoldsetTests(unittest.TestCase):
    def test_goldset_is_deterministic(self) -> None:
        first = build_shadow_timeline_goldset(
            [reviewed_manifest()],
            goldset_id="timeline",
            version="1",
            generated_at="first",
        )
        second = build_shadow_timeline_goldset(
            [reviewed_manifest()],
            goldset_id="timeline",
            version="1",
            generated_at="second",
        )
        self.assertEqual(first["goldset_digest"], second["goldset_digest"])

    def test_evaluator_accepts_correct_state_and_identity_abstention(self) -> None:
        goldset = build_shadow_timeline_goldset(
            [reviewed_manifest()],
            goldset_id="timeline",
            version="1",
            generated_at="fixed",
        )
        prediction = {
            "transition_events": [
                {
                    "event_key": "event-1",
                    "status": "predicted",
                    "identity_continuity_status": "supported",
                }
            ],
            "subjects": [
                {
                    "shadow_subject_id": "subject-2",
                    "state_runs": [
                        {
                            "status": "missing",
                            "start_frame": 5,
                            "end_frame": 6,
                            "identity_continuity_status": "uncertain",
                        }
                    ],
                }
            ],
        }
        report = evaluate_shadow_timeline_goldset(
            goldset,
            {"easy": prediction},
            min_state_accuracy=1.0,
        )

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["summary"]["identity_false_positive"], 0)
        self.assertEqual(report["summary"]["identity_abstained"], 1)


if __name__ == "__main__":
    unittest.main()
