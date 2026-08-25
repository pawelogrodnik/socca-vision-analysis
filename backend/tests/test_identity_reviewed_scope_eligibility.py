from __future__ import annotations

import unittest

from app.services.identity_reviewed_scope_eligibility import (
    mixed_review_relevant_for_scope,
    required_review_relevant_for_scope,
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
            "source_team_label": "B",
            "effective_team_label": "B",
            "reason_codes": ["cross_team_identity_conflict"],
        }, self.match))

    def test_mixed_scope_is_evaluated_per_exact_source(self) -> None:
        certain_b = {"mixed_hint": "same_team_b", "source": {"effective_team_label": "B"}}
        certain_a = {"mixed_hint": "same_team_a", "source": {"effective_team_label": "A"}}
        cross_team = {"mixed_hint": "cross_team", "source": {"effective_team_label": "B"}}
        team_u = {"mixed_hint": "unknown", "source": {"effective_team_label": "U"}}

        self.assertFalse(mixed_review_relevant_for_scope(certain_b, [{"team_label": "B"}, {"team_label": "B"}], self.match))
        self.assertTrue(mixed_review_relevant_for_scope(certain_a, [{"team_label": "A"}, {"team_label": "A"}], self.match))
        self.assertTrue(mixed_review_relevant_for_scope(cross_team, [{"team_label": "B"}, {"team_label": "B"}], self.match))
        self.assertTrue(mixed_review_relevant_for_scope(team_u, [{"team_label": "U"}, {"team_label": "U"}], self.match))

