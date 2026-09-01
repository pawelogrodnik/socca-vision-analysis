from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


class MatchGroupApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_selector_is_compact_and_only_returns_physical_sources(self) -> None:
        with patch("app.main.list_eligible_match_group_sources", return_value=[
            {"id": "physical-1", "report_type": "public_match_report", "analyzed_duration_sec": 12},
        ]):
            response = self.client.get("/api/published/match-groups/eligible-sources")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["report_type"], "public_match_report")

    def test_create_only_accepts_member_ids_and_metadata_and_generates_report(self) -> None:
        group = {
            "group_id": "match-group-1",
            "metadata": {"title": "Full match"},
            "members": [{"published_id": "one"}, {"published_id": "two"}],
            "timing": {"analyzed_duration_sec": 20},
            "compatibility": {"status": "compatible"},
        }
        with patch("app.main.create_match_group", return_value=group) as create, patch(
            "app.main.generate_match_group_report", return_value={"report_type": "public_aggregate_match_report"}
        ), patch("app.main.validate_match_group", return_value={"status": "compatible", "blocking_reasons": []}):
            response = self.client.post("/api/published/match-groups", json={
                "member_published_ids": ["one", "two"],
                "metadata": {"title": "Full match"},
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(create.call_args.kwargs["member_published_ids"], ["one", "two"])
        self.assertEqual(response.json()["report"]["report_type"], "public_aggregate_match_report")

    def test_forged_aggregate_statistics_are_rejected_by_request_contract(self) -> None:
        response = self.client.post("/api/published/match-groups", json={
            "member_published_ids": ["one", "two"],
            "metadata": {"title": "Full match"},
            "summed_distance_m": 999999,
        })
        self.assertEqual(response.status_code, 422)

    def test_api_exposes_dedicated_aggregate_report_route(self) -> None:
        operation = app.openapi()["paths"]["/api/published/match-groups/{group_id}/report"]["get"]
        self.assertEqual(operation["summary"], "Api Get Match Group Report")


if __name__ == "__main__":
    unittest.main()
