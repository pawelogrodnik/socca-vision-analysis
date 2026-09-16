from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import config
from app.services.analysis import load_pitch_config
from app.services.ball_downstream_rebuild import (
    GENERATION_FILENAME,
    GROUP_PUBLICATION_STATE_FILENAME,
    _mark_physical_publication_current,
    acknowledge_ball_downstream_physical_publication,
    ball_downstream_status,
    rebuild_ball_downstream_analytics,
    rebuild_ball_downstream_for_match,
)
from app.services.ball_possession import build_ball_possession_analysis
from app.services.effective_ball_tracks import EffectiveBallTracksError, effective_ball_track_digest, load_effective_ball_tracks
from app.services.resolved_ball_tracks import RESOLUTION_SCHEMA_VERSION, RESOLVED_BALL_TRACKS_FILENAME


def _tracks(candidate_id: str, *, x: float = 5.0) -> dict:
    return {
        "schema_version": "0.1.0",
        "generated_at": "2026-09-15T00:00:00Z",
        "positions": [
            {
                "frame": 1,
                "time_sec": 1 / 30,
                "candidate_id": candidate_id,
                "position_m": [x, 8.0],
                "position_px": [x * 10, 80.0],
                "source": "detected",
                "confidence": 0.7,
            }
        ],
        "interpolation_gaps": [],
    }


def _write(path: Path, name: str, document: dict) -> None:
    (path / name).write_text(json.dumps(document), encoding="utf-8")


def _match_fixture(root: Path, match_id: str = "source-one") -> Path:
    match_path = root / match_id
    match_path.mkdir()
    _write(match_path, "match.json", {"id": match_id, "video": {"fps": 30.0, "width": 1920, "height": 1080, "duration_sec": 1.0}})
    _write(match_path, "pitch_config.json", {"image_points": [[0, 0], [100, 0], [100, 100], [0, 100]], "width_m": 30.0, "length_m": 47.4})
    # Public stable players deliberately keep only a sparse report trajectory.
    # The production possession input remains private global-identity overlay
    # rows, matching the real stabilization lifecycle.
    stable_player = {
        "stable_player_id": "A01", "team_label": "A", "team_id": "team-a", "team_name": "A",
        "stable_subject_id": "slot-a01",
        "trajectory_m": [{"frame": 1, "time_sec": 1 / 30, "pitch_m": [1.0, 1.0], "source": "detected", "status": "detected"}],
    }
    private_slot = {
        **stable_player,
        "overlay_positions": [{"frame": 1, "time_sec": 1 / 30, "pitch_m": [5.2, 8.0], "source": "detected", "status": "detected"}],
    }
    _write(match_path, "stable_players.json", {"players": [stable_player]})
    _write(match_path, "global_identity.json", {"slots": [private_slot]})
    _write(match_path, "ball_tracks.json", _tracks("automatic", x=5.0))
    return match_path


class EffectiveBallTracksTests(unittest.TestCase):
    def test_resolved_projection_is_preferred_and_generated_at_is_not_semantic(self) -> None:
        with TemporaryDirectory() as temporary:
            match_path = _match_fixture(Path(temporary))
            resolved = _tracks("operator-selected", x=7.0)
            resolved["resolution"] = {"schema_version": RESOLUTION_SCHEMA_VERSION}
            _write(match_path, RESOLVED_BALL_TRACKS_FILENAME, resolved)

            effective = load_effective_ball_tracks(match_path)
            regenerated = dict(resolved)
            regenerated["generated_at"] = "2026-09-15T01:00:00Z"

            self.assertEqual(effective.provenance, "resolved_operator_projection")
            self.assertEqual(effective.document["positions"][0]["candidate_id"], "operator-selected")
            self.assertEqual(effective.input_digest, effective_ball_track_digest(regenerated))

    def test_absent_resolved_projection_uses_automatic_but_malformed_projection_fails_loudly(self) -> None:
        with TemporaryDirectory() as temporary:
            match_path = _match_fixture(Path(temporary))
            self.assertEqual(load_effective_ball_tracks(match_path).provenance, "automatic_legacy_fallback")
            _write(match_path, RESOLVED_BALL_TRACKS_FILENAME, {"positions": []})
            with self.assertRaises(EffectiveBallTracksError):
                load_effective_ball_tracks(match_path)


class BallDownstreamRebuildTests(unittest.TestCase):
    def test_explicit_rebuild_uses_resolved_input_and_records_current_digest(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            match_path = _match_fixture(root)
            resolved = _tracks("operator-selected", x=7.0)
            resolved["resolution"] = {"schema_version": RESOLUTION_SCHEMA_VERSION}
            _write(match_path, RESOLVED_BALL_TRACKS_FILENAME, resolved)

            result = rebuild_ball_downstream_for_match(match_path)
            marker = json.loads((match_path / GENERATION_FILENAME).read_text(encoding="utf-8"))
            possession = json.loads((match_path / "possession_candidates.json").read_text(encoding="utf-8"))

            self.assertEqual(result["provenance"], "resolved_operator_projection")
            self.assertEqual(marker["ball_track_input_digest"], effective_ball_track_digest(resolved))
            self.assertEqual(possession["frames"][0]["ball_position_m"], [7.0, 8.0])
            for filename in (
                "possession_segments.json", "contact_candidates.json", "event_candidates.json", "event_review_report.json",
                "restart_candidates.json", "pass_candidates.json", "pass_review_report.json", "attacking_momentum.json",
                "analytics_readiness.json", "possession_report.json",
            ):
                self.assertTrue((match_path / filename).exists(), filename)

    def test_explicit_rebuild_uses_private_dense_player_timeline_not_public_trajectory(self) -> None:
        with TemporaryDirectory() as temporary:
            match_path = _match_fixture(Path(temporary))
            private = json.loads((match_path / "global_identity.json").read_text(encoding="utf-8"))
            with patch(
                "app.services.ball_downstream_rebuild.build_ball_possession_analysis",
                wraps=build_ball_possession_analysis,
            ) as build:
                rebuild_ball_downstream_for_match(match_path)

            production_players = build.call_args.args[5]
            self.assertEqual(production_players["players"][0]["overlay_positions"][0]["pitch_m"], [5.2, 8.0])
            self.assertEqual(production_players["players"][0]["trajectory_m"][0]["pitch_m"], [1.0, 1.0])
            self.assertEqual(production_players["players"][0]["overlay_positions"], private["slots"][0]["overlay_positions"])

            # A normal pipeline receives that same private stabilization row,
            # so unchanged ball evidence cannot alter possession globally just
            # because the explicit path chose a lower-resolution player source.
            normal = build_ball_possession_analysis(
                match_path,
                match_path / "video.mp4",
                load_pitch_config(match_path),
                json.loads((match_path / "match.json").read_text(encoding="utf-8"))["video"],
                _tracks("automatic"),
                {"players": private["slots"]},
                write_overlay_video=False,
                persist_artifacts=False,
            )
            explicit = json.loads((match_path / "possession_candidates.json").read_text(encoding="utf-8"))
            self.assertEqual(explicit["frames"], normal["possession_candidates"]["frames"])

    def test_changed_or_removed_resolved_projection_is_stale_without_mutating_automatic_tracks(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            match_path = _match_fixture(root)
            resolved = _tracks("operator-selected", x=7.0)
            resolved["resolution"] = {"schema_version": RESOLUTION_SCHEMA_VERSION}
            _write(match_path, RESOLVED_BALL_TRACKS_FILENAME, resolved)
            rebuild_ball_downstream_for_match(match_path)
            _mark_physical_publication_current(match_path, "published-source-one")
            automatic_before = (match_path / "ball_tracks.json").read_bytes()

            with patch.object(config, "MATCHES_DIR", root), patch(
                "app.services.ball_downstream_rebuild.get_published_match",
                return_value={"id": "published-source-one", "source_match_id": "source-one"},
            ):
                self.assertEqual(ball_downstream_status("published-source-one")["status"], "current")
                (match_path / RESOLVED_BALL_TRACKS_FILENAME).unlink()
                self.assertEqual(ball_downstream_status("published-source-one")["status"], "stale")

            self.assertEqual((match_path / "ball_tracks.json").read_bytes(), automatic_before)

    def test_changed_player_timeline_is_stale_even_when_ball_digest_is_unchanged(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            match_path = _match_fixture(root)
            rebuild_ball_downstream_for_match(match_path)
            _mark_physical_publication_current(match_path, "published-source-one")
            published = {"id": "published-source-one", "source_match_id": "source-one"}
            with patch.object(config, "MATCHES_DIR", root), patch(
                "app.services.ball_downstream_rebuild.get_published_match", return_value=published,
            ):
                self.assertEqual(ball_downstream_status("published-source-one")["status"], "current")
                identity = json.loads((match_path / "global_identity.json").read_text(encoding="utf-8"))
                identity["slots"][0]["overlay_positions"][0]["pitch_m"] = [6.2, 8.0]
                _write(match_path, "global_identity.json", identity)
                status = ball_downstream_status("published-source-one")["sources"][0]

            self.assertFalse(status["analytics_generation_current"])
            self.assertEqual(status["current_effective_ball_track_digest"], status["ball_track_input_digest"])
            self.assertNotEqual(status["player_event_timeline_digest"], status["recorded_player_event_timeline_digest"])

    def test_legacy_marker_without_player_digest_is_not_current(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            match_path = _match_fixture(root)
            _write(match_path, GENERATION_FILENAME, {
                "ball_track_input_digest": effective_ball_track_digest(_tracks("automatic")),
                "analytics_generation": {"status": "current"},
                "physical_publication": {"status": "current", "published_id": "published-source-one"},
            })
            with patch.object(config, "MATCHES_DIR", root), patch(
                "app.services.ball_downstream_rebuild.get_published_match",
                return_value={"id": "published-source-one", "source_match_id": "source-one"},
            ):
                self.assertEqual(ball_downstream_status("published-source-one")["status"], "stale")

    def test_normal_marker_records_both_inputs_and_normal_publish_acknowledges_it(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            match_path = _match_fixture(root)
            private_players = json.loads((match_path / "global_identity.json").read_text(encoding="utf-8"))["slots"]
            build_ball_possession_analysis(
                match_path,
                match_path / "video.mp4",
                load_pitch_config(match_path),
                json.loads((match_path / "match.json").read_text(encoding="utf-8"))["video"],
                _tracks("automatic"),
                {"players": private_players},
                write_overlay_video=False,
            )
            marker = json.loads((match_path / GENERATION_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(marker["analytics_generation"]["status"], "current")
            self.assertIn("ball_track_input_digest", marker)
            self.assertIn("player_event_timeline_digest", marker)
            self.assertFalse(marker["physical_publication"]["status"] == "current")

            with patch.object(config, "MATCHES_DIR", root), patch(
                "app.services.ball_downstream_rebuild.get_published_match",
                return_value={"id": "published-source-one", "source_match_id": "source-one"},
            ):
                self.assertTrue(acknowledge_ball_downstream_physical_publication("source-one", "published-source-one"))
                self.assertEqual(ball_downstream_status("published-source-one")["status"], "current")

    def test_current_endpoint_is_a_noop_and_does_not_rebuild_or_publish(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            match_path = _match_fixture(root)
            rebuild_ball_downstream_for_match(match_path)
            _mark_physical_publication_current(match_path, "published-source-one")
            with patch.object(config, "MATCHES_DIR", root), patch(
                "app.services.ball_downstream_rebuild.get_published_match",
                return_value={"id": "published-source-one", "source_match_id": "source-one"},
            ), patch("app.services.ball_downstream_rebuild.import_match_package") as publish:
                result = rebuild_ball_downstream_analytics("published-source-one", package_builder=lambda _: self.fail("package builder must not run"))

            self.assertEqual(result["result"], "already_current")
            self.assertEqual(result["rebuilt_sources"], [])
            publish.assert_not_called()

    def test_merged_rebuild_visits_each_stale_physical_source_once_then_refreshes_once(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = _match_fixture(root, "one")
            second = _match_fixture(root, "two")
            groups_root = root / "groups"
            (groups_root / "group-one").mkdir(parents=True)
            merged_id = "published-merged-00000000-0000-4000-8000-000000000001"
            packages: list[str] = []
            with patch.object(config, "MATCHES_DIR", root), patch(
                "app.services.ball_downstream_rebuild.MATCH_GROUPS_DIR", groups_root,
            ), patch(
                "app.services.ball_downstream_rebuild.get_published_match",
                return_value={"id": merged_id, "source_kind": "merged"},
            ), patch(
                "app.services.ball_downstream_rebuild.group_id_for_merged_published_id",
                return_value="group-one",
            ), patch(
                "app.services.ball_downstream_rebuild.get_match_group",
                return_value={"members": [
                    {"published_id": "published-one", "source_match_id": "one"},
                    {"published_id": "published-two", "source_match_id": "two"},
                ]},
            ), patch(
                "app.services.ball_downstream_rebuild.list_match_groups",
                return_value=[{"group_id": "group-one", "members": [
                    {"published_id": "published-one", "source_match_id": "one"},
                    {"published_id": "published-two", "source_match_id": "two"},
                ]}],
            ), patch(
                "app.services.ball_downstream_rebuild.import_match_package",
                side_effect=lambda package, replace: {"id": package["published_id"]},
            ), patch("app.services.ball_downstream_rebuild.refresh_merged_match_to_latest") as refresh:
                def build_package(path: Path) -> dict:
                    packages.append(path.name)
                    return {"published_id": f"published-{path.name}"}

                result = rebuild_ball_downstream_analytics(merged_id, package_builder=build_package)

            self.assertEqual(packages, [first.name, second.name])
            refresh.assert_called_once_with("group-one")
            self.assertEqual(result["result"], "rebuilt")
            self.assertEqual([row["source_match_id"] for row in result["rebuilt_sources"]], ["one", "two"])

    def test_failed_physical_publish_resumes_without_rebuilding_valid_analytics(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _match_fixture(root)
            published = {"id": "published-source-one", "source_match_id": "source-one"}
            with patch.object(config, "MATCHES_DIR", root), patch(
                "app.services.ball_downstream_rebuild.get_published_match", return_value=published,
            ), patch(
                "app.services.ball_downstream_rebuild.import_match_package", side_effect=OSError("publish unavailable"),
            ):
                with self.assertRaisesRegex(Exception, "publish unavailable"):
                    rebuild_ball_downstream_analytics("published-source-one", package_builder=lambda _: {"published_id": "published-source-one"})
                status = ball_downstream_status("published-source-one")["sources"][0]
                self.assertTrue(status["analytics_generation_current"])
                self.assertFalse(status["physical_publication_current"])

            with patch.object(config, "MATCHES_DIR", root), patch(
                "app.services.ball_downstream_rebuild.get_published_match", return_value=published,
            ), patch(
                "app.services.ball_downstream_rebuild.rebuild_ball_downstream_for_match",
                wraps=rebuild_ball_downstream_for_match,
            ) as rebuild, patch(
                "app.services.ball_downstream_rebuild.import_match_package", return_value=published,
            ):
                result = rebuild_ball_downstream_analytics("published-source-one", package_builder=lambda _: {"published_id": "published-source-one"})

            self.assertEqual(rebuild.call_count, 0)
            self.assertEqual(result["status"], "current")

    def test_physical_request_refreshes_only_dependent_merged_group_and_resumes_failed_refresh(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            groups_root = root / "groups"
            for group_id in ("group-main", "group-unrelated"):
                (groups_root / group_id).mkdir(parents=True)
            _match_fixture(root)
            members = [{"published_id": "published-source-one", "source_match_id": "source-one"}]
            groups = [
                {"group_id": "group-main", "members": members},
                {"group_id": "group-unrelated", "members": [{"published_id": "published-other", "source_match_id": "other"}]},
            ]
            published = {"id": "published-source-one", "source_match_id": "source-one"}
            with patch.object(config, "MATCHES_DIR", root), patch(
                "app.services.ball_downstream_rebuild.MATCH_GROUPS_DIR", groups_root,
            ), patch(
                "app.services.ball_downstream_rebuild.get_published_match", return_value=published,
            ), patch(
                "app.services.ball_downstream_rebuild.list_match_groups", return_value=groups,
            ), patch(
                "app.services.ball_downstream_rebuild.get_match_group", return_value={"members": members},
            ), patch(
                "app.services.ball_downstream_rebuild.import_match_package", return_value=published,
            ), patch(
                "app.services.ball_downstream_rebuild.refresh_merged_match_to_latest", side_effect=OSError("merged unavailable"),
            ):
                with self.assertRaisesRegex(Exception, "merged unavailable"):
                    rebuild_ball_downstream_analytics("published-source-one", package_builder=lambda _: {"published_id": "published-source-one"})
                self.assertEqual(ball_downstream_status("published-source-one")["status"], "stale")

            with patch.object(config, "MATCHES_DIR", root), patch(
                "app.services.ball_downstream_rebuild.MATCH_GROUPS_DIR", groups_root,
            ), patch(
                "app.services.ball_downstream_rebuild.get_published_match", return_value=published,
            ), patch(
                "app.services.ball_downstream_rebuild.list_match_groups", return_value=groups,
            ), patch(
                "app.services.ball_downstream_rebuild.get_match_group", return_value={"members": members},
            ), patch(
                "app.services.ball_downstream_rebuild.import_match_package") as publish, patch(
                "app.services.ball_downstream_rebuild.refresh_merged_match_to_latest",
            ) as refresh:
                result = rebuild_ball_downstream_analytics("published-source-one", package_builder=lambda _: self.fail("publish must already be current"))

            publish.assert_not_called()
            refresh.assert_called_once_with("group-main")
            self.assertEqual(result["status"], "current")

    def test_physical_rebuild_snapshots_every_merged_member_and_digest_state_drives_retry(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            groups_root = root / "groups"
            (groups_root / "group-main").mkdir(parents=True)
            first = _match_fixture(root, "one")
            second = _match_fixture(root, "two")
            third = _match_fixture(root, "three")
            members = [
                {"published_id": "published-one", "source_match_id": "one"},
                {"published_id": "published-two", "source_match_id": "two"},
                {"published_id": "published-three", "source_match_id": "three"},
            ]
            groups = [{"group_id": "group-main", "members": members}, {"group_id": "group-unrelated", "members": []}]
            # B and C already have current downstream analytics and physical
            # publications.  Only A is initiated through the physical page.
            for match_path, published_id in ((second, "published-two"), (third, "published-three")):
                rebuild_ball_downstream_for_match(match_path)
                _mark_physical_publication_current(match_path, published_id)
            published = {"id": "published-one", "source_match_id": "one"}
            with patch.object(config, "MATCHES_DIR", root), patch(
                "app.services.ball_downstream_rebuild.MATCH_GROUPS_DIR", groups_root,
            ), patch(
                "app.services.ball_downstream_rebuild.get_published_match", return_value=published,
            ), patch(
                "app.services.ball_downstream_rebuild.list_match_groups", return_value=groups,
            ), patch(
                "app.services.ball_downstream_rebuild.get_match_group", return_value={"members": members},
            ), patch(
                "app.services.ball_downstream_rebuild.import_match_package", return_value=published,
            ), patch("app.services.ball_downstream_rebuild.refresh_merged_match_to_latest") as refresh:
                result = rebuild_ball_downstream_analytics("published-one", package_builder=lambda _: {"published_id": "published-one"})
                state = json.loads((groups_root / "group-main" / GROUP_PUBLICATION_STATE_FILENAME).read_text(encoding="utf-8"))
                self.assertEqual(set(state["source_digests"]), {"published-one", "published-two", "published-three"})
                self.assertEqual(result["status"], "current")
                refresh.assert_called_once_with("group-main")

                # A stale success flag is not enough: a changed stored digest
                # requires a merged-only retry, without physical recomputation.
                state["source_digests"]["published-one"] = "sha256:outdated"
                _write(groups_root / "group-main", GROUP_PUBLICATION_STATE_FILENAME, state)
                with patch(
                    "app.services.ball_downstream_rebuild.rebuild_ball_downstream_for_match",
                ) as rebuild, patch(
                    "app.services.ball_downstream_rebuild.import_match_package",
                ) as publish, patch("app.services.ball_downstream_rebuild.refresh_merged_match_to_latest") as retry_refresh:
                    retry = rebuild_ball_downstream_analytics("published-one", package_builder=lambda _: self.fail("no source publish expected"))
                rebuild.assert_not_called()
                publish.assert_not_called()
                retry_refresh.assert_called_once_with("group-main")
                self.assertEqual(retry["status"], "current")

                # A later B digest and a membership addition both invalidate a
                # group flagged current until it is refreshed with the full set.
                _write(second, "ball_tracks.json", _tracks("automatic", x=8.0))
                self.assertEqual(ball_downstream_status("published-one")["status"], "stale")
                members.append({"published_id": "published-four", "source_match_id": "four"})
                _match_fixture(root, "four")
                self.assertEqual(ball_downstream_status("published-one")["status"], "stale")
