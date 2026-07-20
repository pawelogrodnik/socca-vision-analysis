from __future__ import annotations

import unittest

from app.services.identity_fragment_visual_content_gate import (
    classify_with_visual_content_gate,
    evaluate_visual_content_gate,
)


class IdentityFragmentVisualContentGateTests(unittest.TestCase):
    def test_supported_person_content_preserves_strict_shadow_accept(self) -> None:
        result = classify_with_visual_content_gate(
            _proposal("a"),
            _content_pair("a", "person_content_supported"),
        )

        self.assertTrue(result["strict_gate_passed"])
        self.assertTrue(result["visual_content_gate_passed"])
        self.assertTrue(result["auto_accept"])
        self.assertFalse(result["safe_for_production_identity_merge"])

    def test_not_person_blocks_otherwise_strict_candidate(self) -> None:
        result = classify_with_visual_content_gate(
            _proposal("a"),
            _content_pair("a", "invalid_content"),
        )

        self.assertTrue(result["strict_gate_passed"])
        self.assertFalse(result["auto_accept"])
        self.assertIn("endpoint_not_person", result["reason_codes"])

    def test_missing_unclear_and_unavailable_content_abstain(self) -> None:
        cases = [
            (None, "visual_content_evidence_missing"),
            (_content_pair("a", "unclear"), "visual_content_unclear"),
            (_content_pair("a", "unavailable"), "visual_content_unavailable"),
        ]

        for evidence, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                result = classify_with_visual_content_gate(_proposal("a"), evidence)
                self.assertFalse(result["auto_accept"])
                self.assertIn(expected_reason, result["reason_codes"])

    def test_person_content_does_not_override_failed_strict_policy(self) -> None:
        proposal = _proposal("a")
        proposal["gap_frames"] = 0
        proposal["gap_seconds"] = 0.0

        result = classify_with_visual_content_gate(
            proposal,
            _content_pair("a", "person_content_supported"),
        )

        self.assertFalse(result["strict_gate_passed"])
        self.assertTrue(result["visual_content_gate_passed"])
        self.assertFalse(result["auto_accept"])

    def test_evaluator_reports_conservative_gate_impact(self) -> None:
        goldset = {
            "goldset_id": "fragments",
            "version": "1",
            "goldset_digest": "digest",
            "items": [
                _gold("easy", "same", "confirmed_same", True),
                _gold("easy", "different", "confirmed_different", False),
                _gold("easy", "unclear", "uncertain", None),
            ],
        }
        report = evaluate_visual_content_gate(
            goldset,
            {
                "easy": {
                    "proposals": [
                        _proposal("same"),
                        _proposal("different"),
                        _proposal("unclear"),
                    ]
                }
            },
            {
                "easy": {
                    "pairs": [
                        _content_pair("same", "person_content_supported"),
                        _content_pair("different", "invalid_content"),
                        _content_pair("unclear", "unclear"),
                    ]
                }
            },
            generated_at="fixed",
        )

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["summary"]["strict_auto_accepts"], 3)
        self.assertEqual(report["summary"]["gated_auto_accepts"], 1)
        self.assertEqual(report["summary"]["confusion"]["false_positive"], 0)
        self.assertEqual(report["summary"]["uncertain_auto_accepts"], 0)


def _proposal(key: str) -> dict:
    return {
        "proposal_key": key,
        "decision": "recommended_review",
        "gap_frames": 10,
        "gap_seconds": 0.4,
        "confidence": 0.9,
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


def _content_pair(key: str, quality: str) -> dict:
    return {
        "proposal_key": key,
        "quality": quality,
        "person_content_supported": quality == "person_content_supported",
        "blocks_automatic_identity_merge": quality == "invalid_content",
    }


def _gold(benchmark_id: str, key: str, status: str, expected: bool | None) -> dict:
    return {
        "benchmark_id": benchmark_id,
        "candidate_key": key,
        "review_status": status,
        "expected_same_person": expected,
    }


if __name__ == "__main__":
    unittest.main()
