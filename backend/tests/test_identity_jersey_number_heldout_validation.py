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
        self.assertEqual(result["summary"]["distinct_source_matches"], 0)
        self.assertIn(
            "insufficient_external_match_coverage", result["summary"]["reason_codes"]
        )

    def test_two_clean_source_matches_pass_gate(self) -> None:
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

    def test_false_read_blocks_gate(self) -> None:
        first = _case("match-a", "match-a")
        first["recognizer_doc"]["calibration"]["false_number_on_plain_shirt"] = 1

        result = build_identity_jersey_number_heldout_validation(
            [
                {"case_contract_doc": _contract("match-a", "match-a", case=first)},
                {"case_contract_doc": _contract("match-b", "match-b")},
            ],
            generated_at="fixed",
        )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("false_jersey_number_reads_detected", result["summary"]["reason_codes"])

    def test_incomplete_case_cannot_pass_gate(self) -> None:
        incomplete = _contract("match-a", "match-a")
        incomplete.pop("safety")
        complete = _contract("match-b", "match-b")

        result = build_identity_jersey_number_heldout_validation(
            [
                {"case_contract_doc": incomplete},
                {"case_contract_doc": complete},
            ],
            generated_at="fixed",
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["summary"]["invalid_cases"], 1)
        self.assertIn("heldout_case_contract_incomplete", result["summary"]["reason_codes"])
        self.assertIn(
            "heldout_case_contract_invalid", result["cases"][0]["reason_codes"]
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

    def test_raw_case_cannot_spoof_canonical_origin(self) -> None:
        spoofed = _case("match-a", "match-a")
        spoofed["canonical_case_origin"] = True
        result = build_identity_jersey_number_heldout_validation(
            [spoofed, {"case_contract_doc": _contract("match-b", "match-b")}],
            generated_at="fixed",
        )

        self.assertFalse(result["cases"][0]["canonical_case_origin"])
        self.assertFalse(result["cases"][0]["case_contract_valid"])
        self.assertEqual(result["summary"]["distinct_source_matches"], 1)

    def test_reused_lineage_across_source_keys_cannot_pass_gate(self) -> None:
        shared = _case("shared", "shared")
        result = build_identity_jersey_number_heldout_validation(
            [
                {"case_contract_doc": _contract("match-a", "match-a", case=shared)},
                {"case_contract_doc": _contract("match-b", "match-b", case=shared)},
            ],
            generated_at="fixed",
        )

        self.assertFalse(result["summary"]["activation_gate_passed"])
        self.assertEqual(result["summary"]["distinct_source_matches"], 2)
        self.assertEqual(result["summary"]["distinct_independent_match_lineages"], 1)
        self.assertIn(
            "reused_independent_match_lineage_across_source_keys",
            result["summary"]["reason_codes"],
        )

    def test_duplicate_exact_lineage_does_not_inflate_propagations(self) -> None:
        contract = _contract("match-a", "match-a")
        result = build_identity_jersey_number_heldout_validation(
            [{"case_contract_doc": contract}, {"case_contract_doc": contract}],
            generated_at="fixed",
        )

        self.assertEqual(result["summary"]["positive_multi_tracklet_propagations"], 1)
        self.assertEqual(result["summary"]["duplicate_independent_match_lineages"], 1)
        self.assertIn("duplicate_independent_match_lineage", result["summary"]["reason_codes"])

    def test_invalid_canonical_case_cannot_pass_gate(self) -> None:
        for failure, reason in (
            ("calibration", "recognizer_calibration_not_measured"),
            ("assignment", "assignment_benchmark_gate_failed"),
            ("targeted", "targeted_evaluation_failed"),
            ("production", "production_identity_unchanged_not_verified"),
        ):
            with self.subTest(failure=failure):
                case = _case("match-a", "match-a")
                if failure == "calibration":
                    case["recognizer_doc"]["calibration"]["calibration_status"] = "unmeasured"
                elif failure == "assignment":
                    case["assignment_doc"]["safety"]["benchmark_gate"]["passed"] = False
                elif failure == "targeted":
                    case["targeted_evaluation_doc"]["summary"]["safety_passed"] = False
                before = _production_hashes("same")
                after = _production_hashes("changed" if failure == "production" else "same")
                contract = build_identity_jersey_number_heldout_case_contract(
                    benchmark_id="match-a",
                    source_match_key="match-a",
                    recognizer_doc=case["recognizer_doc"],
                    assignment_doc=case["assignment_doc"],
                    propagation_doc=case["propagation_doc"],
                    targeted_evaluation_doc=case["targeted_evaluation_doc"],
                    production_before=before,
                    production_after=after,
                    generated_at="fixed",
                )
                result = build_identity_jersey_number_heldout_validation(
                    [{"case_contract_doc": contract}, {"case_contract_doc": _contract("match-b", "match-b")}],
                    generated_at="fixed",
                )

                self.assertFalse(contract["case"]["case_contract_valid"])
                self.assertIn(reason, contract["case"]["reason_codes"])
                self.assertFalse(result["summary"]["activation_gate_passed"])


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
            "source_match_key": source_match_key,
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


def _contract(
    benchmark_id: str,
    source_match_key: str,
    *,
    case: dict | None = None,
) -> dict:
    case = case or _case(benchmark_id, source_match_key)
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


def _production_hashes(value: str) -> dict[str, str | None]:
    return {
        "global_identity.json": f"{value}-global",
        "stable_players.json": f"{value}-stable",
        "player_identity_assignments.json": f"{value}-assignments",
    }


if __name__ == "__main__":
    unittest.main()
