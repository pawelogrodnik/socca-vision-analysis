from __future__ import annotations

import unittest

from app.services.identity_reviewed_scope_eligibility import (
    has_team_attribution_uncertainty,
    mixed_review_relevant_for_scope,
    required_review_relevant_for_scope,
    team_attribution_state,
)


class ReviewedIdentityScopeEligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.match = {
            "identity_review_scope": {
                "schema_version": "1.0.0",
                "teams": {"A": "complete_roster", "B": "team_stats_only"},
            },
            "teams": [{"team_label": "A"}, {"team_label": "B"}],
        }

    def test_certain_team_stats_only_player_identity_is_not_required(self) -> None:
        self.assertFalse(required_review_relevant_for_scope({
            "source_team_label": "B",
            "effective_team_label": "B",
            "coverage_team_label": "B",
            "reason_codes": ["identity_conflict"],
        }, self.match))

    def test_known_team_evidence_treats_u_as_neutral(self) -> None:
        cases = (
            (
                "certain B from B/U evidence",
                {
                    "source_team_label": "B",
                    "effective_team_label": "B",
                    "coverage_team_label": "B",
                    "detected_team_labels": ["B"],
                },
                "certain_B",
                False,
            ),
            (
                "certain B from unknown source and B evidence",
                {
                    "source_team_label": "U",
                    "effective_team_label": "B",
                    "coverage_team_label": "B",
                    "detected_team_labels": ["B"],
                },
                "certain_B",
                False,
            ),
            (
                "certain A from A/U evidence",
                {
                    "source_team_label": "A",
                    "effective_team_label": "U",
                    "coverage_team_label": "A",
                    "detected_team_labels": ["A"],
                },
                "certain_A",
                True,
            ),
            (
                "cross-team A/B evidence",
                {
                    "source_team_label": "B",
                    "effective_team_label": "B",
                    "coverage_team_label": "B",
                    "detected_team_labels": ["A", "B"],
                },
                "cross_team",
                True,
            ),
            (
                "cross-team A/B/U evidence",
                {
                    "source_team_label": "U",
                    "effective_team_label": "B",
                    "coverage_team_label": "B",
                    "detected_team_labels": ["A", "B"],
                },
                "cross_team",
                True,
            ),
            (
                "U-only evidence",
                {
                    "source_team_label": "U",
                    "effective_team_label": "U",
                    "coverage_team_label": "U",
                    "detected_team_labels": [],
                },
                "unknown",
                False,
            ),
        )

        for name, value, expected_state, required in cases:
            with self.subTest(name=name):
                self.assertEqual(team_attribution_state(value), expected_state)
                self.assertEqual(required_review_relevant_for_scope(value, self.match), required)

    def test_current_canonical_team_evidence_not_reason_text_classifies_certainty(self) -> None:
        certain_b_with_stale_diagnostic = {
            "source_team_label": "B",
            "effective_team_label": "B",
            "coverage_team_label": "B",
            "detected_team_labels": ["B"],
            "reason_codes": ["team_mismatch", "team_attribution_recovered"],
        }

        self.assertEqual(team_attribution_state(certain_b_with_stale_diagnostic), "certain_B")
        self.assertFalse(has_team_attribution_uncertainty(certain_b_with_stale_diagnostic))
        self.assertFalse(required_review_relevant_for_scope(certain_b_with_stale_diagnostic, self.match))

    def test_explicit_team_unknown_never_infers_a_known_team(self) -> None:
        self.assertEqual(team_attribution_state({
            "source_team_label": "B",
            "effective_team_label": "B",
            "detected_team_labels": ["B"],
            "current_decision": {"action": "team_unknown"},
        }), "unknown")
        self.assertFalse(required_review_relevant_for_scope({
            "source_team_label": "B",
            "effective_team_label": "B",
            "detected_team_labels": ["B"],
            "current_decision": {"action": "team_unknown"},
        }, self.match))

    def test_contradictory_operator_assignment_fails_closed(self) -> None:
        self.assertEqual(team_attribution_state({
            "source_team_label": "B",
            "effective_team_label": "B",
            "detected_team_labels": ["B"],
            "current_decision": {"action": "assign_team", "team_label": "A"},
        }), "cross_team")

    def test_mixed_scope_is_evaluated_per_exact_source(self) -> None:
        certain_b = {"mixed_hint": "same_team_b", "source": {"effective_team_label": "B"}}
        certain_a = {"mixed_hint": "same_team_a", "source": {"effective_team_label": "A"}}
        cross_team = {"mixed_hint": "cross_team", "source": {"effective_team_label": "B"}}
        team_u = {"mixed_hint": "unknown", "source": {"effective_team_label": "U"}}

        self.assertFalse(mixed_review_relevant_for_scope(certain_b, [{"team_label": "B"}, {"team_label": "B"}], self.match))
        self.assertTrue(mixed_review_relevant_for_scope(certain_a, [{"team_label": "A"}, {"team_label": "A"}], self.match))
        self.assertTrue(mixed_review_relevant_for_scope(cross_team, [{"team_label": "B"}, {"team_label": "B"}], self.match))
        self.assertFalse(mixed_review_relevant_for_scope(team_u, [{"team_label": "U"}, {"team_label": "U"}], self.match))

    def test_b_and_u_only_mixed_source_is_nonmandatory_but_cross_team_is_mandatory(self) -> None:
        b_and_u = {
            "mixed_hint": "same_team_b",
            "source": {"effective_team_label": "B", "coverage_team_label": "B"},
        }
        a_b_and_u = {
            "mixed_hint": "cross_team",
            "source": {"effective_team_label": "B", "coverage_team_label": "B"},
        }

        self.assertFalse(mixed_review_relevant_for_scope(
            b_and_u,
            [{"team_label": "B"}, {"team_label": "U"}],
            self.match,
        ))
        self.assertTrue(mixed_review_relevant_for_scope(
            a_b_and_u,
            [{"team_label": "A"}, {"team_label": "B"}, {"team_label": "U"}],
            self.match,
        ))

    def test_b_only_player_continuity_and_identity_changes_do_not_make_mixed_required(self) -> None:
        b_only = {
            "mixed_hint": "same_team_b",
            "source": {
                "source_team_label": "B",
                "effective_team_label": "B",
                "detected_team_labels": ["B"],
                "reason_codes": ["material_identity_continuity_gap", "identity_conflict"],
            },
        }

        self.assertFalse(mixed_review_relevant_for_scope(
            b_only,
            [{"team_label": "B"}, {"team_label": "B"}],
            self.match,
        ))
