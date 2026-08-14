from __future__ import annotations

import unittest

from app.services.identity_review_scope import (
    identity_review_scope_digest,
    identity_review_scope_read_model,
    review_scope_dependency_matches,
    team_review_scope,
    validate_identity_review_scope,
)


class IdentityReviewScopeTests(unittest.TestCase):
    def test_team_stats_only_is_recognized_and_exposed_without_frontend_inference(self) -> None:
        match = {
            "teams": [{"name": "Corgi"}, {"name": "Verisk"}],
            "identity_review_scope": {
                "teams": {"A": "complete_roster", "B": "team_stats_only"},
            },
        }
        self.assertEqual(team_review_scope(match, "B"), "team_stats_only")
        model = identity_review_scope_read_model(match)
        self.assertTrue(model["teams"]["A"]["named_player_review_required"])
        self.assertFalse(model["teams"]["B"]["named_player_review_required"])
        self.assertTrue(model["teams"]["B"]["team_stats_required"])

    def test_missing_explicit_scope_preserves_unspecified_legacy_semantics(self) -> None:
        model = identity_review_scope_read_model({"teams": [{}, {}]})
        self.assertFalse(model["explicit"])
        self.assertEqual(model["teams"]["A"]["scope"], "unspecified")
        self.assertTrue(model["teams"]["B"]["named_player_review_required"])

    def test_top_level_scope_overrides_legacy_team_field(self) -> None:
        match = {
            "teams": [
                {"identity_coverage_scope": "complete_roster"},
                {"identity_coverage_scope": "complete_roster"},
            ],
            "identity_review_scope": {
                "teams": {"A": "complete_roster", "B": "team_stats_only"},
            },
        }

        self.assertEqual(team_review_scope(match, "B"), "team_stats_only")

    def test_legacy_team_scope_is_still_an_explicit_dependency(self) -> None:
        model = identity_review_scope_read_model({
            "teams": [
                {"identity_coverage_scope": "complete_roster"},
                {"identity_coverage_scope": "team_stats_only"},
            ],
        })

        self.assertTrue(model["explicit"])
        self.assertEqual(model["teams"]["B"]["scope"], "team_stats_only")

    def test_scope_digest_changes_when_policy_changes(self) -> None:
        first = {"identity_review_scope": {"teams": {"A": "complete_roster", "B": "team_stats_only"}}}
        second = {"identity_review_scope": {"teams": {"A": "complete_roster", "B": "complete_roster"}}}
        self.assertNotEqual(identity_review_scope_digest(first), identity_review_scope_digest(second))

    def test_cached_progress_rejects_an_incompatible_scope_fingerprint(self) -> None:
        team_only = {
            "identity_review_scope": {
                "teams": {"A": "complete_roster", "B": "team_stats_only"}
            }
        }
        both_full = {
            "identity_review_scope": {
                "teams": {"A": "complete_roster", "B": "complete_roster"}
            }
        }
        artifact = {
            "source_review_scope_digest": identity_review_scope_digest(team_only)
        }

        self.assertTrue(review_scope_dependency_matches(team_only, artifact))
        self.assertFalse(review_scope_dependency_matches(both_full, artifact))

    def test_validation_requires_both_team_policies(self) -> None:
        with self.assertRaisesRegex(ValueError, "Team B"):
            validate_identity_review_scope({"teams": {"A": "complete_roster"}})


if __name__ == "__main__":
    unittest.main()
