from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.main import (
    api_create_match_group,
    api_generate_match_group_video,
    api_get_match_group_video_file,
    api_get_match_group_video,
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
        report = {"report_type": "public_aggregate_match_report"}
        with patch("app.main.create_match_group_and_generate_report", return_value=(group, report)) as create, patch(
            "app.main.validate_match_group", return_value={"status": "compatible", "blocking_reasons": []}
        ):
            response = api_create_match_group(MatchGroupPayload.model_validate({
                "member_published_ids": ["one", "two"], "metadata": {"title": "Full match"},
            }))
        self.assertEqual(create.call_args.kwargs["member_published_ids"], ["one", "two"])
        self.assertTrue(callable(create.call_args.kwargs["generate_report"]))
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

    def test_api_exposes_status_and_background_generation_for_group_video(self) -> None:
        status = {"group_id": "match-group-1", "status": "not_generated", "artifact_url": None}
        queued = {"group_id": "match-group-1", "status": "generating"}
        with patch("app.main.get_match_group_video_status", return_value=status), patch(
            "app.main.submit_match_group_video_generation", return_value=queued
        ):
            self.assertEqual(api_get_match_group_video("match-group-1"), status)
            self.assertEqual(api_generate_match_group_video("match-group-1"), queued)
        paths = app.openapi()["paths"]
        self.assertIn("/api/published/match-groups/{group_id}/video", paths)
        self.assertIn("/api/published/match-groups/{group_id}/video/generate", paths)
        self.assertIn("/api/published/match-groups/{group_id}/video/file", paths)

    def test_stale_combined_video_is_never_served_as_current(self) -> None:
        with patch("app.main.get_match_group_video_status", return_value={"status": "stale"}):
            with self.assertRaises(HTTPException) as error:
                api_get_match_group_video_file("match-group-1")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(error.exception.detail["code"], "combined_video_not_current")


if __name__ == "__main__":
    unittest.main()
