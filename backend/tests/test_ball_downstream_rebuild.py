from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import config
from app.services.ball_downstream_rebuild import (
    GENERATION_FILENAME,
    ball_downstream_status,
    rebuild_ball_downstream_analytics,
    rebuild_ball_downstream_for_match,
)
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
    _write(match_path, "stable_players.json", {"players": [{
        "stable_player_id": "A01", "team_label": "A", "team_id": "team-a", "team_name": "A",
        "trajectory_m": [{"frame": 1, "time_sec": 1 / 30, "pitch_m": [5.2, 8.0], "source": "detected", "status": "detected"}],
    }]})
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

    def test_changed_or_removed_resolved_projection_is_stale_without_mutating_automatic_tracks(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            match_path = _match_fixture(root)
            resolved = _tracks("operator-selected", x=7.0)
            resolved["resolution"] = {"schema_version": RESOLUTION_SCHEMA_VERSION}
            _write(match_path, RESOLVED_BALL_TRACKS_FILENAME, resolved)
            rebuild_ball_downstream_for_match(match_path)
            automatic_before = (match_path / "ball_tracks.json").read_bytes()

            with patch.object(config, "MATCHES_DIR", root), patch(
                "app.services.ball_downstream_rebuild.get_published_match",
                return_value={"id": "published-source-one", "source_match_id": "source-one"},
            ):
                self.assertEqual(ball_downstream_status("published-source-one")["status"], "current")
                (match_path / RESOLVED_BALL_TRACKS_FILENAME).unlink()
                self.assertEqual(ball_downstream_status("published-source-one")["status"], "stale")

            self.assertEqual((match_path / "ball_tracks.json").read_bytes(), automatic_before)

    def test_current_endpoint_is_a_noop_and_does_not_rebuild_or_publish(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            match_path = _match_fixture(root)
            rebuild_ball_downstream_for_match(match_path)
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
            merged_id = "published-merged-00000000-0000-4000-8000-000000000001"
            packages: list[str] = []
            with patch.object(config, "MATCHES_DIR", root), patch(
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
