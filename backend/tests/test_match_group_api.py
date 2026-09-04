from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

import app.main as main_module
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
    api_update_match_group,
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
        self.assertTrue(callable(create.call_args.kwargs["generate_and_persist_report"]))
        self.assertEqual(response["report"]["report_type"], "public_aggregate_match_report")

    def test_forged_aggregate_statistics_are_rejected_by_request_contract(self) -> None:
        with self.assertRaises(ValidationError):
            MatchGroupPayload.model_validate({
                "member_published_ids": ["one", "two"],
                "metadata": {"title": "Full match"},
                "summed_distance_m": 999999,
            })

    def test_report_returns_one_coherent_snapshot_with_matching_validation(self) -> None:
        report = {"report_type": "public_aggregate_match_report"}
        validation = {"status": "stale", "blocking_reasons": [{"code": "source_generation_changed", "detail": "Republished."}]}
        snapshot = {"report": report, "manifest": {"group_id": "match-group-1"}, "validation": validation}
        with patch("app.main.get_coherent_match_group_report", return_value=snapshot), patch("app.main.get_match_group_external_video", return_value={"status": "not_configured"}):
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

    def test_video_request_reports_maintenance_reservation_as_a_conflict(self) -> None:
        with patch(
            "app.main.submit_match_group_video_generation",
            side_effect=MatchGroupVideoError("match_group_maintenance_in_progress", "refresh owns the group"),
        ):
            with self.assertRaises(HTTPException) as error:
                api_generate_match_group_video("match-group-1")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(error.exception.detail["code"], "match_group_maintenance_in_progress")


if __name__ == "__main__":
    unittest.main()


class MatchGroupCreateUpdateWiringTests(unittest.TestCase):
    """Production route wiring for CREATE/UPDATE report callbacks.

    These tests exercise the real API handlers with the real production
    callbacks instead of mocking the helper under test, so an inverted
    callback contract fails here even though service-level tests stay green.
    """

    def test_real_create_endpoint_persists_a_coherent_pair(self) -> None:
        from test_match_group_aggregation import _metadata, _write_source

        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two")
            physical_before = self._physical_bytes(root)
            seen: dict[str, object] = {}
            real_generate = main_module.generate_match_group_report

            def spy(group_id: object) -> object:
                seen["callback_argument"] = group_id
                return real_generate(group_id)  # type: ignore[arg-type]

            with patch("app.main.generate_match_group_report", side_effect=spy):
                response = api_create_match_group(self._payload(["published-one", "published-two"]))

            # The CREATE callback receives a persisted str group ID, not a manifest.
            self.assertIsInstance(seen["callback_argument"], str)
            group_id = str(response["group"]["group_id"])
            self.assertEqual(seen["callback_argument"], group_id)
            group_dir = root / "groups" / group_id
            self.assertTrue((group_dir / "manifest.json").is_file())
            self.assertTrue((group_dir / "public_report.json").is_file())
            self.assertEqual(response["report"]["group_id"], group_id)

            from app.services.match_group_aggregation import get_coherent_match_group_report

            snapshot = get_coherent_match_group_report(group_id)
            self.assertEqual(
                [row["published_id"] for row in snapshot["report"]["sources"]],
                ["published-one", "published-two"],
            )
            self.assertEqual(snapshot["validation"]["status"], "compatible")
            self.assertEqual(self._physical_bytes(root), physical_before)
            self.assertEqual(len(list((root / "groups").glob("match-group-*"))), 1)

    def test_real_create_report_failure_removes_the_new_group(self) -> None:
        from test_match_group_aggregation import _metadata, _write_source

        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two")
            physical_before = self._physical_bytes(root)

            from app.services.match_groups import MatchGroupError

            with patch(
                "app.main.generate_match_group_report",
                side_effect=MatchGroupError("aggregate_generation_blocked", "candidate exploded"),
            ):
                with self.assertRaises(HTTPException) as failure:
                    api_create_match_group(self._payload(["published-one", "published-two"]))

            self.assertEqual(failure.exception.status_code, 409)
            self.assertEqual(list((root / "groups").glob("match-group-*")), [])
            self.assertEqual(self._physical_bytes(root), physical_before)

    def test_real_update_endpoint_rebuilds_pair_from_replacement_manifest(self) -> None:
        from test_match_group_aggregation import _metadata, _write_source

        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two")
            created = api_create_match_group(self._payload(["published-one", "published-two"]))
            group_id = str(created["group"]["group_id"])
            seen: dict[str, object] = {}
            real_builder = main_module.build_match_group_report_candidate

            def spy(manifest: object) -> object:
                seen["callback_argument"] = manifest
                return real_builder(manifest)  # type: ignore[arg-type]

            with patch("app.main.build_match_group_report_candidate", side_effect=spy):
                response = api_update_match_group(
                    group_id, self._payload(["published-one", "published-two"], title="Updated title")
                )

            # The UPDATE builder receives the in-memory replacement manifest dict.
            self.assertIsInstance(seen["callback_argument"], dict)
            self.assertEqual(seen["callback_argument"]["metadata"]["title"], "Updated title")  # type: ignore[index]
            self.assertEqual(str(response["group"]["group_id"]), group_id)
            self.assertEqual(response["group"]["metadata"]["title"], "Updated title")
            self.assertEqual(response["report"]["group_id"], group_id)
            self.assertEqual(response["report"]["match"]["title"], "Updated title")

            from app.services.match_group_aggregation import get_coherent_match_group_report

            snapshot = get_coherent_match_group_report(group_id)
            self.assertEqual(snapshot["report"]["match"]["title"], "Updated title")
            self.assertEqual(snapshot["validation"]["status"], "compatible")

            # Member-order replacement follows the replacement manifest lineage.
            reordered = api_update_match_group(
                group_id, self._payload(["published-two", "published-one"], title="Reordered")
            )
            self.assertEqual(
                [row["published_id"] for row in reordered["report"]["sources"]],
                ["published-two", "published-one"],
            )

    def test_real_update_candidate_failure_preserves_old_pair_exactly(self) -> None:
        from test_match_group_aggregation import _metadata, _write_source

        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two")
            group_id = str(api_create_match_group(self._payload(["published-one", "published-two"]))["group"]["group_id"])
            group_dir = root / "groups" / group_id
            before = {
                name: (group_dir / name).read_bytes() for name in ("manifest.json", "public_report.json")
            }

            from app.services.match_groups import MatchGroupError

            with patch(
                "app.main.build_match_group_report_candidate",
                side_effect=MatchGroupError("aggregate_generation_blocked", "candidate exploded"),
            ):
                with self.assertRaises(HTTPException) as failure:
                    api_update_match_group(
                        group_id, self._payload(["published-one", "published-two"], title="Lost update")
                    )

            self.assertEqual(failure.exception.status_code, 409)
            self.assertEqual(
                {name: (group_dir / name).read_bytes() for name in before}, before
            )

            from app.services.match_group_aggregation import get_coherent_match_group_report

            snapshot = get_coherent_match_group_report(group_id)
            self.assertEqual(snapshot["validation"]["status"], "compatible")
            # The maintenance lock was released: a later update succeeds.
            retried = api_update_match_group(
                group_id, self._payload(["published-one", "published-two"], title="Retried")
            )
            self.assertEqual(retried["group"]["metadata"]["title"], "Retried")

    def test_production_callbacks_are_pinned_to_their_contracts(self) -> None:
        from test_match_group_aggregation import _metadata, _write_source

        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two")
            with patch(
                "app.main.create_match_group_and_generate_report", return_value=({}, {})
            ) as create, patch(
                "app.main._group_with_validation", return_value={"group": {}, "validation": {}}
            ):
                api_create_match_group(self._payload(["published-one", "published-two"]))
            self.assertIs(
                create.call_args.kwargs["generate_and_persist_report"],
                main_module.generate_match_group_report,
            )
            group_id = str(api_create_match_group(self._payload(["published-one", "published-two"]))["group"]["group_id"])
            with patch(
                "app.main.update_match_group_and_generate_report", return_value=({}, {})
            ) as update, patch(
                "app.main._group_with_validation", return_value={"group": {}, "validation": {}}
            ):
                api_update_match_group(group_id, self._payload(["published-one", "published-two"]))
            self.assertIs(
                update.call_args.kwargs["build_report_candidate"],
                main_module.build_match_group_report_candidate,
            )

    @staticmethod
    def _payload(members: list[str], title: str = "Logical match") -> MatchGroupPayload:
        from test_match_group_aggregation import _metadata

        return MatchGroupPayload.model_validate({
            "member_published_ids": members,
            "metadata": {**_metadata(), "title": title},
        })

    @staticmethod
    def _physical_bytes(root: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in sorted((root / "published").rglob("*"))
            if path.is_file()
        }

    def _store(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        patches = (
            patch("app.services.match_groups.PUBLISHED_MATCHES_DIR", root / "published"),
            patch("app.services.match_groups.MATCH_GROUPS_DIR", root / "groups"),
            patch("app.services.match_group_aggregation.PUBLISHED_MATCHES_DIR", root / "published"),
            patch("app.services.match_group_aggregation.MATCH_GROUPS_DIR", root / "groups"),
            patch("app.services.match_group_refresh.MATCH_GROUPS_DIR", root / "groups"),
            patch("app.services.match_group_video.PUBLISHED_MATCHES_DIR", root / "published"),
            patch("app.services.match_group_video.MATCH_GROUPS_DIR", root / "groups"),
            patch("app.services.match_group_external_video.MATCH_GROUPS_DIR", root / "groups"),
        )

        class StoreContext:
            def __enter__(self) -> Path:
                for item in patches:
                    item.__enter__()
                return root

            def __exit__(self, *args: object) -> None:
                for item in reversed(patches):
                    item.__exit__(*args)
                temporary.cleanup()

        return StoreContext()


if __name__ == "__main__":
    unittest.main()
