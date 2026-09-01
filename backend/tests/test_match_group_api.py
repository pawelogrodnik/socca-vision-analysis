from __future__ import annotations

import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.main import (
    api_create_match_group,
    api_get_match_group_report,
    api_list_eligible_match_group_sources,
    app,
)
from app.models import MatchGroupPayload


class MatchGroupApiTests(unittest.TestCase):
    def test_selector_is_compact_and_only_returns_physical_sources(self) -> None:
        with patch("app.main.list_eligible_match_group_sources", return_value=[
            {"id": "physical-1", "report_type": "public_match_report", "analyzed_duration_sec": 12},
        ]):
            response = api_list_eligible_match_group_sources()
        self.assertEqual(response[0]["report_type"], "public_match_report")

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
            response = api_create_match_group(MatchGroupPayload.model_validate({
                "member_published_ids": ["one", "two"], "metadata": {"title": "Full match"},
            }))
        self.assertEqual(create.call_args.kwargs["member_published_ids"], ["one", "two"])
        self.assertEqual(response["report"]["report_type"], "public_aggregate_match_report")

    def test_forged_aggregate_statistics_are_rejected_by_request_contract(self) -> None:
        with self.assertRaises(ValidationError):
            MatchGroupPayload.model_validate({
                "member_published_ids": ["one", "two"],
                "metadata": {"title": "Full match"},
                "summed_distance_m": 999999,
            })

    def test_report_returns_last_coherent_bytes_with_authoritative_stale_validation(self) -> None:
        report = {"report_type": "public_aggregate_match_report"}
        validation = {"status": "stale", "blocking_reasons": [{"code": "source_generation_changed", "detail": "Republished."}]}
        with patch("app.main.load_match_group_report", return_value=report), patch(
            "app.main.validate_match_group", return_value=validation
        ):
            response = api_get_match_group_report("match-group-1")
        self.assertEqual(response["report"], report)
        self.assertEqual(response["validation"]["status"], "stale")

    def test_api_exposes_dedicated_aggregate_report_route(self) -> None:
        operation = app.openapi()["paths"]["/api/published/match-groups/{group_id}/report"]["get"]
        self.assertEqual(operation["summary"], "Api Get Match Group Report")


if __name__ == "__main__":
    unittest.main()
