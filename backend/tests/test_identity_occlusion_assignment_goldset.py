from __future__ import annotations

import unittest

from app.services.identity_occlusion_assignment_goldset import (
    build_joint_assignment_goldset,
    evaluate_joint_assignment_goldset,
)


def _assignment(assignment_id: str, pairs: list[tuple[str, str]]) -> dict:
    return {
        "assignment_id": assignment_id,
        "pairs": [
            {"source_tracklet_id": source, "target_tracklet_id": target}
            for source, target in pairs
        ],
    }


def _item(case_key: str, status: str) -> dict:
    return {
        "case_key": case_key,
        "team_label": "A",
        "event": {"start_frame": 10, "end_frame": 12},
        "assignments": [
            _assignment("assignment_a", [("s1", "t1"), ("s2", "t2")]),
            _assignment("assignment_b", [("s1", "t2"), ("s2", "t1")]),
        ],
        "manual_review": {
            "status": status,
            "correct_assignment_id": status if status in {"assignment_a", "assignment_b"} else None,
            "confirmed_pairs": [{"source": "S2", "target": "T1"}] if status == "partial" else [],
            "notes": "",
        },
    }


def _manifest(items: list[dict]) -> dict:
    return {
        "benchmark": {"benchmark_id": "hard", "label": "hard3m"},
        "algorithm": {"name": "audit", "version": "test"},
        "source": {"assignment_algorithm": {"name": "shadow", "version": "test"}},
        "items": items,
    }


def _prediction(
    case_key: str,
    assignment_id: str | None,
    *,
    partial_pair: tuple[str, str] | None = None,
) -> dict:
    return {
        "case_key": case_key,
        "assignments": [
            _assignment("assignment_a", [("s1", "t1"), ("s2", "t2")]),
            _assignment("assignment_b", [("s1", "t2"), ("s2", "t1")]),
        ],
        "decision": {
            "recommended_assignment_id": assignment_id,
            "recommended_pairs": (
                [{"source_tracklet_id": partial_pair[0], "target_tracklet_id": partial_pair[1]}]
                if partial_pair
                else []
            ),
        },
    }


class JointAssignmentGoldsetTests(unittest.TestCase):
    def test_selected_assignment_creates_positive_and_negative_edge_labels(self) -> None:
        goldset = build_joint_assignment_goldset(
            [_manifest([_item("case-1", "assignment_b")])],
            goldset_id="joint",
            version="1.0.0",
            generated_at="fixed",
        )

        self.assertEqual(goldset["status"], "ready")
        self.assertEqual(goldset["summary"]["positive_edge_labels"], 2)
        self.assertEqual(goldset["summary"]["negative_edge_labels"], 2)
        positives = {
            (row["source_tracklet_id"], row["target_tracklet_id"])
            for row in goldset["items"][0]["edge_labels"]
            if row["expected_same_person"]
        }
        self.assertEqual(positives, {("s1", "t2"), ("s2", "t1")})

    def test_neither_marks_all_edges_as_negative(self) -> None:
        goldset = build_joint_assignment_goldset(
            [_manifest([_item("case-1", "neither")])],
            goldset_id="joint",
            version="1.0.0",
            generated_at="fixed",
        )

        self.assertEqual(goldset["summary"]["positive_edge_labels"], 0)
        self.assertEqual(goldset["summary"]["negative_edge_labels"], 4)

    def test_partial_review_preserves_one_positive_edge(self) -> None:
        goldset = build_joint_assignment_goldset(
            [_manifest([_item("case-1", "partial")])],
            goldset_id="joint",
            version="1.0.0",
            generated_at="fixed",
        )

        self.assertEqual(goldset["summary"]["partial"], 1)
        self.assertEqual(goldset["summary"]["positive_edge_labels"], 1)
        self.assertEqual(goldset["summary"]["negative_edge_labels"], 3)
        self.assertEqual(
            goldset["items"][0]["expected_pairs"],
            [{"source_tracklet_id": "s2", "target_tracklet_id": "t1"}],
        )

    def test_evaluator_counts_correct_wrong_and_abstained_assignments(self) -> None:
        goldset = build_joint_assignment_goldset(
            [_manifest([
                _item("correct", "assignment_a"),
                _item("wrong", "assignment_b"),
                _item("abstain", "assignment_a"),
                _item("none", "neither"),
            ])],
            goldset_id="joint",
            version="1.0.0",
            generated_at="fixed",
        )
        predictions = {
            "hard": {
                "cases": [
                    _prediction("correct", "assignment_a"),
                    _prediction("wrong", "assignment_a"),
                    _prediction("abstain", None),
                    _prediction("none", None),
                ]
            }
        }
        evaluation = evaluate_joint_assignment_goldset(
            goldset,
            predictions,
            min_labeled_cases=4,
            min_accuracy=0.5,
            max_wrong_assignments=1,
            generated_at="fixed",
        )

        self.assertEqual(evaluation["status"], "passed")
        self.assertEqual(evaluation["summary"]["correct"], 2)
        self.assertEqual(evaluation["summary"]["wrong"], 1)
        self.assertEqual(evaluation["summary"]["abstained"], 1)
        self.assertEqual(evaluation["summary"]["accuracy"], 0.5)

    def test_evaluator_accepts_exact_partial_pair(self) -> None:
        goldset = build_joint_assignment_goldset(
            [_manifest([_item("partial", "partial")])],
            goldset_id="joint",
            version="1.0.0",
            generated_at="fixed",
        )
        evaluation = evaluate_joint_assignment_goldset(
            goldset,
            {"hard": {"cases": [_prediction("partial", "partial", partial_pair=("s2", "t1"))]}},
            min_labeled_cases=1,
            min_accuracy=1.0,
            generated_at="fixed",
        )

        self.assertEqual(evaluation["status"], "passed")
        self.assertEqual(evaluation["summary"]["correct"], 1)
        self.assertEqual(
            evaluation["summary"]["edge_confusion"],
            {"true_positive": 1, "false_positive": 0, "false_negative": 0, "true_negative": 3},
        )

    def test_evaluator_rejects_wrong_partial_pair(self) -> None:
        goldset = build_joint_assignment_goldset(
            [_manifest([_item("partial", "partial")])],
            goldset_id="joint",
            version="1.0.0",
            generated_at="fixed",
        )
        evaluation = evaluate_joint_assignment_goldset(
            goldset,
            {"hard": {"cases": [_prediction("partial", "partial", partial_pair=("s1", "t1"))]}},
            min_labeled_cases=1,
            min_accuracy=1.0,
            generated_at="fixed",
        )

        self.assertEqual(evaluation["status"], "failed")
        self.assertEqual(evaluation["summary"]["wrong"], 1)
        self.assertEqual(evaluation["errors"][0]["predicted_assignment_id"], "partial")

    def test_pending_reviews_remain_not_ready(self) -> None:
        goldset = build_joint_assignment_goldset(
            [_manifest([_item("pending", "pending")])],
            goldset_id="joint",
            version="draft",
            generated_at="fixed",
        )
        evaluation = evaluate_joint_assignment_goldset(
            goldset,
            {"hard": {"cases": []}},
            min_labeled_cases=1,
            generated_at="fixed",
        )

        self.assertEqual(goldset["status"], "needs_review")
        self.assertEqual(evaluation["status"], "not_ready")


if __name__ == "__main__":
    unittest.main()
