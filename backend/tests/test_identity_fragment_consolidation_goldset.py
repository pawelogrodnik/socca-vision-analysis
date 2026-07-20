from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from app.services.identity_fragment_consolidation_goldset import (
    build_identity_fragment_consolidation_goldset,
    classify_fragment_consolidation_proposal,
    evaluate_identity_fragment_consolidation_goldset,
)


class IdentityFragmentConsolidationGoldsetTests(unittest.TestCase):
    def test_committed_goldset_preserves_reviewed_benchmark_counts(self) -> None:
        fixture_path = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "player_identity"
            / "identity_fragment_consolidation_goldset_v1.json"
        )
        goldset = json.loads(fixture_path.read_text(encoding="utf-8"))

        self.assertEqual(goldset["status"], "ready")
        self.assertEqual(goldset["summary"]["items"], 132)
        self.assertEqual(goldset["summary"]["confirmed_same"], 99)
        self.assertEqual(goldset["summary"]["confirmed_different"], 13)
        self.assertEqual(goldset["summary"]["uncertain"], 20)
        self.assertEqual(goldset["summary"]["pending"], 0)

    def test_builds_three_state_deterministic_goldset(self) -> None:
        manifest = _manifest(
            "easy",
            [("a", "confirmed_same"), ("b", "confirmed_different"), ("c", "uncertain")],
        )
        first = build_identity_fragment_consolidation_goldset(
            [manifest], goldset_id="fragments", version="1.0.0", generated_at="first"
        )
        later = deepcopy(manifest)
        later["items"][0]["manual_review"]["reviewed_at"] = "later"
        second = build_identity_fragment_consolidation_goldset(
            [later], goldset_id="fragments", version="1.0.0", generated_at="second"
        )

        self.assertEqual(first["summary"]["labeled"], 2)
        self.assertEqual(first["summary"]["uncertain"], 1)
        self.assertEqual(first["goldset_digest"], second["goldset_digest"])

    def test_pending_review_keeps_goldset_not_ready(self) -> None:
        goldset = build_identity_fragment_consolidation_goldset(
            [_manifest("easy", [("a", "pending")])],
            goldset_id="fragments",
            version="1.0.0",
        )

        self.assertEqual(goldset["status"], "needs_review")

    def test_strict_policy_accepts_only_real_high_quality_gap(self) -> None:
        accepted = classify_fragment_consolidation_proposal(_proposal("a"))
        overlap_fragment = _proposal("b", gap_frames=0, gap_seconds=0.0)
        rejected = classify_fragment_consolidation_proposal(overlap_fragment)

        self.assertTrue(accepted["auto_accept"])
        self.assertFalse(rejected["auto_accept"])
        self.assertIn("no_real_temporal_gap", rejected["reason_codes"])

    def test_reason_codes_or_unreliable_active_ratio_require_review(self) -> None:
        with_reason = _proposal("a")
        with_reason["reason_codes"] = ["endpoint_distance_requires_review"]
        low_active = _proposal("b")
        low_active["target_active_ratio"] = 0.5

        self.assertFalse(classify_fragment_consolidation_proposal(with_reason)["auto_accept"])
        self.assertFalse(classify_fragment_consolidation_proposal(low_active)["auto_accept"])

    def test_evaluator_passes_zero_false_merge_and_uncertain_gate(self) -> None:
        goldset = build_identity_fragment_consolidation_goldset(
            [
                _manifest("easy", [("a", "confirmed_same"), ("b", "confirmed_different")]),
                _manifest("hard", [("c", "confirmed_same"), ("d", "uncertain")]),
            ],
            goldset_id="fragments",
            version="1.0.0",
            generated_at="fixed",
        )
        report = evaluate_identity_fragment_consolidation_goldset(
            goldset,
            {
                "easy": {"proposals": [_proposal("a"), _proposal("b", confidence=0.5)]},
                "hard": {"proposals": [_proposal("c"), _proposal("d", gap_frames=0, gap_seconds=0.0)]},
            },
            min_labeled=3,
            generated_at="fixed",
        )

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["summary"]["auto_accepts"], 2)
        self.assertEqual(report["summary"]["precision"], 1.0)
        self.assertEqual(report["summary"]["uncertain_auto_accepts"], 0)
        self.assertEqual(report["source_recommendation_baseline"]["selected"], 4)
        self.assertEqual(report["source_recommendation_baseline"]["uncertain_selected"], 1)

    def test_uncertain_auto_accept_fails_gate(self) -> None:
        goldset = build_identity_fragment_consolidation_goldset(
            [_manifest("easy", [("a", "confirmed_same"), ("b", "uncertain")])],
            goldset_id="fragments",
            version="1.0.0",
        )
        report = evaluate_identity_fragment_consolidation_goldset(
            goldset,
            {"easy": {"proposals": [_proposal("a"), _proposal("b")]}},
            min_labeled=1,
        )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["summary"]["uncertain_auto_accepts"], 1)
        self.assertFalse(report["gates"]["uncertain_auto_accepts"])


def _manifest(benchmark_id: str, reviews: list[tuple[str, str]]) -> dict:
    items = []
    for index, (key, status) in enumerate(reviews, start=1):
        expected = True if status == "confirmed_same" else False if status == "confirmed_different" else None
        items.append(
            {
                "audit_index": index,
                "candidate_key": key,
                "source": {"tracklet_id": f"A01~{index}", "raw_tracker_id": f"source-{index}"},
                "transition": {"gap_sec": 0.5},
                "target": {"tracklet_id": f"A01~{index + 1}", "raw_tracker_id": f"target-{index}"},
                "decision": {
                    "source_quality_class": "recommended_review",
                    "base_confidence": 0.9,
                    "distance_m": 0.5,
                    "required_speed_mps": 1.0,
                    "reason_codes": [],
                },
                "manual_review": {
                    "status": status,
                    "same_person": expected,
                    "reviewed_at": "fixed",
                    "notes": "",
                },
            }
        )
    return {
        "audit_kind": "fragment_consolidation",
        "algorithm": {"name": "audit"},
        "benchmark": {"benchmark_id": benchmark_id, "label": benchmark_id},
        "source": {"consolidation_algorithm": {"name": "consolidation"}},
        "items": items,
    }


def _proposal(
    key: str,
    *,
    gap_frames: int = 10,
    gap_seconds: float = 0.4,
    confidence: float = 0.9,
) -> dict:
    return {
        "proposal_key": key,
        "decision": "recommended_review",
        "gap_frames": gap_frames,
        "gap_seconds": gap_seconds,
        "confidence": confidence,
        "endpoint_distance_m": 0.8,
        "required_speed_mps": 2.0,
        "source_active_ratio": 0.95,
        "target_active_ratio": 0.95,
        "source_team_label": "A",
        "target_team_label": "A",
        "shared_production_anchor": "slot-A01",
        "reason_codes": [],
        "overlap_frames": 0,
    }


if __name__ == "__main__":
    unittest.main()
