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

    def test_team_u_and_cross_team_attribution_remain_required(self) -> None:
        self.assertTrue(required_review_relevant_for_scope({
            "source_team_label": "B",
            "effective_team_label": "U",
        }, self.match))
        self.assertTrue(required_review_relevant_for_scope({
            "source_team_label": "U",
            "effective_team_label": "U",
        }, self.match))
        self.assertTrue(required_review_relevant_for_scope({
            "source_team_label": "B",
            "effective_team_label": "B",
            "detected_team_labels": ["A", "B"],
            "reason_codes": ["identity_conflict"],
        }, self.match))

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

    def test_structured_team_attribution_states_fail_closed_for_unknown_and_cross_team(self) -> None:
        self.assertEqual(team_attribution_state({
            "source_team_label": "B",
            "effective_team_label": "U",
            "detected_team_labels": ["B"],
        }), "uncertain")
        self.assertEqual(team_attribution_state({
            "source_team_label": "B",
            "effective_team_label": "B",
            "detected_team_labels": ["A", "B"],
        }), "cross_team")
        self.assertEqual(team_attribution_state({}), "unknown")

    def test_mixed_scope_is_evaluated_per_exact_source(self) -> None:
        certain_b = {"mixed_hint": "same_team_b", "source": {"effective_team_label": "B"}}
        certain_a = {"mixed_hint": "same_team_a", "source": {"effective_team_label": "A"}}
        cross_team = {"mixed_hint": "cross_team", "source": {"effective_team_label": "B"}}
        team_u = {"mixed_hint": "unknown", "source": {"effective_team_label": "U"}}

        self.assertFalse(mixed_review_relevant_for_scope(certain_b, [{"team_label": "B"}, {"team_label": "B"}], self.match))
        self.assertTrue(mixed_review_relevant_for_scope(certain_a, [{"team_label": "A"}, {"team_label": "A"}], self.match))
        self.assertTrue(mixed_review_relevant_for_scope(cross_team, [{"team_label": "B"}, {"team_label": "B"}], self.match))
        self.assertTrue(mixed_review_relevant_for_scope(team_u, [{"team_label": "U"}, {"team_label": "U"}], self.match))

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
