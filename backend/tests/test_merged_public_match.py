from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.artifact_lineage import canonical_json_sha256
from app.services.json_publish_store import (
    get_published_match as store_get_published_match,
)
from app.services.json_publish_store import (
    list_eligible_match_group_sources as store_list_eligible_sources,
)
from app.services.json_publish_store import (
    list_published_matches as store_list_published_matches,
)
from app.services.match_group_aggregation import generate_match_group_report
from app.services.match_groups import MatchGroupError, create_match_group
from app.services.merged_public_match import (
    ensure_merged_published_match,
    group_id_for_merged_published_id,
    is_merged_published_id,
    merged_published_id_for_group,
)
from app.services.public_match_report import (
    PUBLIC_MATCH_REPORT_SCHEMA_VERSION,
    PUBLIC_MATCH_REPORT_TYPE,
)


class MergedPublicMatchTests(unittest.TestCase):
    def test_merged_match_is_canonical_published_match_with_summed_semantics(self) -> None:
        with self._store() as root:
            # Adversarial values: movement_time != detected_time, contested /
            # free / unknown != 0, swapped local A/B with real local momentum
            # signs, sub-minute logical boundary (595s), shared calibration.
            _write_source(root, "published-one", "physical-one", duration=595, team_distance=1000, peak=20,
                          player_distance=120, movement_time=100, detected_time=80, attempts=20, completed=16,
                          controlled_corgi=40, controlled_verisk=30, contested=10, free=10, unknown=10,
                          momentum_local_a=0.8, momentum_local_b=-0.2, momentum_dominant_local="A",
                          team_shape_eligible=200, team_shape_width=20.0, team_shape_cells=(0.6, 0.4))
            _write_source(root, "published-two", "physical-two", duration=300, labels=("B", "A"), team_distance=500, peak=30,
                          player_distance=180, movement_time=150, detected_time=150, attempts=10, completed=8,
                          controlled_corgi=80, controlled_verisk=60, contested=20, free=20, unknown=20,
                          momentum_local_a=0.6, momentum_local_b=-0.3, momentum_dominant_local="A",
                          team_shape_eligible=500, team_shape_width=30.0, team_shape_cells=(0.2, 0.8))
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

            result = ensure_merged_published_match(str(group["group_id"]))
            merged_id = result["merged_published_match_id"]

            self.assertTrue(is_merged_published_id(merged_id))
            self.assertEqual(merged_published_id_for_group(str(group["group_id"])), merged_id)

            stored = store_get_published_match(merged_id)
            self.assertEqual(stored["source_kind"], "merged")
            self.assertEqual(stored["backing_group_id"], str(group["group_id"]))
            self.assertFalse(stored["capabilities"]["rebuild_physical_publication"])
            self.assertTrue(stored["capabilities"]["regenerate_report"])
            self.assertTrue(stored["capabilities"]["refresh_to_latest"])

            report = stored["public_report"]
            self.assertEqual(report["report_type"], "public_match_report")
            self.assertEqual(report["schema_version"], PUBLIC_MATCH_REPORT_SCHEMA_VERSION)
            self.assertEqual(report["id"], merged_id)
            # Canonical match metadata: summed duration.
            self.assertEqual(report["match"]["duration_sec"], 895.0)
            self.assertEqual(report["match"]["title"], "Logical match")

            # Team aggregation: SUM distance, MAX peak, recomputed possession.
            corgi = next(row for row in report["teams"] if row["team_id"] == "team-corgi")
            self.assertEqual(corgi["total_distance_m"], 1500.0)
            self.assertEqual(corgi["peak_speed_kmh"], 30.0)
            # Controlled corgi 40+80=120 of 210 → 57.1%.
            self.assertAlmostEqual(corgi["possession_share_percent"], 57.1, places=1)
            self.assertEqual(report["teams"][0]["team_label"], "A")

            # Pass counts SUM, completion rate RECOMPUTED (not averaged).
            self.assertEqual(report["ball"]["pass_attempts"], 30)
            self.assertEqual(report["ball"]["completed_passes"], 24)
            self.assertEqual(report["ball"]["completion_rate"], 80.0)

            # Coverage semantics: controlled != known when free/contested exist.
            # controlled 210/300 = 0.70, known (210+30+30)/300 = 0.90.
            self.assertEqual(report["ball"]["controlled_coverage"], 0.70)
            self.assertEqual(report["ball"]["known_possession_coverage"], 0.90)
            self.assertNotEqual(report["ball"]["controlled_coverage"], report["ball"]["known_possession_coverage"])

            # One player row per stable player, times SUMMED.
            players = [row for row in report["players"] if row["player_id"] == "player-one"]
            self.assertEqual(len(players), 1)
            player = players[0]
            self.assertEqual(player["playing_time_sec"], 895.0)
            self.assertEqual(player["total_distance_m"], 300.0)
            self.assertEqual(player["peak_speed_kmh"], 30.0)
            # avg speed from movement_time primitive: 300m / 250s * 3.6 = 4.32,
            # NOT 300/230*3.6 = 4.70 from detected time.
            self.assertAlmostEqual(player["avg_speed_kmh"], 4.32, places=2)
            self.assertEqual(player["sprint_count"], 2)
            self.assertIn("player-one", [row["player_id"] for row in report["players"]])

            # Canonical possession timeline: rebased 0..595 + 595..895.
            timeline = report["ball"]["possession_timeline"]
            self.assertEqual([(row["start_time_sec"], row["end_time_sec"]) for row in timeline], [(0.0, 595.0), (595.0, 895.0)])
            self.assertEqual(timeline[-1]["cumulative_team_a_frames"], 120)
            self.assertEqual(timeline[-1]["cumulative_team_b_frames"], 90)

            # Canonical momentum: A >= 0, B <= 0 even for the swapped source.
            momentum = report["ball"]["attacking_momentum"]
            self.assertTrue(momentum["experimental"])
            self.assertEqual(len(momentum["timeline"]), 2)
            first, second = momentum["timeline"]
            self.assertGreater(first["team_a_value"], 0)
            self.assertLess(first["team_b_value"], 0)
            self.assertAlmostEqual(first["signed_score"], first["team_a_value"] + first["team_b_value"], places=3)
            self.assertEqual(first["dominant_team_label"], "A")
            self.assertGreater(second["team_a_value"], 0)
            self.assertLess(second["team_b_value"], 0)
            self.assertAlmostEqual(second["signed_score"], second["team_a_value"] + second["team_b_value"], places=3)
            self.assertEqual(second["dominant_team_label"], "B")
            self.assertEqual(second["start_time_sec"], 595.0)

            # Heatmaps use merged samples through the canonical player field.
            self.assertTrue(player["heatmap"]["path"].startswith(f"published/matches/{merged_id}/heatmaps/"))
            self.assertEqual(player["heatmap"]["samples"], 8)
            self.assertIn("average_position", player["heatmap"])
            self.assertIsNotNone(player["heatmap"]["average_position"])
            self.assertEqual(player["heatmap"]["average_position"]["pitch_m"], [6.5, 11.5])
            heatmap_file = root / "published" / merged_id / "heatmaps" / Path(player["heatmap"]["path"]).name
            self.assertTrue(heatmap_file.is_file())
            mirror_file = root / "client-public" / merged_id / "heatmaps" / Path(player["heatmap"]["path"]).name
            self.assertTrue(mirror_file.is_file())

            # Team Shape uses eligible-frame evidence weighting, not duration:
            # (200*20 + 500*30)/700 = 27.14, not (595*20 + 300*30)/895 = 23.35.
            shape = report["team_shape"]
            corgi_shape = next(row for row in shape["teams"] if row["team_id"] == "team-corgi")
            self.assertAlmostEqual(corgi_shape["summary"]["average_width_m"], 27.14, places=2)
            # Density grids use the same evidence weights: (200*0.6 + 500*0.2)/700.
            merged_cells = {cell["column"]: cell["value"] for cell in corgi_shape["average_shape"]["cells"]}
            self.assertAlmostEqual(merged_cells[0], 0.314286, places=5)
            self.assertAlmostEqual(merged_cells[1], 0.685714, places=5)
            # Sub-minute boundary: fragment two starts at logical 595s = 09:55.
            second_bin = next(point for point in corgi_shape["timeline"] if point["label"] == "09:55")
            self.assertEqual(second_bin["minute"], 10)

            # Provenance is internal; the report still looks like a normal one.
            self.assertEqual(report["merged_provenance"]["group_id"], str(group["group_id"]))
            self.assertEqual(group_id_for_merged_published_id(merged_id), str(group["group_id"]))

    def test_regenerate_and_refresh_keep_stable_merged_id(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            first = ensure_merged_published_match(str(group["group_id"]))

            # Regenerate from current pins keeps the merged published ID.
            second = ensure_merged_published_match(str(group["group_id"]))
            self.assertEqual(first["merged_published_match_id"], second["merged_published_match_id"])

            # Merged match appears in the standard published-match listing.
            ids = [row["id"] for row in store_list_published_matches()]
            self.assertIn(first["merged_published_match_id"], ids)
            # ... but is never offered as a mergeable fragment.
            eligible = [row["id"] for row in store_list_eligible_sources()]
            self.assertNotIn(first["merged_published_match_id"], eligible)
            self.assertIn("published-one", eligible)

    def test_heatmap_orientation_not_proven_yields_no_spatial_output(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300, calibration=_FLIPPED_CALIBRATION_POINTS)
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            report = ensure_merged_published_match(str(group["group_id"]))["report"]
            player = next(row for row in report["players"] if row["player_id"] == "player-one")
            # Same dims but different calibration: orientation unproven → None,
            # never points rendered against fallback dimensions.
            self.assertIsNone(player["heatmap"])
            self.assertIn("unavailable", str(report["merged_provenance"].get("spatial_heatmaps")))

    def test_heatmap_missing_calibration_yields_no_spatial_output(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300, calibration=None)
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            report = ensure_merged_published_match(str(group["group_id"]))["report"]
            player = next(row for row in report["players"] if row["player_id"] == "player-one")
            self.assertIsNone(player["heatmap"])

    def test_missing_heatmap_lineage_disables_heatmaps_but_keeps_report(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300, heatmap_digest=None)
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            report = ensure_merged_published_match(str(group["group_id"]))["report"]
            # The canonical report still renders; only spatial output is off.
            self.assertEqual(report["report_type"], "public_match_report")
            player = next(row for row in report["players"] if row["player_id"] == "player-one")
            self.assertIsNone(player["heatmap"])
            self.assertIn("spatial_lineage_unproven", str(report["merged_provenance"].get("spatial_heatmaps")))
            merged_id = report["id"]
            heatmap_dir = root / "published" / merged_id / "heatmaps"
            self.assertFalse(any(heatmap_dir.iterdir()) if heatmap_dir.is_dir() else False)
            mirror_heatmaps = root / "client-public" / merged_id / "heatmaps"
            self.assertFalse(any(mirror_heatmaps.iterdir()) if mirror_heatmaps.is_dir() else False)

    def test_stale_spatial_lineage_fails_closed(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300, heatmap_digest="sha256:stale-generation")
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            with self.assertRaises(MatchGroupError) as failure:
                ensure_merged_published_match(str(group["group_id"]))
            self.assertEqual(failure.exception.code, "spatial_lineage_mismatch")

    def test_merged_heatmap_uses_shared_renderer(self) -> None:
        from unittest.mock import patch as mock_patch

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            seen: dict[str, object] = {}

            def fake_renderer(output_path, rows, **kwargs):
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_bytes(b"png")
                seen["rows"] = list(rows)
                seen["kwargs"] = dict(kwargs)

            with mock_patch("app.services.merged_public_match._write_player_heatmap_png", side_effect=fake_renderer):
                ensure_merged_published_match(str(group["group_id"]))
            # All 8 merged pitch-m samples reach the shared renderer in one call.
            merged_rows = seen["rows"]
            assert isinstance(merged_rows, list)
            self.assertEqual(len(merged_rows), 8)
            merged_kwargs = seen["kwargs"]
            assert isinstance(merged_kwargs, dict)
            self.assertEqual(merged_kwargs["pitch_width_m"], 30.0)

    def test_delete_group_removes_projection_mirror_and_keeps_sources(self) -> None:
        from fastapi import HTTPException

        from app.main import api_delete_match_group

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            before = {
                str(path.relative_to(root)): path.read_bytes()
                for path in sorted((root / "published").rglob("*"))
                if path.is_file()
            }
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            group_id = str(group["group_id"])
            merged_id = ensure_merged_published_match(group_id)["merged_published_match_id"]
            self.assertTrue((root / "published" / merged_id).is_dir())

            response = api_delete_match_group(group_id)
            self.assertEqual(response["status"], "deleted")

            # Group, canonical projection, and static mirror are all gone.
            self.assertFalse((root / "groups" / group_id).exists())
            self.assertFalse((root / "published" / merged_id).exists())
            self.assertFalse((root / "client-public" / merged_id).exists())
            # Physical member publications are byte-identical.
            after = {
                str(path.relative_to(root)): path.read_bytes()
                for path in sorted((root / "published").rglob("*"))
                if path.is_file()
            }
            self.assertEqual(after, before)
            with self.assertRaises(HTTPException):
                api_delete_match_group(group_id)

    def test_delete_blocked_while_maintenance_reservation_held(self) -> None:
        from fastapi import HTTPException

        from app.main import api_delete_match_group
        from app.services.match_group_video import reserve_match_group_video_idle

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            group_id = str(group["group_id"])
            merged_id = ensure_merged_published_match(group_id)["merged_published_match_id"]
            with reserve_match_group_video_idle(group_id, operation="test-maintenance"):
                with self.assertRaises(HTTPException) as failure:
                    api_delete_match_group(group_id)
            self.assertEqual(failure.exception.status_code, 409)
            # Nothing was deleted by the blocked attempt.
            self.assertTrue((root / "groups" / group_id).is_dir())
            self.assertTrue((root / "published" / merged_id).is_dir())

    def test_projection_build_failure_preserves_previous_complete_projection(self) -> None:
        from unittest.mock import patch as mock_patch

        from app.services.merged_public_match import check_merged_projection

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            group_id = str(group["group_id"])
            merged_id = ensure_merged_published_match(group_id)["merged_published_match_id"]
            live_before = _snapshot_tree(root / "published" / merged_id)
            mirror_before = _snapshot_tree(root / "client-public" / merged_id)
            self.assertTrue(live_before)

            failures = {
                "heatmap render": "app.services.merged_public_match._write_player_heatmap_png",
                "candidate validation": "app.services.merged_public_match._validate_projection_candidate",
                "atomic commit": "app.services.json_publish_store._commit_publication_generation",
                "mirror copy": "shutil.copytree",
            }
            for name, target in failures.items():
                with mock_patch(target, side_effect=RuntimeError(f"injected {name}")):
                    with self.assertRaises(Exception, msg=name):
                        ensure_merged_published_match(group_id)
                self.assertEqual(_snapshot_tree(root / "published" / merged_id), live_before, name)
                self.assertEqual(_snapshot_tree(root / "client-public" / merged_id), mirror_before, name)
            self.assertEqual(check_merged_projection(merged_id)["status"], "current")

    def test_refresh_advances_pins_and_projection_coherently(self) -> None:
        from app.services.merged_public_match import check_merged_projection, refresh_merged_match_to_latest
        from app.services.match_groups import get_match_group

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            group_id = str(group["group_id"])
            merged_id = ensure_merged_published_match(group_id)["merged_published_match_id"]

            # A rebuilt physical source changes its pinned generation.
            _write_source(root, "published-two", "physical-two", duration=400)
            response = refresh_merged_match_to_latest(group_id)
            self.assertEqual(response["status"], "refreshed")
            self.assertEqual(response["merged_published_match_id"], merged_id)

            stored = store_get_published_match(merged_id)
            self.assertEqual(stored["public_report"]["match"]["duration_sec"], 1000.0)
            manifest = get_match_group(group_id)
            provenance = stored["provenance"]
            self.assertEqual(provenance["manifest_digest"], manifest["aggregate_semantic_digest"])
            self.assertEqual(check_merged_projection(merged_id)["status"], "current")

    def test_tampered_live_report_reads_as_stale_not_silent(self) -> None:
        from app.services.merged_public_match import check_merged_projection

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            merged_id = ensure_merged_published_match(str(group["group_id"]))["merged_published_match_id"]
            self.assertEqual(check_merged_projection(merged_id)["status"], "current")

            live_report = root / "published" / merged_id / "public_report.json"
            document = _read(live_report)
            document["match"]["title"] = "Tampered title"
            _write(live_report, document)
            self.assertEqual(check_merged_projection(merged_id)["status"], "stale")

    def test_refresh_aborts_when_physical_source_rebuilt_mid_refresh(self) -> None:
        import threading
        from unittest.mock import patch as mock_patch

        from app.services.match_groups import get_match_group
        from app.services.merged_public_match import refresh_merged_match_to_latest

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            group_id = str(group["group_id"])
            merged_id = ensure_merged_published_match(group_id)["merged_published_match_id"]
            pins_before = json.loads(json.dumps(get_match_group(group_id)["members"]))
            live_before = _snapshot_tree(root / "published" / merged_id)
            mirror_before = _snapshot_tree(root / "client-public" / merged_id)

            entered = threading.Event()
            release = threading.Event()
            real_validate = __import__("app.services.merged_public_match", fromlist=["_validate_projection_candidate"])._validate_projection_candidate

            def gate(staged, candidate_id):
                entered.set()
                assert release.wait(timeout=30), "refresh worker never reached staging gate"
                return real_validate(staged, candidate_id)

            errors: list[BaseException] = []

            def worker() -> None:
                try:
                    refresh_merged_match_to_latest(group_id)
                except BaseException as error:  # noqa: BLE001 - captured for assertions
                    errors.append(error)

            # Prime a first in-place rebuild (G1.5) so refresh has changed
            # pins and proceeds to candidate staging.
            _write_source(root, "published-two", "physical-two", duration=350)
            with mock_patch("app.services.merged_public_match._validate_projection_candidate", side_effect=gate):
                thread = threading.Thread(target=worker, daemon=True)
                thread.start()
                self.assertTrue(entered.wait(timeout=30), "refresh did not reach the pre-commit gate")
                # Physical "Przebuduj publikację" lands G2 in place mid-refresh.
                _write_source(root, "published-two", "physical-two", duration=400)
                release.set()
                thread.join(timeout=60)
            self.assertFalse(thread.is_alive(), "refresh worker hung")

            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], MatchGroupError)
            assert isinstance(errors[0], MatchGroupError)
            self.assertEqual(errors[0].code, "source_generation_changed_during_refresh")

            # NOTHING committed: pins still G1, projection still the old one.
            self.assertEqual(get_match_group(group_id)["members"], pins_before)
            self.assertEqual(_snapshot_tree(root / "published" / merged_id), live_before)
            self.assertEqual(_snapshot_tree(root / "client-public" / merged_id), mirror_before)
            staging_root = root / ".staging"
            leftovers = [path for path in staging_root.rglob("*")] if staging_root.exists() else []
            self.assertEqual(leftovers, [])
            # Physical G2 itself is untouched and refreshable on retry.
            current_two = _read(root / "published" / "published-two" / "aggregate_inputs.json")
            self.assertEqual(current_two["timing"]["analyzed_duration_sec"], 400)

    def test_sidecar_reservation_failure_leaves_no_orphan(self) -> None:
        from unittest.mock import patch as mock_patch

        from app.services.merged_public_match import merged_ids_for_group

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            group_id = str(group["group_id"])
            before = _snapshot_tree(root / "published")

            # A: establishing the stable ID itself fails.
            with mock_patch(
                "app.services.merged_public_match._write_merged_projection",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaises(MatchGroupError):
                    ensure_merged_published_match(group_id)
            self.assertEqual(merged_ids_for_group(group_id), [])
            self.assertEqual(_snapshot_tree(root / "published"), before)
            client_public = root / "client-public"
            self.assertFalse(client_public.exists() and any(client_public.rglob("*")))

    def test_promotion_failure_keeps_sidecar_and_reuses_id_on_retry(self) -> None:
        from unittest.mock import patch as mock_patch

        from app.services.merged_public_match import merged_ids_for_group

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            group_id = str(group["group_id"])

            # B/C: first creation — live promotion fails after ID reservation.
            # The sidecar retains the ID and no orphan live publication exists
            # without an authoritative relation.
            with mock_patch(
                "app.services.json_publish_store._commit_publication_generation",
                side_effect=OSError("promotion exploded"),
            ):
                with self.assertRaises(OSError):
                    ensure_merged_published_match(group_id)
            reserved = merged_ids_for_group(group_id)
            self.assertEqual(len(reserved), 1)
            reserved_id = reserved[0]
            # Live projection absent on first creation, sidecar retained.
            self.assertFalse((root / "published" / reserved_id).exists())
            # Retry reuses the SAME stable merged ID and succeeds.
            retry = ensure_merged_published_match(group_id)
            self.assertEqual(retry["merged_published_match_id"], reserved_id)
            self.assertTrue((root / "published" / reserved_id / "public_report.json").is_file())

    def test_lazy_migration_retry_uses_single_stable_id(self) -> None:
        from unittest.mock import patch as mock_patch

        from app.services.merged_public_match import merged_ids_for_group

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            group_id = str(group["group_id"])
            self.assertEqual(merged_ids_for_group(group_id), [])

            # D: first lazy attempt fails (heatmap render explodes), retry works.
            calls = {"count": 0}
            real_render = __import__("app.services.merged_public_match", fromlist=["render_merged_heatmaps"]).render_merged_heatmaps

            def flaky_render(report, sources, **kwargs):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise OSError("renderer exploded")
                return real_render(report, sources, **kwargs)

            with mock_patch("app.services.merged_public_match.render_merged_heatmaps", side_effect=flaky_render):
                with self.assertRaises(OSError):
                    ensure_merged_published_match(group_id)
            first_ids = merged_ids_for_group(group_id)
            self.assertEqual(len(first_ids), 1)
            retry = ensure_merged_published_match(group_id)
            self.assertEqual(retry["merged_published_match_id"], first_ids[0])
            live = [path for path in (root / "published").iterdir() if path.name.startswith("published-merged-")]
            self.assertEqual(len(live), 1)

    def test_concurrent_first_merged_projection_uses_one_reserved_id_and_no_orphan(self) -> None:
        import threading

        from app.services.merged_public_match import get_merged_projection

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            group_id = str(group["group_id"])
            physical_before = {
                published_id: _snapshot_tree(root / "published" / published_id)
                for published_id in ("published-one", "published-two")
            }

            # Both lazy readers start together.  The first owns projection
            # maintenance while choosing the stable ID; the second must then
            # reuse it after the first lifecycle operation completes.
            start = threading.Barrier(3)
            results: list[dict] = []
            errors: list[BaseException] = []

            def worker() -> None:
                try:
                    start.wait(timeout=30)
                    results.append(ensure_merged_published_match(group_id))
                except BaseException as error:  # noqa: BLE001 - asserted below
                    errors.append(error)

            first = threading.Thread(target=worker, daemon=True)
            second = threading.Thread(target=worker, daemon=True)
            first.start()
            second.start()
            start.wait(timeout=30)
            first.join(timeout=60)
            second.join(timeout=60)

            self.assertFalse(first.is_alive(), "first lazy reader hung")
            self.assertFalse(second.is_alive(), "second lazy reader hung")
            # Maintenance ownership intentionally rejects a concurrent full
            # lifecycle operation.  Its retry must still reuse the winner's
            # sidecar ID; the lower-level reservation test below covers two
            # simultaneous candidate IDs directly.
            self.assertLessEqual(len(errors), 1)
            if errors:
                from app.services.match_group_video import MatchGroupVideoError

                self.assertIsInstance(errors[0], MatchGroupVideoError)
                assert isinstance(errors[0], MatchGroupVideoError)
                self.assertEqual(errors[0].code, "match_group_maintenance_in_progress")
                results.append(ensure_merged_published_match(group_id))
            self.assertEqual(len(results), 2)
            merged_ids = {str(result["merged_published_match_id"]) for result in results}
            self.assertEqual(len(merged_ids), 1)
            merged_id = next(iter(merged_ids))
            sidecar = get_merged_projection(group_id)
            self.assertIsNotNone(sidecar)
            assert sidecar is not None
            self.assertEqual(sidecar["merged_published_match_id"], merged_id)
            self.assertEqual(
                [path.name for path in (root / "published").iterdir() if path.name.startswith("published-merged-")],
                [merged_id],
            )
            self.assertEqual(
                [path.name for path in (root / "client-public").iterdir() if path.name.startswith("published-merged-")],
                [merged_id],
            )
            self.assertEqual(
                {
                    published_id: _snapshot_tree(root / "published" / published_id)
                    for published_id in ("published-one", "published-two")
                },
                physical_before,
            )

    def test_concurrent_candidate_reservations_reuse_the_authoritative_winner(self) -> None:
        import threading

        from app.services.merged_public_match import (
            _reserve_merged_projection,
            get_merged_projection,
            new_merged_published_id,
        )

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            group_id = str(group["group_id"])
            candidates = (new_merged_published_id(), new_merged_published_id())
            start = threading.Barrier(3)
            results: list[str] = []
            errors: list[BaseException] = []

            def worker(candidate_id: str) -> None:
                try:
                    start.wait(timeout=30)
                    results.append(_reserve_merged_projection(group_id, candidate_id))
                except BaseException as error:  # noqa: BLE001 - asserted below
                    errors.append(error)

            first = threading.Thread(target=worker, args=(candidates[0],), daemon=True)
            second = threading.Thread(target=worker, args=(candidates[1],), daemon=True)
            first.start()
            second.start()
            start.wait(timeout=30)
            first.join(timeout=30)
            second.join(timeout=30)

            self.assertFalse(first.is_alive(), "first candidate reservation hung")
            self.assertFalse(second.is_alive(), "second candidate reservation hung")
            self.assertEqual(errors, [])
            self.assertEqual(len(set(results)), 1)
            winner = results[0]
            self.assertIn(winner, candidates)
            sidecar = get_merged_projection(group_id)
            self.assertIsNotNone(sidecar)
            assert sidecar is not None
            self.assertEqual(sidecar["merged_published_match_id"], winner)

            # A later explicit candidate can only reuse the valid winner;
            # it may never turn sidecar persistence into last-writer-wins.
            self.assertEqual(_reserve_merged_projection(group_id, new_merged_published_id()), winner)
            self.assertEqual(get_merged_projection(group_id), sidecar)

    def test_concurrent_ensure_cannot_enter_projection_promotion_while_owner_is_staging(self) -> None:
        import threading
        from unittest.mock import patch as mock_patch

        from app.services.match_group_video import MatchGroupVideoError
        from app.services.merged_public_match import check_merged_projection

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group_id = str(create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())["group_id"])
            merged_id = ensure_merged_published_match(group_id)["merged_published_match_id"]
            entered = threading.Event()
            release = threading.Event()
            real_commit = __import__(
                "app.services.merged_public_match",
                fromlist=["_commit_projection_candidate"],
            )._commit_projection_candidate
            commits: list[str] = []
            errors: list[BaseException] = []

            def gate(commit_group_id: str, commit_merged_id: str, candidate: dict) -> None:
                commits.append(commit_merged_id)
                entered.set()
                self.assertTrue(release.wait(timeout=30), "first ensure never released")
                real_commit(commit_group_id, commit_merged_id, candidate)

            def first_ensure() -> None:
                try:
                    ensure_merged_published_match(group_id)
                except BaseException as error:  # noqa: BLE001 - asserted below
                    errors.append(error)

            with mock_patch("app.services.merged_public_match._commit_projection_candidate", side_effect=gate):
                owner = threading.Thread(target=first_ensure, daemon=True)
                owner.start()
                self.assertTrue(entered.wait(timeout=30), "owner did not reach promotion gate")
                with self.assertRaises(MatchGroupVideoError) as blocked:
                    ensure_merged_published_match(group_id)
                self.assertEqual(blocked.exception.code, "match_group_maintenance_in_progress")
                self.assertEqual(commits, [merged_id])
                release.set()
                owner.join(timeout=60)

            self.assertFalse(owner.is_alive(), "owner ensure hung")
            self.assertEqual(errors, [])
            self.assertEqual(ensure_merged_published_match(group_id)["merged_published_match_id"], merged_id)
            self.assertEqual(check_merged_projection(merged_id)["status"], "current")

    def test_first_ensure_blocks_delete_and_cannot_leave_an_orphan_after_retry(self) -> None:
        import threading
        from unittest.mock import patch as mock_patch

        from fastapi import HTTPException

        from app.main import api_delete_match_group
        from app.services.merged_public_match import merged_ids_for_group

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group_id = str(create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())["group_id"])
            entered = threading.Event()
            release = threading.Event()
            real_stage = __import__(
                "app.services.merged_public_match",
                fromlist=["_stage_projection_candidate"],
            )._stage_projection_candidate
            errors: list[BaseException] = []

            def gate(*args: object, **kwargs: object) -> dict:
                candidate = real_stage(*args, **kwargs)
                entered.set()
                self.assertTrue(release.wait(timeout=30), "first ensure never released")
                return candidate

            def worker() -> None:
                try:
                    ensure_merged_published_match(group_id)
                except BaseException as error:  # noqa: BLE001 - asserted below
                    errors.append(error)

            with mock_patch("app.services.merged_public_match._stage_projection_candidate", side_effect=gate):
                owner = threading.Thread(target=worker, daemon=True)
                owner.start()
                self.assertTrue(entered.wait(timeout=30), "ensure did not reach staging gate")
                with self.assertRaises(HTTPException) as blocked:
                    api_delete_match_group(group_id)
                self.assertEqual(blocked.exception.status_code, 409)
                release.set()
                owner.join(timeout=60)

            self.assertFalse(owner.is_alive(), "ensure hung")
            self.assertEqual(errors, [])
            merged_id = merged_ids_for_group(group_id)[0]
            self.assertTrue((root / "published" / merged_id).is_dir())
            self.assertEqual(api_delete_match_group(group_id)["status"], "deleted")
            self.assertFalse((root / "groups" / group_id).exists())
            self.assertFalse((root / "published" / merged_id).exists())
            self.assertFalse((root / "client-public" / merged_id).exists())

    def test_delete_winning_before_ensure_prevents_projection_resurrection(self) -> None:
        from app.services.match_group_video import reserve_match_group_video_idle
        from app.services.match_groups import delete_match_group

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group_id = str(create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())["group_id"])
            with reserve_match_group_video_idle(group_id, operation="test-delete-winner"):
                delete_match_group(group_id)
                with self.assertRaises(KeyError):
                    ensure_merged_published_match(group_id)
            self.assertFalse((root / "groups" / group_id).exists())
            self.assertEqual([path for path in (root / "published").iterdir() if path.name.startswith("published-merged-")], [])
            self.assertEqual([path for path in (root / "client-public").iterdir() if path.name.startswith("published-merged-")], [])

    def test_staged_candidate_does_not_promote_after_backing_manifest_changes(self) -> None:
        import threading
        from unittest.mock import patch as mock_patch

        from app.services.match_groups import update_match_group
        from app.services.merged_public_match import merged_published_id_for_group

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group_id = str(create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())["group_id"])
            entered = threading.Event()
            release = threading.Event()
            real_stage = __import__(
                "app.services.merged_public_match",
                fromlist=["_stage_projection_candidate"],
            )._stage_projection_candidate
            errors: list[BaseException] = []

            def gate(*args: object, **kwargs: object) -> dict:
                candidate = real_stage(*args, **kwargs)
                entered.set()
                self.assertTrue(release.wait(timeout=30), "ensure did not resume")
                return candidate

            def worker() -> None:
                try:
                    ensure_merged_published_match(group_id)
                except BaseException as error:  # noqa: BLE001 - asserted below
                    errors.append(error)

            with mock_patch("app.services.merged_public_match._stage_projection_candidate", side_effect=gate):
                owner = threading.Thread(target=worker, daemon=True)
                owner.start()
                self.assertTrue(entered.wait(timeout=30), "ensure did not reach staging gate")
                # Controlled failure injection: production updates are excluded
                # by maintenance ownership, so mutate directly to prove the
                # mandatory pre-promotion digest guard itself.
                update_match_group(
                    group_id,
                    member_published_ids=["published-one", "published-two"],
                    metadata={**_metadata(), "title": "G2 while G1 staged"},
                )
                release.set()
                owner.join(timeout=60)

            self.assertFalse(owner.is_alive(), "ensure hung")
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], MatchGroupError)
            assert isinstance(errors[0], MatchGroupError)
            self.assertEqual(errors[0].code, "merged_projection_source_changed")
            merged_id = merged_published_id_for_group(group_id)
            self.assertIsNotNone(merged_id)
            assert merged_id is not None
            self.assertFalse((root / "published" / merged_id).exists())
            self.assertFalse((root / "client-public" / merged_id).exists())

    def test_failed_commit_releases_lifecycle_before_retry_can_promote_coherently(self) -> None:
        import threading
        from unittest.mock import patch as mock_patch

        from app.services.match_group_video import MatchGroupVideoError
        from app.services.merged_public_match import check_merged_projection

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group_id = str(create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())["group_id"])
            merged_id = ensure_merged_published_match(group_id)["merged_published_match_id"]
            entered = threading.Event()
            release = threading.Event()
            real_commit = __import__(
                "app.services.json_publish_store",
                fromlist=["_commit_publication_generation"],
            )._commit_publication_generation
            calls = {"count": 0}
            errors: list[BaseException] = []

            def fail_first_commit(*args: object, **kwargs: object) -> None:
                calls["count"] += 1
                if calls["count"] == 1:
                    entered.set()
                    self.assertTrue(release.wait(timeout=30), "failing owner never released")
                    raise OSError("injected private/public promotion failure")
                real_commit(*args, **kwargs)

            def failing_owner() -> None:
                try:
                    ensure_merged_published_match(group_id)
                except BaseException as error:  # noqa: BLE001 - asserted below
                    errors.append(error)

            with mock_patch("app.services.json_publish_store._commit_publication_generation", side_effect=fail_first_commit):
                owner = threading.Thread(target=failing_owner, daemon=True)
                owner.start()
                self.assertTrue(entered.wait(timeout=30), "owner did not enter commit")
                with self.assertRaises(MatchGroupVideoError) as blocked:
                    ensure_merged_published_match(group_id)
                self.assertEqual(blocked.exception.code, "match_group_maintenance_in_progress")
                self.assertEqual(calls["count"], 1)
                release.set()
                owner.join(timeout=60)
                self.assertEqual(ensure_merged_published_match(group_id)["merged_published_match_id"], merged_id)

            self.assertFalse(owner.is_alive(), "failing owner hung")
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], OSError)
            self.assertEqual(check_merged_projection(merged_id)["status"], "current")

    def test_conflicting_player_team_fails_closed(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            _force_player_team(root, "published-two", "player-one", "team-verisk")
            with self.assertRaises(MatchGroupError):
                create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

    def test_swapped_local_labels_map_to_same_canonical_teams(self) -> None:
        with self._store() as root:
            # Real local sign semantics: source 1 A=Corgi +0.8 / B=Verisk -0.2,
            # source 2 A=Verisk +0.6 / B=Corgi -0.3.
            _write_source(root, "published-one", "physical-one", duration=600, labels=("A", "B"),
                          momentum_local_a=0.8, momentum_local_b=-0.2, momentum_dominant_local="A")
            _write_source(root, "published-two", "physical-two", duration=300, labels=("B", "A"),
                          momentum_local_a=0.6, momentum_local_b=-0.3, momentum_dominant_local="A")
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            result = ensure_merged_published_match(str(group["group_id"]))
            report = result["report"]
            labels = {row["team_id"]: row["team_label"] for row in report["teams"]}
            self.assertEqual(labels, {"team-corgi": "A", "team-verisk": "B"})
            # Canonical momentum signs follow canonical roles, not local ones.
            first, second = report["ball"]["attacking_momentum"]["timeline"]
            self.assertAlmostEqual(first["team_a_value"], 0.8, places=3)
            self.assertAlmostEqual(first["team_b_value"], -0.2, places=3)
            self.assertAlmostEqual(first["signed_score"], 0.6, places=3)
            self.assertEqual(first["dominant_team_label"], "A")
            self.assertAlmostEqual(second["team_a_value"], 0.3, places=3)
            self.assertAlmostEqual(second["team_b_value"], -0.6, places=3)
            self.assertAlmostEqual(second["signed_score"], -0.3, places=3)
            self.assertEqual(second["dominant_team_label"], "B")

    def test_aggregate_report_still_generated_for_internal_provenance(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            aggregate = generate_match_group_report(str(group["group_id"]))
            self.assertEqual(aggregate["report_type"], "public_aggregate_match_report")
            merged = ensure_merged_published_match(str(group["group_id"]))
            self.assertEqual(merged["report"]["report_type"], "public_match_report")

    def test_merged_facade_regenerate_and_refresh_keep_id_and_reject_physical(self) -> None:
        from fastapi import HTTPException

        from app.main import (
            api_refresh_merged_published_match_to_latest,
            api_regenerate_merged_published_match,
        )

        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            first = ensure_merged_published_match(str(group["group_id"]))
            merged_id = first["merged_published_match_id"]

            regenerated = api_regenerate_merged_published_match(merged_id)
            self.assertEqual(regenerated["id"], merged_id)
            self.assertEqual(regenerated["public_report"]["report_type"], "public_match_report")

            refreshed = api_refresh_merged_published_match_to_latest(merged_id)
            self.assertEqual(refreshed["id"], merged_id)

            with self.assertRaises(HTTPException) as failure:
                api_regenerate_merged_published_match("published-one")
            self.assertEqual(failure.exception.status_code, 409)
            with self.assertRaises(HTTPException) as failure:
                api_refresh_merged_published_match_to_latest("published-one")
            self.assertEqual(failure.exception.status_code, 409)

    # -- store context -----------------------------------------------------

    def _store(self):  # type: ignore[no-untyped-def]
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "published").mkdir(parents=True)
        (root / "groups").mkdir(parents=True)
        (root / "client-public").mkdir(parents=True)
        patches = [
            patch("app.services.match_groups.PUBLISHED_MATCHES_DIR", root / "published"),
            patch("app.services.match_groups.MATCH_GROUPS_DIR", root / "groups"),
            patch("app.services.match_group_aggregation.PUBLISHED_MATCHES_DIR", root / "published"),
            patch("app.services.match_group_aggregation.MATCH_GROUPS_DIR", root / "groups"),
            patch("app.services.match_group_video.MATCH_GROUPS_DIR", root / "groups"),
            patch("app.services.match_group_video.PUBLISHED_MATCHES_DIR", root / "published"),
            patch("app.services.match_group_refresh.MATCH_GROUPS_DIR", root / "groups"),
            patch("app.services.merged_public_match.PUBLISHED_MATCHES_DIR", root / "published"),
            patch("app.services.merged_public_match.MATCH_GROUPS_DIR", root / "groups"),
            patch("app.services.merged_public_match.CLIENT_PUBLIC_MATCHES_DIR", root / "client-public"),
            patch("app.services.json_publish_store.PUBLISHED_MATCHES_DIR", root / "published"),
        ]

        class StoreContext:
            def __enter__(self) -> Path:
                for item in patches:
                    item.__enter__()
                return root

            def __exit__(self, *args: object) -> None:
                for item in reversed(patches):
                    item.__exit__(*args)  # type: ignore[arg-type]
                temporary.cleanup()

        return StoreContext()


_SHARED_CALIBRATION_POINTS = [[0.0, 0.0], [100.0, 0.0], [100.0, 200.0], [0.0, 200.0]]
_FLIPPED_CALIBRATION_POINTS = [[100.0, 0.0], [0.0, 0.0], [0.0, 200.0], [100.0, 200.0]]


def _write_source(
    root: Path,
    published_id: str,
    source_match_id: str,
    *,
    duration: float = 600,
    labels: tuple[str, str] = ("A", "B"),
    team_distance: float = 1000,
    peak: float = 20,
    player_distance: float = 100,
    movement_time: float | None = None,
    detected_time: float | None = None,
    attempts: int = 20,
    completed: int = 16,
    controlled_corgi: float = 360,
    controlled_verisk: float = 240,
    contested: float = 0,
    free: float = 0,
    unknown: float = 0,
    momentum_local_a: float = 60.0,
    momentum_local_b: float = -20.0,
    momentum_dominant_local: str = "A",
    calibration: list | None = _SHARED_CALIBRATION_POINTS,
    heatmap_digest: str | None = "auto",
    team_shape_eligible: int = 200,
    team_shape_width: float = 20.0,
    team_shape_cells: tuple[float, float] = (0.6, 0.4),
) -> None:
    """Write one adversarial physical publication fixture.

    Deliberately distinguishable values: movement_time != detected_time,
    contested/free/unknown != 0, local A/B momentum signs following LOCAL
    semantics, and explicit calibration identity for spatial gating.
    """

    directory = root / "published" / published_id
    directory.mkdir(parents=True, exist_ok=True)
    corgi_label, verisk_label = labels
    movement = movement_time if movement_time is not None else duration
    detected = detected_time if detected_time is not None else duration
    reviewed_digest = f"reviewed-{source_match_id}"
    total_frames = controlled_corgi + controlled_verisk + contested + free + unknown
    stable_of_local = {corgi_label: "team-corgi", verisk_label: "team-verisk"}
    public = {
        "schema_version": PUBLIC_MATCH_REPORT_SCHEMA_VERSION,
        "report_type": PUBLIC_MATCH_REPORT_TYPE,
        "id": published_id,
        "source_match_id": source_match_id,
        "generated_at": "2026-09-01T00:00:00+00:00",
        "match": {"id": source_match_id, "title": source_match_id, "duration_sec": duration},
        "stats_semantics": {"ball": "experimental_candidates"},
        "teams": [
            {
                "team_label": corgi_label,
                "team_id": "team-corgi",
                "team_name": "Corgi",
                "playing_time_sec": duration,
                "total_distance_m": team_distance,
                "high_intensity_distance_m": 100,
                "sprint_count": 5,
                "avg_speed_kmh": 6.0,
                "peak_speed_kmh": peak,
                "pass_candidates": attempts + 2,
                "pass_attempts": attempts,
                "completed_passes": completed,
                "failed_passes": attempts - completed,
                "completion_rate": round(completed / attempts * 100, 1),
                "restart_passes": 1,
                "same_team_pass_candidates": attempts,
                "turnover_or_interception_candidates": 0,
                "progressive_pass_candidates": 3,
                "accepted_passes": completed,
            },
            {
                "team_label": verisk_label,
                "team_id": "team-verisk",
                "team_name": "Verisk",
                "playing_time_sec": duration,
                "total_distance_m": team_distance / 2,
                "high_intensity_distance_m": 50,
                "sprint_count": 2,
                "avg_speed_kmh": 5.0,
                "peak_speed_kmh": peak - 1,
                "pass_candidates": 5,
                "pass_attempts": 4,
                "completed_passes": 3,
                "failed_passes": 1,
                "completion_rate": 75.0,
                "restart_passes": 0,
                "same_team_pass_candidates": 4,
                "turnover_or_interception_candidates": 0,
                "progressive_pass_candidates": 1,
                "accepted_passes": 3,
            },
        ],
        "players": [
            {
                "player_id": "player-one",
                "player_name": "Alex",
                "player_number": "7",
                "team_id": "team-corgi",
                "team_label": corgi_label,
                "playing_time_sec": duration,
                "detected_time_sec": detected,
                "certain_playing_time_sec": detected,
                "possible_playing_time_sec": 0,
                "ambiguous_playing_time_sec": 0,
                "continuity_gap_time_sec": 0,
                "playing_time_method": "exact_detected_only",
                "total_distance_m": player_distance,
                "avg_speed_kmh": 6.0,
                "peak_speed_kmh": peak,
                "high_intensity_distance_m": 10,
                "high_intensity_time_sec": 5,
                "sprint_count": 2 if duration >= 600 else 1,
                "sprint_time_sec": 2,
                "sprint_distance_m": 20,
                "max_sprint_speed_kmh": peak,
                "workload": {
                    "semantics": "reviewed_confirmed_detected_in_play",
                    "rate_window_sec": 300.0,
                    "minimum_rate_sample_sec": 120.0,
                    "detected_time_sec": detected,
                    "distance_per_5min_m": 50.0,
                    "high_intensity_distance_per_5min_m": 5.0,
                    "sprints_per_5min": 1.0,
                    "high_intensity_distance_ratio": 0.1,
                    "activity_windows": [
                        {
                            "window_index": 0,
                            "start_time_sec": 0,
                            "end_time_sec": min(300, duration),
                            "duration_sec": min(300, duration),
                            "display_label": "0–5",
                            "detected_time_sec": min(300, detected),
                            "total_distance_m": 50,
                            "high_intensity_distance_m": 5,
                            "sprint_count": 1,
                            "rate_status": "reportable",
                            "distance_per_5min_m": 50.0,
                            "high_intensity_distance_per_5min_m": 5.0,
                            "sprints_per_5min": 1.0,
                        }
                    ],
                    "best_activity_window": None,
                },
                "calculation_method": "exact_identity_coverage",
                "coverage_ratio": 1.0,
                "quality_flags": [],
            }
        ],
        "ball": {
            "pass_candidates": attempts + 2,
            "pass_attempts": attempts,
            "completed_passes": completed,
            "failed_passes": attempts - completed,
            "completion_rate": round(completed / attempts * 100, 1),
            "restart_passes": 1,
            "same_team_pass_candidates": attempts,
            "progressive_pass_candidates": 3,
            "accepted_passes": completed,
            "possession_timeline": [],
            "attacking_momentum": {"experimental": True, "status": "completed", "timeline": []},
        },
    }
    aggregate = {
        "schema_version": "1.0.0",
        "aggregation_policy_version": "1.0.0",
        "source": {
            "source_match_id": source_match_id,
            "published_id": published_id,
            "reviewed_identity_digest": reviewed_digest,
            "public_report_semantic_digest": canonical_json_sha256(public),
        },
        "timing": {"analyzed_duration_sec": duration},
        "teams": [
            {"team_id": "team-corgi", "source_team_label": corgi_label, "movement": {"total_distance_m": team_distance, "high_intensity_distance_m": 100, "sprint_count": 5, "peak_speed_kmh": peak}},
            {"team_id": "team-verisk", "source_team_label": verisk_label, "movement": {"total_distance_m": team_distance / 2, "high_intensity_distance_m": 50, "sprint_count": 2, "peak_speed_kmh": peak - 1}},
        ],
        "players": [
            {
                "player_id": "player-one",
                "team_id": "team-corgi",
                "movement": {
                    "total_distance_m": player_distance,
                    "movement_time_sec": movement,
                    "detected_time_sec": detected,
                    "high_intensity_distance_m": 10,
                    "sprint_count": 2 if duration >= 600 else 1,
                    "peak_speed_kmh": peak,
                },
            }
        ],
        "identity_coverage": {"status": "ready", "coverage_unit": "observations", "confirmed_observations": 15, "reliable_observations": 20, "unresolved_observations": 3, "conflicted_observations": 2},
        "ball": {
            "possession": {"status": "ready", "controlled_frames_by_team_id": {"team-corgi": controlled_corgi, "team-verisk": controlled_verisk}, "known_frames": controlled_corgi + controlled_verisk, "contested_frames": contested, "free_frames": free, "unknown_frames": unknown, "processed_frames": total_frames},
            "passes": {"status": "ready", "attempts": attempts, "completed": completed, "failed": attempts - completed, "restart_attempts": 1, "accepted": completed, "attempts_by_team_id": {"team-corgi": attempts, "team-verisk": 0}, "completed_by_team_id": {"team-corgi": completed, "team-verisk": 0}, "failed_by_team_id": {"team-corgi": attempts - completed, "team-verisk": 0}, "restart_attempts_by_team_id": {"team-corgi": 1, "team-verisk": 0}, "accepted_by_team_id": {"team-corgi": completed, "team-verisk": 0}},
        },
        "timelines": {
            "possession": {"status": "ready", "windows": [{"start_time_sec": 0, "end_time_sec": duration, "controlled_frames_by_team_id": {"team-corgi": controlled_corgi, "team-verisk": controlled_verisk}, "contested_frames": contested, "free_frames": free, "unknown_frames": unknown, "frames": total_frames}]},
            "attacking_momentum": {"status": "completed", "product_readiness": "experimental", "signal_quality": "medium", "quality": "medium", "points": [{"start_time_sec": 0, "end_time_sec": duration, "team_values_by_team_id": {stable_of_local["A"]: momentum_local_a, stable_of_local["B"]: momentum_local_b}, "dominant_team_id": stable_of_local[momentum_dominant_local], "confidence": 0.9, "intensity": 0.8}]},
        },
        "spatial": {"orientation": "unproven", "heatmaps": {"status": "not_available"}, "team_shape": {"status": "not_available"}, "pitch_dimensions_m": {"width_m": 30.0, "length_m": 50.0}},
        "metric_readiness": {"team_movement": {"status": "ready"}, "player_movement": {"status": "ready"}, "possession": {"status": "ready"}, "passes": {"status": "ready"}},
    }
    aggregate["source"]["aggregation_input_semantic_digest"] = canonical_json_sha256(aggregate)
    pitch_config = (
        {"image_points": [list(point) for point in calibration], "width_m": 30.0, "length_m": 50.0, "calibration_frame_time_sec": 1.0}
        if calibration is not None
        else None
    )
    match_phase_config = {"periods": [{"period_id": "full", "start_time_sec": 0, "end_time_sec": duration, "teams": {"A": {"attack_direction": "towards_y_max"}, "B": {"attack_direction": "towards_y_min"}}}]}
    team_config = {"teams": [{"team_label": "A", "team_id": "team-corgi", "team_name": "Corgi"}, {"team_label": "B", "team_id": "team-verisk", "team_name": "Verisk"}]}
    heatmap_digest_value = reviewed_digest if heatmap_digest == "auto" else heatmap_digest
    heatmaps_doc = {
        "schema_version": "1.0.0",
        "source_snapshot_digest": heatmap_digest_value,
        "pitch_dimensions_m": {"width_m": 30.0, "length_m": 50.0},
        "heatmaps": [{"player_id": "player-one", "positions_m": [[5.0 + index, 10.0 + index] for index in range(4)]}],
    }
    team_shape_doc = _team_shape_fixture(duration, eligible=team_shape_eligible, width=team_shape_width, cells=team_shape_cells) if team_shape_eligible else None
    package = {
        "match": {"id": source_match_id, "title": source_match_id},
        "pitch_config": pitch_config,
        "match_phase_config": match_phase_config,
        "team_config": team_config,
        "reviewed_player_heatmaps": heatmaps_doc,
        "team_shape": team_shape_doc,
    }
    if team_shape_doc is not None:
        team_shape_doc["generated_from"] = [
            {"artifact": filename, "sha256": canonical_json_sha256(payload)}
            for filename, payload in (
                ("pitch_config.json", pitch_config),
                ("match_phase_config.json", match_phase_config),
                ("team_config.json", team_config),
            )
        ]
    _write(directory / "public_report.json", public)
    _write(directory / "aggregate_inputs.json", aggregate)
    _write(directory / "package.json", package)
    _write(directory / "summary.json", {
        "id": published_id,
        "source_match_id": source_match_id,
        "source_kind": "physical",
        "title": source_match_id,
        "match_date": "2026-09-01",
        "teams": [{"id": "team-corgi", "name": "Corgi"}, {"id": "team-verisk", "name": "Verisk"}],
        "status": "published",
        "report_type": "public_match_report",
        "created_at": "2026-09-01T00:00:00+00:00",
    })


def _team_shape_fixture(duration: float, *, eligible: int, width: float, cells: tuple[float, float] = (0.6, 0.4)) -> dict:
    grid_cells = [{"column": 0, "row": 0, "value": cells[0]}, {"column": 1, "row": 0, "value": cells[1]}]
    teams = []
    for label, team_id, name in (("A", "team-corgi", "Corgi"), ("B", "team-verisk", "Verisk")):
        teams.append({
            "team_label": label,
            "team_id": team_id,
            "team_name": name,
            "readiness": "ready",
            "summary": {"average_width_m": width, "average_depth_m": 12.0, "average_compactness_m": 4.0, "average_block_height_percent": 50.0},
            "average_shape": {"grid": {"columns": 6, "rows": 10}, "cells": grid_cells},
            "timeline": [
                {"minute": 1, "label": "00:00", "width_m": width, "depth_m": 12.0, "compactness_m": 4.0, "block_height_percent": 50.0},
                {"minute": 2, "label": "01:00", "width_m": width, "depth_m": 12.0, "compactness_m": 4.0, "block_height_percent": 50.0},
            ],
            "diagnostics": {"eligible_frames": eligible, "candidate_frames": eligible, "attack_direction_trusted": True},
        })
    return {
        "schema_version": "team-shape-v1",
        "algorithm_version": "team_shape_spatial_v1_1",
        "available": True,
        "readiness": "ready",
        "pitch_dimensions_m": {"width_m": 30.0, "length_m": 50.0},
        "parameters": {"timeline_bin_sec": 60.0, "density_columns": 6, "density_rows": 10},
        "teams": teams,
        "takeaways": [],
    }


def _force_player_team(root: Path, published_id: str, player_id: str, team_id: str) -> None:
    directory = root / "published" / published_id
    aggregate = _read(directory / "aggregate_inputs.json")
    for row in aggregate["players"]:
        if row["player_id"] == player_id:
            row["team_id"] = team_id
    digest_document = copy.deepcopy(aggregate)
    digest_document["source"].pop("aggregation_input_semantic_digest", None)
    aggregate["source"]["aggregation_input_semantic_digest"] = canonical_json_sha256(digest_document)
    _write(directory / "aggregate_inputs.json", aggregate)


def _metadata() -> dict[str, str]:
    return {"title": "Logical match", "match_date": "2026-09-01", "season": "2026", "venue": "Orlik", "format": "7v7"}


def _snapshot_tree(directory: Path) -> dict[str, bytes]:
    if not directory.is_dir():
        return {}
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
