from __future__ import annotations

import unittest

from app.services.identity_jersey_number_candidate_shadow import (
    build_identity_jersey_number_candidate_integration_shadow,
)
from app.services.identity_jersey_number_heldout_validation import (
    build_identity_jersey_number_heldout_case_contract,
    build_identity_jersey_number_heldout_validation,
)


class IdentityJerseyNumberCandidateShadowTests(unittest.TestCase):
    def test_manual_production_unchanged_never_replaces_canonical_proof(self) -> None:
        assignment, propagation, targeted = _current_documents()
        validated_assignment, validated_propagation, validated_targeted = _current_documents()
        validated_assignment["candidates"].append({"candidate_subject_id": "validated"})
        heldout = _validation(
            validated_assignment, validated_propagation, validated_targeted
        )

        result = _build(assignment, propagation, targeted, heldout)

        self.assertFalse(result["safety"]["activation_enabled"])
        self.assertIn(
            "matching_canonical_heldout_case_missing", result["safety"]["reason_codes"]
        )

    def test_changed_current_assignment_no_longer_matches_validation_proof(self) -> None:
        assignment, propagation, targeted = _current_documents()
        heldout = _validation(assignment, propagation, targeted)
        assignment["candidates"].append({"candidate_subject_id": "changed"})

        result = _build(assignment, propagation, targeted, heldout)

        self.assertFalse(result["safety"]["activation_enabled"])
        self.assertIn(
            "matching_canonical_heldout_case_missing", result["safety"]["reason_codes"]
        )

    def test_matching_canonical_evidence_clears_proof_specific_blocks(self) -> None:
        assignment, propagation, targeted = _current_documents()
        heldout = _validation(assignment, propagation, targeted)

        result = _build(assignment, propagation, targeted, heldout)

        self.assertNotIn(
            "matching_canonical_heldout_case_missing", result["safety"]["reason_codes"]
        )
        self.assertNotIn(
            "matching_canonical_heldout_case_invalid", result["safety"]["reason_codes"]
        )
        self.assertTrue(result["safety"]["activation_enabled"])


def _build(
    assignment: dict, propagation: dict, targeted: dict, heldout: dict
) -> dict:
    return build_identity_jersey_number_candidate_integration_shadow(
        assignment,
        propagation,
        targeted_evaluation_doc=targeted,
        heldout_validation_doc=heldout,
        production_identity_unchanged=True,
        activation_requested=True,
        generated_at="fixed",
    )


def _current_documents() -> tuple[dict, dict, dict]:
    return (
        {"safety": {"benchmark_gate": {"passed": True}}, "candidates": []},
        {
            "status": "fresh",
            "safety": {"lineage_gate": {"passed": True}},
            "summary": {"cross_subject_propagations": 0, "automatic_assignments": 0},
            "subjects": [],
        },
        {"summary": {"safety_passed": True, "unexpected_propagated_tracklets": 0}},
    )


def _validation(assignment: dict, propagation: dict, targeted: dict) -> dict:
    contracts = [
        build_identity_jersey_number_heldout_case_contract(
            benchmark_id=f"benchmark-{source_match_key}",
            source_match_key=source_match_key,
            recognizer_doc={
                "calibration": {"calibration_status": "measured"},
                "heldout_source_match_key": source_match_key,
            },
            assignment_doc=assignment,
            propagation_doc=propagation,
            targeted_evaluation_doc=targeted,
            production_before=_production_hashes(),
            production_after=_production_hashes(),
            generated_at="fixed",
        )
        for source_match_key in ("match-a", "match-b")
    ]
    return build_identity_jersey_number_heldout_validation(
        [{"case_contract_doc": contract} for contract in contracts],
        minimum_positive_multi_tracklet_propagations=0,
        generated_at="fixed",
    )


def _production_hashes() -> dict[str, str | None]:
    return {
        "global_identity.json": "global",
        "stable_players.json": "stable",
        "player_identity_assignments.json": "assignments",
    }


if __name__ == "__main__":
    unittest.main()
