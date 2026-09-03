from __future__ import annotations

import unittest

from app.main import app


class ReviewedIdentityProgressApiTests(unittest.TestCase):
    def test_team_filter_query_contract_accepts_a_b_or_uncertain_team(self) -> None:
        operation = app.openapi()["paths"][
            "/api/matches/{match_id}/reviewed-identity/review-progress"
        ]["get"]
        parameter = next(
            row for row in operation["parameters"] if row["name"] == "team_label"
        )
        variants = parameter["schema"]["anyOf"]
        allowed = next(row["enum"] for row in variants if "enum" in row)

        self.assertFalse(parameter["required"])
        self.assertEqual(allowed, ["A", "B", "U"])

    def test_queue_query_defaults_to_required_and_supports_optional_audit(self) -> None:
        operation = app.openapi()["paths"]["/api/matches/{match_id}/reviewed-identity/review-progress"]["get"]
        parameter = next(row for row in operation["parameters"] if row["name"] == "queue")
        self.assertEqual(parameter["schema"]["default"], "required")
        self.assertEqual(parameter["schema"]["enum"], ["required", "optional_audit"])


if __name__ == "__main__":
    unittest.main()
