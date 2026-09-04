from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

import app.services.match_group_refresh as match_group_refresh
from app.services.match_group_aggregation import generate_match_group_report
from app.services.match_group_refresh import preview_match_group_refresh, refresh_match_group_to_latest
from app.services.match_group_video import MatchGroupVideoError
from app.services.match_groups import MatchGroupError, create_match_group, get_match_group, update_match_group
from test_match_group_aggregation import _metadata, _write_source


class MatchGroupRefreshTests(unittest.TestCase):
    def test_refresh_advances_same_stable_publications_and_rebuilds_report(self) -> None:
        with self._store() as root:
            group = self._group(root)
            previous = get_match_group(group["group_id"])
            _write_source(root, "published-one", "physical-one", player_distance=333)

            preview = preview_match_group_refresh(group["group_id"])
            self.assertEqual(preview["status"], "refreshable")
            self.assertEqual([row["published_id"] for row in preview["members"]], ["published-one", "published-two"])
            result = refresh_match_group_to_latest(group["group_id"])

            self.assertEqual(result["status"], "refreshed")
            refreshed = get_match_group(group["group_id"])
            self.assertEqual([row["published_id"] for row in refreshed["members"]], ["published-one", "published-two"])
            self.assertEqual([row["source_match_id"] for row in refreshed["members"]], ["physical-one", "physical-two"])
            self.assertNotEqual(
                previous["members"][0]["aggregation_input_semantic_digest"],
                refreshed["members"][0]["aggregation_input_semantic_digest"],
            )
            self.assertEqual(result["validation"]["status"], "compatible")
            self.assertEqual(result["video"]["status"], "unavailable_source_video")
            self.assertEqual(result["external_video"]["status"], "not_configured")

    def test_current_refresh_is_a_byte_preserving_no_op(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_dir = root / "groups" / group["group_id"]
            before = {name: (group_dir / name).read_bytes() for name in ("manifest.json", "public_report.json")}

            self.assertEqual(preview_match_group_refresh(group["group_id"])["status"], "current")
            result = refresh_match_group_to_latest(group["group_id"])

            self.assertEqual(result["status"], "current")
            self.assertEqual(before, {name: (group_dir / name).read_bytes() for name in before})

    def test_refresh_blocks_stable_publication_that_changed_physical_identity(self) -> None:
        with self._store() as root:
            group = self._group(root)
            _write_source(root, "published-one", "different-physical-source")

            preview = preview_match_group_refresh(group["group_id"])

            self.assertEqual(preview["status"], "blocked")
            self.assertEqual(preview["blocking_reasons"][0]["code"], "source_match_identity_changed")

    def test_refresh_blocks_a_tampered_persisted_manifest_without_rewriting_it(self) -> None:
        with self._store() as root:
            group = self._group(root)
            manifest_path = root / "groups" / str(group["group_id"]) / "manifest.json"
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            document["metadata"]["title"] = "tampered"
            manifest_path.write_text(json.dumps(document), encoding="utf-8")

            preview = preview_match_group_refresh(str(group["group_id"]))

            self.assertEqual(preview["status"], "blocked")
            self.assertEqual(preview["blocking_reasons"][0]["code"], "refresh_manifest_invalid")

    def test_refresh_never_commits_a_mixed_source_generation(self) -> None:
        with self._store() as root:
            group = self._group(root)
            _write_source(root, "published-one", "physical-one", player_distance=333)
            original = match_group_refresh._build_refresh_candidate
            calls = 0

            def drift_after_report(current: dict[str, object]) -> dict[str, object]:
                nonlocal calls
                calls += 1
                result = original(current)  # type: ignore[arg-type]
                if calls == 2:
                    _write_source(root, "published-one", "physical-one", player_distance=444)
                return result

            with patch("app.services.match_group_refresh._build_refresh_candidate", side_effect=drift_after_report):
                with self.assertRaises(MatchGroupError) as failure:
                    refresh_match_group_to_latest(group["group_id"])
            self.assertEqual(failure.exception.code, "source_generation_changed_during_refresh")

    def test_refresh_never_overwrites_a_concurrent_manifest_metadata_change(self) -> None:
        with self._store() as root:
            group = self._group(root)
            _write_source(root, "published-one", "physical-one", player_distance=333)
            original = match_group_refresh._build_refresh_candidate
            calls = 0

            def update_after_candidate(current: dict[str, object]) -> dict[str, object]:
                nonlocal calls
                calls += 1
                result = original(current)  # type: ignore[arg-type]
                if calls == 2:
                    update_match_group(
                        str(group["group_id"]),
                        member_published_ids=["published-one", "published-two"],
                        metadata={**_metadata(), "title": "operator changed title"},
                    )
                return result

            with patch("app.services.match_group_refresh._build_refresh_candidate", side_effect=update_after_candidate):
                with self.assertRaises(MatchGroupError) as failure:
                    refresh_match_group_to_latest(str(group["group_id"]))
            self.assertEqual(failure.exception.code, "source_generation_changed_during_refresh")
            self.assertEqual(get_match_group(str(group["group_id"]))["metadata"]["title"], "operator changed title")

    def test_pair_commit_rolls_back_when_second_replace_fails(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_dir = root / "groups" / group["group_id"]
            before = {name: (group_dir / name).read_bytes() for name in ("manifest.json", "public_report.json")}
            real_replace = match_group_refresh.os.replace
            replaces = 0

            def fail_second_replace(source: object, target: object) -> None:
                nonlocal replaces
                replaces += 1
                if replaces == 2:
                    raise OSError("simulated second replace failure")
                real_replace(source, target)

            with patch("app.services.match_group_refresh.os.replace", side_effect=fail_second_replace):
                with self.assertRaises(OSError):
                    match_group_refresh._commit_pair(group["group_id"], get_match_group(group["group_id"]), {"report": "replacement"})
            self.assertEqual(before, {name: (group_dir / name).read_bytes() for name in before})

    def test_refresh_is_rejected_while_video_generation_owns_group(self) -> None:
        with self._store() as root:
            group = self._group(root)
            group_dir = root / "groups" / group["group_id"]
            (group_dir / "video_job.json").write_text('{"status":"generating","job_key":"live"}', encoding="utf-8")
            (group_dir / "video_job.lock").write_text('{"pid":999999,"job_key":"live"}', encoding="utf-8")

            with patch("app.services.match_group_video._lock_owner_alive", return_value=True):
                with self.assertRaises(MatchGroupVideoError) as failure:
                    refresh_match_group_to_latest(group["group_id"])

            self.assertEqual(failure.exception.code, "video_generation_in_progress")

    def test_commit_refuses_missing_group_without_recreating_it(self) -> None:
        with self._store() as root:
            with self.assertRaises(KeyError):
                match_group_refresh._commit_pair("match-group-00000000-0000-0000-0000-000000000000", {}, {})
            self.assertFalse((root / "groups" / "match-group-00000000-0000-0000-0000-000000000000").exists())

    def _group(self, root: Path) -> dict[str, object]:
        _write_source(root, "published-one", "physical-one")
        _write_source(root, "published-two", "physical-two")
        group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
        generate_match_group_report(str(group["group_id"]))
        return group

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
