from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.artifact_lineage import canonical_json_sha256
from app.services.match_group_aggregation import generate_match_group_report
from app.services.match_groups import (
    MatchGroupError,
    create_match_group,
    create_match_group_and_generate_report,
    update_match_group_and_generate_report,
)
from app.services.public_match_report import PUBLIC_MATCH_REPORT_SCHEMA_VERSION, PUBLIC_MATCH_REPORT_TYPE


class MatchGroupAggregationTests(unittest.TestCase):
    def test_two_parts_reconcile_stable_ids_and_recompute_all_rates(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=10, player_distance=100, movement_time=10, peak=20, attempts=10, completed=8, controlled_corgi=60, controlled_verisk=40)
            _write_source(root, "published-two", "physical-two", duration=20, labels=("B", "A"), player_distance=50, movement_time=20, peak=30, attempts=2, completed=1, controlled_corgi=20, controlled_verisk=80)
            manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

            report = generate_match_group_report(manifest["group_id"])

            self.assertEqual(report["report_type"], "public_aggregate_match_report")
            self.assertEqual(report["source_match_ids"], ["physical-one", "physical-two"])
            self.assertEqual(report["timing"]["analyzed_duration_sec"], 30.0)
            corgi = next(row for row in report["teams"] if row["team_id"] == "team-corgi")
            self.assertNotIn("source_team_label", corgi)
            self.assertEqual(corgi["movement"]["total_distance_m"], 200.0)
            player = report["players"][0]
            self.assertEqual(player["movement"]["total_distance_m"], 150.0)
            self.assertEqual(player["movement"]["high_intensity_distance_m"], 20.0)
            self.assertEqual(player["movement"]["sprint_count"], 2.0)
            self.assertEqual(player["movement"]["peak_speed_kmh"], 30.0)
            self.assertEqual(player["movement"]["avg_speed_kmh"], 18.0)
            self.assertEqual(report["ball"]["passes"]["attempts"], 12.0)
            self.assertEqual(report["ball"]["passes"]["completed"], 9.0)
            self.assertEqual(report["ball"]["passes"]["completion_rate_percent"], 75.0)
            self.assertEqual(report["ball"]["possession"]["possession_share_percent_by_team_id"]["team-corgi"], 40.0)
            self.assertEqual(report["identity_coverage"]["confirmed_observations"], 30.0)
            self.assertEqual(report["identity_coverage"]["confirmed_coverage_percent"], 75.0)
            self.assertEqual(
                [(row["start_time_sec"], row["end_time_sec"]) for row in report["timelines"]["possession"]["windows"]],
                [(0.0, 10.0), (10.0, 30.0)],
            )
            windows = report["timelines"]["possession"]["windows"]
            self.assertEqual(windows[0]["known_team_frames"], 100.0)
            self.assertEqual(windows[0]["possession_share_percent_by_team_id"]["team-corgi"], 60.0)
            self.assertEqual(windows[1]["possession_share_percent_by_team_id"]["team-corgi"], 20.0)

    def test_three_parts_rebase_timeline_and_keep_players_present_in_one_part(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=10, extra_players=[("player-one", "team-corgi", "Alex")])
            _write_source(root, "published-two", "physical-two", duration=20, extra_players=[("player-two", "team-corgi", "Alex")])
            _write_source(root, "published-three", "physical-three", duration=30, extra_players=[])
            manifest = create_match_group(member_published_ids=["published-one", "published-two", "published-three"], metadata=_metadata())

            report = generate_match_group_report(manifest["group_id"])

            self.assertEqual([row["start_time_sec"] for row in report["timelines"]["attacking_momentum"]["points"]], [0.0, 10.0, 30.0])
            self.assertEqual([row["player_id"] for row in report["players"]], ["player-one", "player-two"])
            self.assertEqual([row["player_name"] for row in report["players"]], ["Alex", "Alex"])

    def test_key_moments_are_derived_after_member_order_rebasing_and_ignore_source_rows(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            _set_source_momentum(root, "published-two", {
                "start_time_sec": 120,
                "end_time_sec": 125,
                "team_values_by_team_id": {"team-corgi": 0.82, "team-verisk": 0.18},
                "dominant_team_id": "team-corgi",
                "intensity": 0.82,
                "confidence": 1.0,
            }, fake_key_moment=True)
            forward = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            reverse = create_match_group(member_published_ids=["published-two", "published-one"], metadata=_metadata())

            forward_report = generate_match_group_report(forward["group_id"])
            reverse_report = generate_match_group_report(reverse["group_id"])
            forward_moments = forward_report["key_moments"]["moments"]
            reverse_moments = reverse_report["key_moments"]["moments"]

            self.assertIn(722.5, [moment["time_sec"] for moment in forward_moments])
            self.assertIn(122.5, [moment["time_sec"] for moment in reverse_moments])
            self.assertNotIn("fake-physical", str(forward_report["key_moments"]))
            self.assertIn("key_moments", forward_report)
            self.assertEqual(forward_report["aggregate_semantic_digest"], generate_match_group_report(forward["group_id"])["aggregate_semantic_digest"])

    def test_conservative_readiness_and_spatial_are_never_upgraded(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two", possession_status="not_available")
            manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

            report = generate_match_group_report(manifest["group_id"])

            self.assertEqual(report["ball"]["possession"]["status"], "not_available")
            self.assertEqual(report["timelines"]["possession"]["status"], "not_available")
            self.assertEqual(report["spatial"]["heatmaps"]["status"], "not_available")
            self.assertEqual(report["spatial"]["team_shape"]["status"], "not_available")

    def test_momentum_preserves_experimental_readiness_and_weakest_quality(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", momentum_quality="medium", momentum_signal_quality="medium")
            _write_source(root, "published-two", "physical-two", momentum_quality="low", momentum_signal_quality="low")
            manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

            report = generate_match_group_report(manifest["group_id"])

            momentum = report["timelines"]["attacking_momentum"]
            self.assertEqual(momentum["status"], "completed")
            self.assertEqual(momentum["product_readiness"], "experimental")
            self.assertEqual(momentum["signal_quality"], "low")
            self.assertEqual(momentum["quality"], "low")
            self.assertEqual(report["stats_semantics"]["ball"], "experimental_candidates")

    def test_unavailable_momentum_and_zero_known_possession_never_fabricate_ready_metrics(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", controlled_corgi=0, controlled_verisk=0)
            _write_source(root, "published-two", "physical-two", momentum_status="not_available")
            manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

            report = generate_match_group_report(manifest["group_id"])

            zero_window = report["timelines"]["possession"]["windows"][0]
            self.assertEqual(zero_window["known_team_frames"], 0.0)
            self.assertIsNone(zero_window["possession_share_percent_by_team_id"]["team-corgi"])
            self.assertIsNone(zero_window["possession_share_percent_by_team_id"]["team-verisk"])
            self.assertEqual(report["timelines"]["attacking_momentum"]["status"], "not_available")
            self.assertEqual(report["timelines"]["attacking_momentum"]["product_readiness"], "experimental")

    def test_stale_or_tampered_member_fails_without_replacing_previous_report(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two")
            manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            generate_match_group_report(manifest["group_id"])
            report_path = root / "groups" / manifest["group_id"] / "public_report.json"
            before = report_path.read_bytes()
            aggregate_path = root / "published" / "published-two" / "aggregate_inputs.json"
            tampered = _read(aggregate_path)
            tampered["timing"]["analyzed_duration_sec"] = 99
            _write(aggregate_path, tampered)

            with self.assertRaisesRegex(MatchGroupError, "semantic digest"):
                generate_match_group_report(manifest["group_id"])
            self.assertEqual(report_path.read_bytes(), before)

    def test_failed_update_generation_restores_the_previous_manifest_and_report_bytes(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two")
            _write_source(root, "published-three", "physical-three")
            manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            generate_match_group_report(manifest["group_id"])
            group_dir = root / "groups" / manifest["group_id"]
            before_manifest = (group_dir / "manifest.json").read_bytes()
            before_report = (group_dir / "public_report.json").read_bytes()

            with self.assertRaisesRegex(MatchGroupError, "generation failed"):
                update_match_group_and_generate_report(
                    manifest["group_id"],
                    member_published_ids=["published-one", "published-three"],
                    metadata={**_metadata(), "title": "Changed"},
                    generate_report=lambda _: (_ for _ in ()).throw(MatchGroupError("generation_failed", "generation failed")),
                )

            self.assertEqual((group_dir / "manifest.json").read_bytes(), before_manifest)
            self.assertEqual((group_dir / "public_report.json").read_bytes(), before_report)

    def test_create_generation_oserror_removes_new_group_without_touching_physical_sources(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two")
            published_root = root / "published"
            source_bytes_before = {
                str(path.relative_to(published_root)): path.read_bytes()
                for path in published_root.rglob("*")
                if path.is_file()
            }

            with self.assertRaisesRegex(OSError, "disk full"):
                create_match_group_and_generate_report(
                    member_published_ids=["published-one", "published-two"],
                    metadata=_metadata(),
                    generate_report=lambda _group_id: (_ for _ in ()).throw(OSError("disk full")),
                )

            self.assertEqual(list((root / "groups").glob("match-group-*")), [])
            source_bytes_after = {
                str(path.relative_to(published_root)): path.read_bytes()
                for path in published_root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(source_bytes_after, source_bytes_before)

    def test_semantic_digest_changes_with_order_but_not_generated_at(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=10)
            _write_source(root, "published-two", "physical-two", duration=20)
            forward = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            reverse = create_match_group(member_published_ids=["published-two", "published-one"], metadata=_metadata())
            first = generate_match_group_report(forward["group_id"])
            second = generate_match_group_report(reverse["group_id"])
            self.assertNotEqual(first["aggregate_semantic_digest"], second["aggregate_semantic_digest"])

            public_path = root / "published" / "published-one" / "public_report.json"
            public = _read(public_path)
            public["generated_at"] = "2031-01-01T00:00:00+00:00"
            _write(public_path, public)
            again = generate_match_group_report(forward["group_id"])
            self.assertEqual(first["aggregate_semantic_digest"], again["aggregate_semantic_digest"])

    def test_generation_never_creates_a_physical_match_or_mutates_child_bytes(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two")
            before = {str(path.relative_to(root / "published")): path.read_bytes() for path in (root / "published").rglob("*") if path.is_file()}
            manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

            generate_match_group_report(manifest["group_id"])

            after = {str(path.relative_to(root / "published")): path.read_bytes() for path in (root / "published").rglob("*") if path.is_file() and "match-groups" not in str(path)}
            self.assertEqual(before, after)
            self.assertFalse((root / "matches").exists())

    def _store(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        patches = (
            patch("app.services.match_groups.PUBLISHED_MATCHES_DIR", root / "published"),
            patch("app.services.match_groups.MATCH_GROUPS_DIR", root / "groups"),
            patch("app.services.match_group_aggregation.PUBLISHED_MATCHES_DIR", root / "published"),
            patch("app.services.match_group_aggregation.MATCH_GROUPS_DIR", root / "groups"),
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


def _write_source(
    root: Path,
    published_id: str,
    source_match_id: str,
    *,
    duration: float = 10,
    labels: tuple[str, str] = ("A", "B"),
    player_distance: float = 100,
    movement_time: float = 10,
    peak: float = 20,
    attempts: float = 10,
    completed: float = 8,
    controlled_corgi: float = 60,
    controlled_verisk: float = 40,
    possession_status: str = "ready",
    momentum_status: str = "completed",
    momentum_quality: str = "medium",
    momentum_signal_quality: str = "medium",
    extra_players: list[tuple[str, str, str]] | None = None,
) -> None:
    directory = root / "published" / published_id
    directory.mkdir(parents=True, exist_ok=True)
    public = {
        "schema_version": PUBLIC_MATCH_REPORT_SCHEMA_VERSION,
        "report_type": PUBLIC_MATCH_REPORT_TYPE,
        "id": published_id,
        "source_match_id": source_match_id,
        "generated_at": "2026-09-01T00:00:00+00:00",
        "match": {"id": source_match_id, "title": source_match_id},
        "stats_semantics": {"ball": "experimental_candidates"},
        "teams": [
            {"team_id": "team-corgi", "team_name": "Corgi"},
            {"team_id": "team-verisk", "team_name": "Verisk"},
        ],
        "players": [
            {"player_id": player_id, "team_id": team_id, "player_name": name}
            for player_id, team_id, name in (extra_players or [("player-one", "team-corgi", "Alex")])
        ],
    }
    player_rows = []
    for player_id, team_id, _name in (extra_players or [("player-one", "team-corgi", "Alex")]):
        player_rows.append({
            "player_id": player_id,
            "team_id": team_id,
            "movement": {
                "total_distance_m": player_distance,
                "observed_distance_m": player_distance - 5,
                "estimated_short_gap_distance_m": 5,
                "movement_time_sec": movement_time,
                "detected_time_sec": movement_time,
                "high_intensity_distance_m": 10,
                "sprint_count": 1,
                "peak_speed_kmh": peak,
            },
        })
    counts = {
        "attempts": attempts,
        "completed": completed,
        "failed": attempts - completed,
        "restart_attempts": 1,
        "accepted": completed,
    }
    aggregate = {
        "schema_version": "1.0.0",
        "aggregation_policy_version": "1.0.0",
        "source": {
            "source_match_id": source_match_id,
            "published_id": published_id,
            "reviewed_identity_digest": f"reviewed-{source_match_id}",
            "public_report_semantic_digest": canonical_json_sha256(public),
        },
        "timing": {"analyzed_duration_sec": duration},
        "teams": [
            {"team_id": "team-corgi", "source_team_label": labels[0], "movement": {"total_distance_m": 100, "high_intensity_distance_m": 20, "sprint_count": 2, "peak_speed_kmh": peak}},
            {"team_id": "team-verisk", "source_team_label": labels[1], "movement": {"total_distance_m": 50, "high_intensity_distance_m": 10, "sprint_count": 1, "peak_speed_kmh": peak - 1}},
        ],
        "players": player_rows,
        "identity_coverage": {"status": "ready", "coverage_unit": "observations", "confirmed_observations": 15, "reliable_observations": 20, "unresolved_observations": 3, "conflicted_observations": 2},
        "ball": {
            "possession": {"status": possession_status, "controlled_frames_by_team_id": {"team-corgi": controlled_corgi, "team-verisk": controlled_verisk}, "known_frames": controlled_corgi + controlled_verisk, "free_frames": 0, "unknown_frames": 0},
            "passes": {"status": "ready", **counts, **{f"{field}_by_team_id": {"team-corgi": value, "team-verisk": 0} for field, value in counts.items()}},
        },
        "timelines": {
            "possession": {"status": possession_status, "windows": [{"start_time_sec": 0, "end_time_sec": duration, "controlled_frames_by_team_id": {"team-corgi": controlled_corgi, "team-verisk": controlled_verisk}}]},
            "attacking_momentum": {"status": momentum_status, "product_readiness": "experimental", "signal_quality": momentum_signal_quality, "quality": momentum_quality, "points": [{"start_time_sec": 0, "end_time_sec": duration, "team_values_by_team_id": {"team-corgi": 1, "team-verisk": 0}}]},
        },
        "spatial": {"orientation": "unproven", "heatmaps": {"status": "not_available"}, "team_shape": {"status": "not_available"}},
        "metric_readiness": {"team_movement": {"status": "ready"}, "player_movement": {"status": "ready"}, "possession": {"status": possession_status}, "passes": {"status": "ready"}},
    }
    aggregate["source"]["aggregation_input_semantic_digest"] = canonical_json_sha256(aggregate)
    _write(directory / "public_report.json", public)
    _write(directory / "aggregate_inputs.json", aggregate)


def _set_source_momentum(root: Path, published_id: str, point: dict[str, object], *, fake_key_moment: bool) -> None:
    directory = root / "published" / published_id
    public = _read(directory / "public_report.json")
    if fake_key_moment:
        public["key_moments"] = {"moments": [{"moment_id": "fake-physical", "time_sec": 5}]}
    aggregate = _read(directory / "aggregate_inputs.json")
    aggregate["timelines"]["attacking_momentum"]["points"] = [point]
    aggregate["source"]["public_report_semantic_digest"] = canonical_json_sha256(public)
    digest_document = copy.deepcopy(aggregate)
    digest_document["source"].pop("aggregation_input_semantic_digest", None)
    aggregate["source"]["aggregation_input_semantic_digest"] = canonical_json_sha256(digest_document)
    _write(directory / "public_report.json", public)
    _write(directory / "aggregate_inputs.json", aggregate)


def _metadata() -> dict[str, str]:
    return {"title": "Logical match", "match_date": "2026-09-01", "season": "2026", "venue": "Orlik", "format": "7v7"}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
