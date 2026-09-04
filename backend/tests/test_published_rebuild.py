from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None


def write_rebuildable_match_fixture(match_dir: Path, *, match_id: str, title: str) -> None:
    """A publishable local match with two stable teams for rebuild tests."""

    from test_match_package import write_json, write_ready_match_fixture

    write_ready_match_fixture(match_dir)
    write_json(
        match_dir / "match.json",
        {
            "id": match_id,
            "title": title,
            "status": "reviewed",
            "format": "7v7",
            "video_filename": "video.mp4",
            "video": {"fps": 25, "frame_count": 250, "duration_sec": 10, "width": 1280, "height": 720},
            "teams": [
                {
                    "id": "team-a",
                    "name": "Team A",
                    "players": [{"id": "p-a1", "name": "Player A1", "role": "player", "is_guest": False}],
                },
                {
                    "id": "team-b",
                    "name": "Team B",
                    "players": [{"id": "p-b1", "name": "Player B1", "role": "player", "is_guest": False}],
                },
            ],
        },
    )


def write_rebuildable_reviewed_fixture(match_dir: Path) -> None:
    """Reviewed-identity variant so publication includes aggregate inputs."""

    from test_match_package import write_json, write_reviewed_identity_fixture

    for name in ("player_identity_assignments.json", "resolved_player_stats.json"):
        (match_dir / name).unlink(missing_ok=True)
    write_reviewed_identity_fixture(match_dir)
    write_json(
        match_dir / "team_config.json",
        {
            "schema_version": "0.1.0",
            "teams": [
                {"team_label": "A", "team_id": "team-a"},
                {"team_label": "B", "team_id": "team-b"},
            ],
        },
    )
    write_json(
        match_dir / "reviewed_player_stats.json",
        {
            "source_snapshot_digest": "reviewed-digest",
            "players": [
                {
                    "player_id": "p-a1",
                    "player_name": "Player A1",
                    "team_label": "A",
                    "detected_time_sec": 8.0,
                    "total_distance_m": 25.0,
                    "detected_frames": 200,
                },
                {
                    "player_id": "p-b1",
                    "player_name": "Player B1",
                    "team_label": "B",
                    "detected_time_sec": 7.0,
                    "total_distance_m": 20.0,
                    "detected_frames": 175,
                },
            ],
        },
    )


@unittest.skipUnless(FASTAPI_AVAILABLE, "fastapi is required for rebuild endpoint tests")
class PublishedRebuildTests(unittest.TestCase):
    def test_rebuild_updates_publication_preserving_stable_identity(self) -> None:
        from app.main import api_rebuild_published_match, publish_local_match

        with self._store() as root:
            self._local_match(root, "match-1", title="Original title")
            published = publish_local_match("match-1", replace=False)
            self.assertEqual(published["id"], "published-match-1")
            before_summary = (root / "published" / "published-match-1" / "summary.json").read_bytes()
            before_report = (root / "published" / "published-match-1" / "public_report.json").read_bytes()

            self._set_local_title(root, "match-1", "Rebuilt title")
            rebuilt = api_rebuild_published_match("published-match-1")

            self.assertEqual(rebuilt["id"], "published-match-1")
            self.assertEqual(rebuilt["source_match_id"], "match-1")
            self.assertEqual(rebuilt["title"], "Rebuilt title")
            self.assertEqual(rebuilt["public_report"]["match"]["title"], "Rebuilt title")
            self.assertNotEqual(
                (root / "published" / "published-match-1" / "public_report.json").read_bytes(), before_report
            )
            self.assertNotEqual(
                (root / "published" / "published-match-1" / "summary.json").read_bytes(), before_summary
            )
            self.assertEqual(
                [path.name for path in (root / "published").iterdir() if path.is_dir()],
                ["published-match-1"],
            )
            # Publication remains fully readable.
            from app.services.json_publish_store import get_published_match

            fetched = get_published_match("published-match-1")
            self.assertEqual(fetched["title"], "Rebuilt title")

    def test_rebuild_rejects_source_identity_mismatch_without_mutation(self) -> None:
        from fastapi import HTTPException

        from app.main import api_rebuild_published_match, publish_local_match

        with self._store() as root:
            self._local_match(root, "match-1", title="Original")
            publish_local_match("match-1", replace=False)
            before = self._publication_bytes(root, "published-match-1")

            # Local directory now claims a different physical source.
            self._set_local_match_id(root, "match-1", "match-2")
            with self.assertRaises(HTTPException) as failure:
                api_rebuild_published_match("published-match-1")

            self.assertEqual(failure.exception.status_code, 409)
            self.assertEqual(self._publication_bytes(root, "published-match-1"), before)
            self.assertFalse((root / "published" / "published-match-2").exists())

    def test_rebuild_unknown_published_id_creates_nothing(self) -> None:
        from fastapi import HTTPException

        from app.main import api_rebuild_published_match

        with self._store() as root:
            with self.assertRaises(HTTPException) as failure:
                api_rebuild_published_match("published-missing")

            self.assertEqual(failure.exception.status_code, 404)
            self.assertFalse((root / "published" / "published-missing").exists())

    def test_rebuild_missing_local_source_keeps_publication_intact(self) -> None:
        from fastapi import HTTPException

        from app.main import api_rebuild_published_match, publish_local_match

        with self._store() as root:
            self._local_match(root, "match-1", title="Original")
            publish_local_match("match-1", replace=False)
            before = self._publication_bytes(root, "published-match-1")

            import shutil

            shutil.rmtree(root / "matches" / "match-1")
            with self.assertRaises(HTTPException) as failure:
                api_rebuild_published_match("published-match-1")

            self.assertEqual(failure.exception.status_code, 404)
            self.assertEqual(self._publication_bytes(root, "published-match-1"), before)

    def test_rebuild_unpublishable_source_keeps_old_snapshot_usable(self) -> None:
        from fastapi import HTTPException

        from app.main import api_rebuild_published_match, publish_local_match

        with self._store() as root:
            self._local_match(root, "match-1", title="Original")
            publish_local_match("match-1", replace=False)
            before = self._publication_bytes(root, "published-match-1")

            (root / "matches" / "match-1" / "resolved_player_stats.json").unlink()
            with self.assertRaises(HTTPException) as failure:
                api_rebuild_published_match("published-match-1")

            self.assertEqual(failure.exception.status_code, 409)
            self.assertEqual(self._publication_bytes(root, "published-match-1"), before)

    def test_rebuild_enforces_publish_workflow_before_mutation(self) -> None:
        from fastapi import HTTPException

        from app.main import api_rebuild_published_match, publish_local_match

        with self._store() as root:
            self._local_match(root, "match-1", title="Original")
            publish_local_match("match-1", replace=False)
            before = self._publication_bytes(root, "published-match-1")

            with patch(
                "app.main._assert_publish_workflow",
                side_effect=HTTPException(status_code=409, detail="review_not_completed"),
            ):
                with self.assertRaises(HTTPException) as failure:
                    api_rebuild_published_match("published-match-1")

            self.assertEqual(failure.exception.status_code, 409)
            self.assertEqual(self._publication_bytes(root, "published-match-1"), before)

    def test_rebuild_invokes_no_analysis_review_or_logical_side_effects(self) -> None:
        from app.main import api_rebuild_published_match, publish_local_match

        with self._store() as root:
            self._local_match(root, "match-1", title="Original")
            publish_local_match("match-1", replace=False)
            with (
                patch("app.main.run_match_analysis_and_update_meta") as analyze,
                patch(
                    "app.services.identity_reviewed_snapshot.finalize_reviewed_identity"
                ) as finalize_identity,
                patch(
                    "app.services.match_group_refresh.refresh_match_group_to_latest"
                ) as logical_refresh,
                patch(
                    "app.services.match_group_video.generate_match_group_video"
                ) as combined_video,
            ):
                rebuilt = api_rebuild_published_match("published-match-1")

            self.assertEqual(rebuilt["id"], "published-match-1")
            analyze.assert_not_called()
            finalize_identity.assert_not_called()
            logical_refresh.assert_not_called()
            combined_video.assert_not_called()

    def test_rebuild_makes_logical_group_refreshable_without_touching_it(self) -> None:
        from app.main import api_rebuild_published_match, publish_local_match
        from app.services.match_group_aggregation import generate_match_group_report
        from app.services.match_group_refresh import preview_match_group_refresh
        from app.services.match_groups import create_match_group, get_match_group

        with self._store() as root:
            self._local_match(root, "match-a", title="Half one", reviewed=True)
            self._local_match(root, "match-b", title="Half two", reviewed=True)
            publish_local_match("match-a", replace=False)
            publish_local_match("match-b", replace=False)

            group = create_match_group(
                member_published_ids=["published-match-a", "published-match-b"],
                metadata={"title": "Full match"},
            )
            group_id = str(group["group_id"])
            generate_match_group_report(group_id)
            self.assertEqual(preview_match_group_refresh(group_id)["status"], "current")
            group_manifest_before = (root / "groups" / group_id / "manifest.json").read_bytes()
            group_report_before = (root / "groups" / group_id / "public_report.json").read_bytes()

            # A meaningful local change, republished through the new endpoint.
            self._set_player_distance(root, "match-a", "p-a1", 99.0)
            rebuilt = api_rebuild_published_match("published-match-a")
            self.assertEqual(rebuilt["id"], "published-match-a")

            # The logical group was not refreshed automatically.
            self.assertEqual((root / "groups" / group_id / "manifest.json").read_bytes(), group_manifest_before)
            self.assertEqual((root / "groups" / group_id / "public_report.json").read_bytes(), group_report_before)
            self.assertEqual(get_match_group(group_id)["group_id"], group_id)

            preview = preview_match_group_refresh(group_id)
            self.assertEqual(preview["status"], "refreshable")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _local_match(self, root: Path, match_id: str, *, title: str, reviewed: bool = False) -> Path:
        match_dir = root / "matches" / match_id
        match_dir.mkdir(parents=True, exist_ok=True)
        write_rebuildable_match_fixture(match_dir, match_id=match_id, title=title)
        if reviewed:
            write_rebuildable_reviewed_fixture(match_dir)
        return match_dir

    @staticmethod
    def _set_local_title(root: Path, match_id: str, title: str) -> None:
        meta_path = root / "matches" / match_id / "match.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["title"] = title
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    @staticmethod
    def _set_local_match_id(root: Path, match_id: str, new_id: str) -> None:
        meta_path = root / "matches" / match_id / "match.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["id"] = new_id
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    @staticmethod
    def _set_player_distance(root: Path, match_id: str, player_id: str, distance: float) -> None:
        stats_path = root / "matches" / match_id / "reviewed_player_stats.json"
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        for player in stats["players"]:
            if player["player_id"] == player_id:
                player["total_distance_m"] = distance
        stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")

    @staticmethod
    def _publication_bytes(root: Path, published_id: str) -> dict[str, bytes]:
        directory = root / "published" / published_id
        return {
            str(path.relative_to(directory)): path.read_bytes()
            for path in sorted(directory.rglob("*"))
            if path.is_file()
        }

    def _store(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "matches").mkdir(parents=True, exist_ok=True)
        patches = (
            patch("app.main.MATCHES_DIR", root / "matches"),
            patch("app.main._assert_publish_workflow", return_value=None),
            patch("app.services.json_publish_store.MATCHES_DIR", root / "matches"),
            patch("app.services.json_publish_store.PUBLISHED_MATCHES_DIR", root / "published"),
            patch("app.services.public_match_report.CLIENT_PUBLIC_MATCHES_DIR", root / "mirror"),
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
