from __future__ import annotations

import unittest

from app.services.identity_jersey_number_heldout_validation import (
    build_identity_jersey_number_heldout_case_contract,
    build_identity_jersey_number_heldout_validation,
    build_production_identity_artifact_comparison,
)


class IdentityJerseyNumberHeldoutValidationTests(unittest.TestCase):
    def test_clips_from_one_match_do_not_satisfy_multi_match_gate(self) -> None:
        result = build_identity_jersey_number_heldout_validation(
            [
                _case("clip-a", "corgi-verisk-2026-07-13"),
                _case("clip-b", "corgi-verisk-2026-07-13"),
                _case("clip-c", "corgi-verisk-2026-07-13"),
            ],
            generated_at="fixed",
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["summary"]["distinct_source_matches"], 1)
        self.assertIn(
            "insufficient_external_match_coverage", result["summary"]["reason_codes"]
        )

    def test_two_clean_source_matches_pass_gate(self) -> None:
        result = build_identity_jersey_number_heldout_validation(
            [_case("match-a", "match-a"), _case("match-b", "match-b")],
            generated_at="fixed",
        )

        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["summary"]["activation_gate_passed"])

    def test_false_read_blocks_gate(self) -> None:
        first = _case("match-a", "match-a")
        first["recognizer_doc"]["calibration"]["false_number_on_plain_shirt"] = 1

        result = build_identity_jersey_number_heldout_validation(
            [first, _case("match-b", "match-b")], generated_at="fixed"
        )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("false_jersey_number_reads_detected", result["summary"]["reason_codes"])

    def test_incomplete_case_cannot_pass_gate(self) -> None:
        incomplete = _case("match-a", "match-a")
        incomplete.pop("targeted_evaluation_doc")

        result = build_identity_jersey_number_heldout_validation(
            [incomplete, _case("match-b", "match-b")], generated_at="fixed"
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["summary"]["invalid_cases"], 1)
        self.assertIn("heldout_case_contract_incomplete", result["summary"]["reason_codes"])
        self.assertIn(
            "required_shadow_documents_missing", result["cases"][0]["reason_codes"]
        )

    def test_output_is_deterministic_for_fixed_timestamp(self) -> None:
        cases = [_case("match-a", "match-a"), _case("match-b", "match-b")]

        first = build_identity_jersey_number_heldout_validation(cases, generated_at="fixed")
        second = build_identity_jersey_number_heldout_validation(cases, generated_at="fixed")

        self.assertEqual(first, second)

    def test_production_comparison_requires_all_artifacts_to_match(self) -> None:
        unchanged = _production_hashes("same")
        result = build_production_identity_artifact_comparison(unchanged, unchanged)

        self.assertTrue(result["production_identity_unchanged"])
        self.assertEqual(result["missing_required_artifacts"], [])
        self.assertEqual(result["changed_required_artifacts"], [])

        changed = dict(unchanged)
        changed["stable_players.json"] = "changed"
        result = build_production_identity_artifact_comparison(unchanged, changed)

        self.assertFalse(result["production_identity_unchanged"])
        self.assertEqual(result["changed_required_artifacts"], ["stable_players.json"])

    def test_case_contract_round_trip_passes_multi_match_gate(self) -> None:
        contracts = [
            _contract("match-a", "match-a"),
            _contract("match-b", "match-b"),
        ]

        result = build_identity_jersey_number_heldout_validation(
            [{"case_contract_doc": contract} for contract in contracts],
            generated_at="fixed",
        )

        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["summary"]["activation_gate_passed"])

    def test_tampered_artifact_comparison_invalidates_contract(self) -> None:
        contract = _contract("match-a", "match-a")
        contract["production_artifact_comparison"]["artifacts"] = []

        result = build_identity_jersey_number_heldout_validation(
            [{"case_contract_doc": contract}],
            generated_at="fixed",
        )

        self.assertFalse(result["cases"][0]["case_contract_valid"])
        self.assertFalse(result["cases"][0]["production_identity_unchanged"])
        self.assertIn(
            "production_identity_artifact_comparison_invalid",
            result["cases"][0]["reason_codes"],
        )

    def test_case_contract_is_deterministic_for_fixed_timestamp(self) -> None:
        first = _contract("match-a", "match-a")
        second = _contract("match-a", "match-a")

        self.assertEqual(first, second)


def _case(benchmark_id: str, source_match_key: str) -> dict:
    return {
        "benchmark_id": benchmark_id,
        "source_match_key": source_match_key,
        "held_out": True,
        "production_identity_unchanged": True,
        "recognizer_doc": {
            "calibration": {
                "calibration_status": "measured",
                "numbered_player_false_positives": 0,
                "false_number_on_plain_shirt": 0,
            }
        },
        "assignment_doc": {
            "safety": {
                "benchmark_gate": {
                    "passed": True,
                    "identity_false_assignments": 0,
                }
            }
        },
        "propagation_doc": {
            "summary": {"number_propagated_tracklets": 1, "automatic_assignments": 0}
        },
        "targeted_evaluation_doc": {
            "summary": {
                "safety_passed": True,
                "eligible_matched_hidden_target_tracklets": 1,
                "unexpected_propagated_tracklets": 0,
                "automatic_assignments": 0,
            }
        },
    }


def _contract(benchmark_id: str, source_match_key: str) -> dict:
    case = _case(benchmark_id, source_match_key)
    hashes = _production_hashes("same")
    return build_identity_jersey_number_heldout_case_contract(
        benchmark_id=benchmark_id,
        source_match_key=source_match_key,
        recognizer_doc=case["recognizer_doc"],
        assignment_doc=case["assignment_doc"],
        propagation_doc=case["propagation_doc"],
        targeted_evaluation_doc=case["targeted_evaluation_doc"],
        production_before=hashes,
        production_after=hashes,
        generated_at="fixed",
    )


def _production_hashes(value: str) -> dict[str, str]:
    return {
        "global_identity.json": f"{value}-global",
        "stable_players.json": f"{value}-stable",
        "player_identity_assignments.json": f"{value}-assignments",
    }


if __name__ == "__main__":
    unittest.main()
