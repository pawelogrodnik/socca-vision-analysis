from __future__ import annotations

from copy import deepcopy
import unittest

from app.services.identity_stitching_goldset import (
    build_identity_stitching_goldset,
    evaluate_identity_stitching_goldset,
)


def audit_manifest(benchmark_id: str, reviews: list[tuple[str, str]]) -> dict:
    items = []
    for index, (candidate_key, status) in enumerate(reviews, start=1):
        same_person = True if status == "confirmed_same" else False if status == "confirmed_different" else None
        items.append(
            {
                "audit_index": index,
                "candidate_key": candidate_key,
                "source": {"tracklet_id": f"source-{index}"},
                "target": {"tracklet_id": f"target-{index}"},
                "decision": {
                    "current_identity_relation": "unresolved",
                    "source_stable_subject_ids": [],
                    "target_stable_subject_ids": [],
                },
                "manual_review": {
                    "status": status,
                    "same_person": same_person,
                    "reviewer": "tester",
                    "reviewed_at": "fixed",
                    "notes": "",
                },
            }
        )
    return {
        "algorithm": {"name": "audit", "version": "test"},
        "benchmark": {"benchmark_id": benchmark_id, "label": benchmark_id},
        "source": {"stitching_algorithm": {"name": "stitch", "version": "test"}},
        "items": items,
    }


def predictions(values: dict[str, bool]) -> dict:
    return {"candidate_edges": [{"candidate_key": key, "recommended": value} for key, value in values.items()]}


class IdentityStitchingGoldsetTests(unittest.TestCase):
    def test_builds_versioned_goldset_and_excludes_uncertain_from_labels(self) -> None:
        goldset = build_identity_stitching_goldset(
            [
                audit_manifest(
                    "easy",
                    [
                        ("stitch:v1:a", "confirmed_same"),
                        ("stitch:v1:b", "confirmed_different"),
                        ("stitch:v1:c", "uncertain"),
                    ],
                )
            ],
            goldset_id="identity",
            version="1.0.0",
            generated_at="fixed",
        )

        self.assertEqual(goldset["status"], "ready")
        self.assertEqual(goldset["summary"]["labeled"], 2)
        self.assertEqual(goldset["summary"]["uncertain"], 1)
        self.assertEqual(goldset["items"][2]["expected_same_person"], None)

    def test_pending_reviews_keep_goldset_not_ready(self) -> None:
        goldset = build_identity_stitching_goldset(
            [audit_manifest("easy", [("stitch:v1:a", "pending")])],
            goldset_id="identity",
            version="1.0.0",
            generated_at="fixed",
        )

        self.assertEqual(goldset["status"], "needs_review")
        self.assertEqual(goldset["summary"]["pending"], 1)

    def test_conflicting_duplicate_review_is_rejected(self) -> None:
        first = audit_manifest("easy", [("stitch:v1:a", "confirmed_same")])
        second = audit_manifest("easy", [("stitch:v1:a", "confirmed_different")])

        with self.assertRaisesRegex(ValueError, "Conflicting reviews"):
            build_identity_stitching_goldset(
                [first, second],
                goldset_id="identity",
                version="1.0.0",
            )

    def test_digest_is_deterministic_and_ignores_review_timestamp(self) -> None:
        first = audit_manifest("easy", [("stitch:v1:a", "confirmed_same")])
        second = deepcopy(first)
        second["items"][0]["manual_review"]["reviewed_at"] = "later"

        first_goldset = build_identity_stitching_goldset(
            [first], goldset_id="identity", version="1.0.0", generated_at="first"
        )
        second_goldset = build_identity_stitching_goldset(
            [second], goldset_id="identity", version="1.0.0", generated_at="second"
        )

        self.assertEqual(first_goldset["goldset_digest"], second_goldset["goldset_digest"])

    def test_evaluator_reports_confusion_and_passes_precision_gate(self) -> None:
        goldset = build_identity_stitching_goldset(
            [
                audit_manifest(
                    "easy",
                    [
                        ("stitch:v1:a", "confirmed_same"),
                        ("stitch:v1:b", "confirmed_same"),
                        ("stitch:v1:c", "confirmed_different"),
                    ],
                )
            ],
            goldset_id="identity",
            version="1.0.0",
            generated_at="fixed",
        )
        report = evaluate_identity_stitching_goldset(
            goldset,
            {"easy": predictions({"stitch:v1:a": True, "stitch:v1:b": True, "stitch:v1:c": False})},
            min_precision=1.0,
            min_recall=1.0,
            min_labeled=3,
            max_false_positives=0,
            generated_at="fixed",
        )

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["summary"]["precision"], 1.0)
        self.assertEqual(report["summary"]["recall"], 1.0)
        self.assertEqual(report["summary"]["confusion"]["true_positive"], 2)
        self.assertEqual(report["summary"]["confusion"]["true_negative"], 1)

    def test_false_recommendation_fails_conservative_gate(self) -> None:
        goldset = build_identity_stitching_goldset(
            [
                audit_manifest(
                    "hard",
                    [
                        ("stitch:v1:a", "confirmed_same"),
                        ("stitch:v1:b", "confirmed_different"),
                    ],
                )
            ],
            goldset_id="identity",
            version="1.0.0",
            generated_at="fixed",
        )
        report = evaluate_identity_stitching_goldset(
            goldset,
            {"hard": predictions({"stitch:v1:a": True, "stitch:v1:b": True})},
            min_precision=0.9,
            min_labeled=2,
            max_false_positives=0,
            generated_at="fixed",
        )

        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["gates"]["precision"])
        self.assertFalse(report["gates"]["false_positives"])
        self.assertEqual(report["cases"][0]["errors"][0]["error_type"], "false_positive")

    def test_missing_predictions_or_too_few_labels_are_not_ready(self) -> None:
        goldset = build_identity_stitching_goldset(
            [audit_manifest("easy", [("stitch:v1:a", "confirmed_same")])],
            goldset_id="identity",
            version="1.0.0",
            generated_at="fixed",
        )
        report = evaluate_identity_stitching_goldset(
            goldset,
            {},
            min_labeled=10,
            generated_at="fixed",
        )

        self.assertEqual(report["status"], "not_ready")
        self.assertIn("insufficient_labeled_examples", report["readiness_reasons"])
        self.assertIn("missing_prediction_documents", report["readiness_reasons"])


if __name__ == "__main__":
    unittest.main()
