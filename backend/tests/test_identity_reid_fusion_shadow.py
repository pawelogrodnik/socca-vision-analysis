from __future__ import annotations

import unittest

from app.services.identity_reid_fusion_shadow import (
    build_identity_reid_fusion_shadow,
    score_reid_fused_proposal,
)


class IdentityReIdFusionShadowTests(unittest.TestCase):
    def test_reid_changes_review_ranking_but_never_merges(self) -> None:
        document = build_identity_reid_fusion_shadow(
            _consolidation_doc(),
            _reid_doc(),
            generated_at="fixed",
        )
        rows = {row["proposal_key"]: row for row in document["proposals"]}

        self.assertEqual(rows["same"]["fused_rank"], 1)
        self.assertEqual(rows["different"]["fused_rank"], 2)
        self.assertTrue(rows["same"]["reid_applied"])
        self.assertTrue(all(not row["automatic_merge"] for row in rows.values()))
        self.assertEqual(document["summary"]["automatic_merges"], 0)
        self.assertFalse(document["safety"]["mutates_production_identity"])

    def test_reid_adjustment_is_bounded(self) -> None:
        result = score_reid_fused_proposal(
            _proposal("candidate", confidence=0.95),
            _evidence("candidate", distance=1.0),
            source_reid_subject=_subject("candidate-source"),
            target_reid_subject=_subject("candidate-target"),
            parameters={"reid_weight": 1.0, "max_absolute_cost_adjustment": 0.03},
        )

        self.assertAlmostEqual(result["baseline_cost"], 0.05)
        self.assertAlmostEqual(result["cost_adjustment"], 0.03)
        self.assertAlmostEqual(result["fused_cost"], 0.08)

    def test_missing_reid_preserves_geometric_cost(self) -> None:
        result = score_reid_fused_proposal(
            _proposal("candidate", confidence=0.72),
            None,
        )

        self.assertFalse(result["reid_applied"])
        self.assertEqual(result["baseline_cost"], result["fused_cost"])
        self.assertIn("reid_pair_unavailable", result["reason_codes"])

    def test_hard_team_constraint_cannot_be_overridden_by_reid(self) -> None:
        proposal = _proposal("candidate", confidence=0.7)
        proposal["target_team_label"] = "B"
        result = score_reid_fused_proposal(
            proposal,
            _evidence("candidate", distance=0.0),
            source_reid_subject=_subject("candidate-source"),
            target_reid_subject=_subject("candidate-target"),
        )

        self.assertFalse(result["reid_applied"])
        self.assertEqual(result["decision"], "hard_constraint_blocked")
        self.assertEqual(result["baseline_cost"], result["fused_cost"])
        self.assertIn("known_team_mismatch", result["hard_constraint_reasons"])
        self.assertFalse(result["automatic_merge"])

    def test_weight_zero_is_exact_geometric_baseline(self) -> None:
        result = score_reid_fused_proposal(
            _proposal("candidate", confidence=0.73),
            _evidence("candidate", distance=0.01),
            source_reid_subject=_subject("candidate-source"),
            target_reid_subject=_subject("candidate-target"),
            parameters={"reid_weight": 0.0},
        )

        self.assertFalse(result["reid_applied"])
        self.assertEqual(result["baseline_cost"], result["fused_cost"])
        self.assertEqual(result["cost_adjustment"], 0.0)

    def test_unreliable_or_insufficient_reid_never_changes_cost(self) -> None:
        cases = [
            (_subject("candidate-source", reliable=False), _subject("candidate-target")),
            (_subject("candidate-source", accepted=2), _subject("candidate-target")),
            (_subject("candidate-source", dispersion=0.5), _subject("candidate-target")),
        ]
        for source, target in cases:
            with self.subTest(source=source):
                result = score_reid_fused_proposal(
                    _proposal("candidate", confidence=0.73),
                    _evidence("candidate", distance=0.01),
                    source_reid_subject=source,
                    target_reid_subject=target,
                )
                self.assertFalse(result["reid_applied"])
                self.assertEqual(result["baseline_cost"], result["fused_cost"])

    def test_untrusted_candidate_flags_block_reid(self) -> None:
        flags = (
            "merges_production_subjects",
            "merges_multiple_production_subjects",
            "cross_production_transition",
            "uncertain_transition",
            "production_anchor_team_mismatch",
        )
        for flag in flags:
            with self.subTest(flag=flag):
                result = score_reid_fused_proposal(
                    _proposal("candidate", confidence=0.73),
                    _evidence("candidate", distance=0.01),
                    source_reid_subject=_subject("candidate-source"),
                    target_reid_subject=_subject("candidate-target"),
                    source_candidate={"quality_flags": [flag], "role": "field_player"},
                    target_candidate={"quality_flags": [], "role": "field_player"},
                )
                self.assertFalse(result["reid_applied"])
                self.assertIn(
                    f"source_candidate_{flag}", result["hard_constraint_reasons"]
                )

    def test_role_speed_and_non_person_constraints_block_reid(self) -> None:
        proposal = _proposal("candidate", confidence=0.73)
        proposal["required_speed_mps"] = 13.0
        result = score_reid_fused_proposal(
            proposal,
            _evidence("candidate", distance=0.01),
            source_reid_subject=_subject("candidate-source"),
            target_reid_subject=_subject("candidate-target"),
            source_candidate={"quality_flags": [], "role": "goalkeeper"},
            target_candidate={"quality_flags": [], "role": "field_player"},
            visual_content_evidence={"quality": "invalid_content"},
        )

        self.assertFalse(result["reid_applied"])
        self.assertEqual(result["baseline_cost"], result["fused_cost"])
        self.assertEqual(
            set(result["hard_constraint_reasons"]),
            {"endpoint_not_person", "impossible_required_speed", "role_conflict"},
        )

    def test_output_is_deterministic_for_reordered_inputs(self) -> None:
        first = build_identity_reid_fusion_shadow(
            _consolidation_doc(),
            _reid_doc(),
            generated_at="fixed",
        )
        consolidation = _consolidation_doc()
        consolidation["proposals"].reverse()
        reid = _reid_doc()
        reid["pairs"].reverse()
        second = build_identity_reid_fusion_shadow(
            consolidation,
            reid,
            generated_at="fixed",
        )

        self.assertEqual(first, second)


def _proposal(key: str, *, confidence: float) -> dict:
    return {
        "proposal_key": key,
        "source_candidate_subject_id": f"{key}-source",
        "target_candidate_subject_id": f"{key}-target",
        "source_candidate_player_id": "A01",
        "target_candidate_player_id": "A01~2",
        "source_team_label": "A",
        "target_team_label": "A",
        "shared_production_anchor": "slot-A01",
        "decision": "recommended_review",
        "confidence": confidence,
        "gap_frames": 3,
        "gap_seconds": 0.1,
        "overlap_frames": 0,
        "endpoint_distance_m": 0.3,
        "required_speed_mps": 3.0,
        "source_active_ratio": 1.0,
        "target_active_ratio": 1.0,
        "reason_codes": [],
    }


def _evidence(key: str, *, distance: float) -> dict:
    return {
        "proposal_key": key,
        "status": "available",
        "prototype_distance": distance,
        "appearance_reliable": True,
    }


def _consolidation_doc() -> dict:
    return {
        "algorithm": {"name": "consolidation", "version": "1"},
        "proposals": [
            _proposal("different", confidence=0.75),
            _proposal("same", confidence=0.75),
        ],
    }


def _reid_doc() -> dict:
    return {
        "algorithm": {"name": "reid", "version": "1"},
        "subjects": [
            _subject("different-source"),
            _subject("different-target"),
            _subject("same-source"),
            _subject("same-target"),
        ],
        "pairs": [
            _evidence("different", distance=0.9),
            _evidence("same", distance=0.1),
        ],
    }


def _subject(
    subject_id: str,
    *,
    reliable: bool = True,
    accepted: int = 8,
    dispersion: float = 0.2,
) -> dict:
    return {
        "candidate_subject_id": subject_id,
        "appearance_reliable": reliable,
        "accepted_embeddings": accepted,
        "prototype_dispersion": dispersion,
    }


if __name__ == "__main__":
    unittest.main()
