from __future__ import annotations

import unittest

from app.main import app


class ReviewedIdentityProgressApiTests(unittest.TestCase):
    def test_team_filter_query_contract_accepts_only_a_or_b(self) -> None:
        operation = app.openapi()["paths"][
            "/api/matches/{match_id}/reviewed-identity/review-progress"
        ]["get"]
        parameter = next(
            row for row in operation["parameters"] if row["name"] == "team_label"
        )
        variants = parameter["schema"]["anyOf"]
        allowed = next(row["enum"] for row in variants if "enum" in row)

        self.assertFalse(parameter["required"])
        self.assertEqual(allowed, ["A", "B"])


if __name__ == "__main__":
    unittest.main()
