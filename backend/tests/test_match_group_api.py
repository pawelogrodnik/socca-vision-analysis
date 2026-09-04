from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.main import (
    api_create_match_group,
    api_delete_match_group,
    api_generate_match_group_video,
    api_get_match_group_video_generation_file,
    api_get_match_group_video_file,
    api_get_match_group_video,
    api_get_match_group_report,
    api_get_match_group_external_video,
    api_preview_match_group_refresh,
    api_refresh_match_group_to_latest,
    api_save_match_group_external_video,
    api_list_eligible_match_group_sources,
    app,
)
from app.models import MatchGroupExternalVideoPayload, MatchGroupPayload
from app.services.match_group_video import MatchGroupVideoError


class MatchGroupApiTests(unittest.TestCase):
    def test_refresh_routes_are_server_authoritative_and_exposed(self) -> None:
        preview = {"group_id": "match-group-1", "status": "refreshable", "members": [], "blocking_reasons": []}
        refreshed = {"status": "refreshed", "group": {"group_id": "match-group-1"}}
        with patch("app.main.preview_match_group_refresh", return_value=preview) as request_preview, patch(
            "app.main.refresh_match_group_to_latest", return_value=refreshed
        ) as refresh:
            self.assertEqual(api_preview_match_group_refresh("match-group-1"), preview)
            self.assertEqual(api_refresh_match_group_to_latest("match-group-1"), refreshed)
        request_preview.assert_called_once_with("match-group-1")
        refresh.assert_called_once_with("match-group-1")
        paths = app.openapi()["paths"]
        self.assertIn("/api/published/match-groups/{group_id}/refresh-preview", paths)
        self.assertIn("/api/published/match-groups/{group_id}/refresh-to-latest", paths)

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
        with patch("app.main.load_match_group_report", return_value=report), patch("app.main.validate_match_group", return_value=validation), patch("app.main.get_match_group_external_video", return_value={"status": "not_configured"}):
            response = api_get_match_group_report("match-group-1")
        self.assertEqual(response["report"], report)
        self.assertEqual(response["validation"]["status"], "stale")

    def test_external_video_routes_expose_only_the_server_projection(self) -> None:
        state = {"group_id": "match-group-1", "status": "current", "external_video": {"embed_url": "https://www.youtube-nocookie.com/embed/AbCdEfGhI_1"}}
        with patch("app.main.get_match_group_external_video", return_value=state), patch("app.main.save_match_group_external_video", return_value=state) as save:
            self.assertEqual(api_get_match_group_external_video("match-group-1"), state)
            self.assertEqual(api_save_match_group_external_video("match-group-1", MatchGroupExternalVideoPayload(url="https://youtu.be/AbCdEfGhI_1")), state)
        self.assertEqual(save.call_args.args, ("match-group-1", "https://youtu.be/AbCdEfGhI_1"))

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
        self.assertIn("/api/published/match-groups/{group_id}/video/generations/{generation_id}/file", paths)

    def test_stale_combined_video_is_never_served_as_current(self) -> None:
        with patch("app.main.get_match_group_video_status", return_value={"status": "stale"}):
            with self.assertRaises(HTTPException) as error:
                api_get_match_group_video_file("match-group-1")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(error.exception.detail["code"], "combined_video_not_current")

    def test_current_video_route_redirects_to_the_immutable_generation_url(self) -> None:
        artifact_url = "/api/published/match-groups/match-group-1/video/generations/generation-b/file"
        with patch("app.main.get_match_group_video_status", return_value={"status": "ready", "artifact_url": artifact_url}):
            response = api_get_match_group_video_file("match-group-1")
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], artifact_url)

    def test_exact_generation_route_serves_the_requested_generation_with_output_etag(self) -> None:
        video = Path("/tmp/immutable-generation.mp4")
        with patch("app.main.generation_video", return_value={"video_path": video, "manifest": {"output": {"semantic_digest": "digest-b"}}}):
            response = api_get_match_group_video_generation_file("match-group-1", "generation-b")
        self.assertEqual(response.headers["etag"], "digest-b")

    def test_delete_reports_video_generation_in_progress_as_a_conflict(self) -> None:
        with patch("app.main.delete_match_group_when_video_idle", side_effect=MatchGroupVideoError("video_generation_in_progress", "rendering")):
            with self.assertRaises(HTTPException) as error:
                api_delete_match_group("match-group-1")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(error.exception.detail["code"], "video_generation_in_progress")


if __name__ == "__main__":
    unittest.main()
