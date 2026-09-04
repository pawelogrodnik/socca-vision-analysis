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
            _write_source(root, "published-one", "physical-one", duration=600, team_distance=1000, peak=20, controlled_corgi=360, controlled_verisk=240, attempts=20, completed=16, player_distance=100)
            _write_source(root, "published-two", "physical-two", duration=300, team_distance=500, peak=30, controlled_corgi=120, controlled_verisk=180, attempts=10, completed=8, player_distance=50)
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
            self.assertEqual(report["match"]["duration_sec"], 900.0)
            self.assertEqual(report["match"]["title"], "Logical match")

            # Team aggregation: SUM distance, MAX peak, recomputed possession.
            corgi = next(row for row in report["teams"] if row["team_id"] == "team-corgi")
            self.assertEqual(corgi["total_distance_m"], 1500.0)
            self.assertEqual(corgi["peak_speed_kmh"], 30.0)
            # Controlled frames: one 360/240 + two 120/180 → 480/420 → 53.3%.
            self.assertAlmostEqual(corgi["possession_share_percent"], 53.3, places=1)
            self.assertEqual(report["teams"][0]["team_label"], "A")

            # Pass counts SUM, completion rate RECOMPUTED (not averaged).
            self.assertEqual(report["ball"]["pass_attempts"], 30)
            self.assertEqual(report["ball"]["completed_passes"], 24)
            self.assertEqual(report["ball"]["completion_rate"], 80.0)

            # One player row per stable player, times SUMMED.
            players = [row for row in report["players"] if row["player_id"] == "player-one"]
            self.assertEqual(len(players), 1)
            player = players[0]
            self.assertEqual(player["playing_time_sec"], 900.0)
            self.assertEqual(player["total_distance_m"], 150.0)
            self.assertEqual(player["peak_speed_kmh"], 30.0)
            # avg speed recomputed from merged primitives: 150m / 900s * 3.6.
            self.assertAlmostEqual(player["avg_speed_kmh"], 0.6, places=2)
            self.assertEqual(player["sprint_count"], 3)
            self.assertIn("player-one", [row["player_id"] for row in report["players"]])

            # Canonical possession timeline: rebased 0..600 + 600..900.
            timeline = report["ball"]["possession_timeline"]
            self.assertEqual([(row["start_time_sec"], row["end_time_sec"]) for row in timeline], [(0.0, 600.0), (600.0, 900.0)])
            self.assertEqual(timeline[-1]["cumulative_team_a_frames"], 480)
            self.assertEqual(timeline[-1]["cumulative_team_b_frames"], 420)

            # Canonical momentum timeline present in physical shape.
            momentum = report["ball"]["attacking_momentum"]
            self.assertTrue(momentum["experimental"])
            self.assertEqual(len(momentum["timeline"]), 2)
            self.assertEqual(momentum["timeline"][1]["start_time_sec"], 600.0)
            self.assertIn("signed_score", momentum["timeline"][0])

            # Heatmaps use merged samples through the canonical player field.
            self.assertTrue(player["heatmap"]["path"].startswith(f"published/matches/{merged_id}/heatmaps/"))
            self.assertEqual(player["heatmap"]["samples"], 8)
            self.assertIn("average_position", player["heatmap"])
            self.assertIsNotNone(player["heatmap"]["average_position"])
            heatmap_file = root / "published" / merged_id / "heatmaps" / Path(player["heatmap"]["path"]).name
            self.assertTrue(heatmap_file.is_file())
            mirror_file = root / "client-public" / merged_id / "heatmaps" / Path(player["heatmap"]["path"]).name
            self.assertTrue(mirror_file.is_file())

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

    def test_conflicting_player_team_fails_closed(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600)
            _write_source(root, "published-two", "physical-two", duration=300)
            _force_player_team(root, "published-two", "player-one", "team-verisk")
            with self.assertRaises(MatchGroupError):
                create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

    def test_swapped_local_labels_map_to_same_canonical_teams(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=600, labels=("A", "B"))
            _write_source(root, "published-two", "physical-two", duration=300, labels=("B", "A"))
            group = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            result = ensure_merged_published_match(str(group["group_id"]))
            report = result["report"]
            labels = {row["team_id"]: row["team_label"] for row in report["teams"]}
            self.assertEqual(labels, {"team-corgi": "A", "team-verisk": "B"})
            # Momentum dominant team follows the stable team, not the local label.
            for point in report["ball"]["attacking_momentum"]["timeline"]:
                self.assertEqual(point["dominant_team_label"], "A")

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
    attempts: int = 20,
    completed: int = 16,
    controlled_corgi: float = 360,
    controlled_verisk: float = 240,
) -> None:
    directory = root / "published" / published_id
    directory.mkdir(parents=True, exist_ok=True)
    corgi_label, verisk_label = labels
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
                "detected_time_sec": duration,
                "certain_playing_time_sec": duration,
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
                    "detected_time_sec": duration,
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
                            "detected_time_sec": min(300, duration),
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
            "reviewed_identity_digest": f"reviewed-{source_match_id}",
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
                    "movement_time_sec": duration,
                    "detected_time_sec": duration,
                    "high_intensity_distance_m": 10,
                    "sprint_count": 2 if duration >= 600 else 1,
                    "peak_speed_kmh": peak,
                },
            }
        ],
        "identity_coverage": {"status": "ready", "coverage_unit": "observations", "confirmed_observations": 15, "reliable_observations": 20, "unresolved_observations": 3, "conflicted_observations": 2},
        "ball": {
            "possession": {"status": "ready", "controlled_frames_by_team_id": {"team-corgi": controlled_corgi, "team-verisk": controlled_verisk}, "known_frames": controlled_corgi + controlled_verisk, "free_frames": 0, "unknown_frames": 0},
            "passes": {"status": "ready", "attempts": attempts, "completed": completed, "failed": attempts - completed, "restart_attempts": 1, "accepted": completed},
        },
        "timelines": {
            "possession": {"status": "ready", "windows": [{"start_time_sec": 0, "end_time_sec": duration, "controlled_frames_by_team_id": {"team-corgi": controlled_corgi, "team-verisk": controlled_verisk}, "free_frames": 0, "unknown_frames": 0}]},
            "attacking_momentum": {"status": "completed", "product_readiness": "experimental", "signal_quality": "medium", "quality": "medium", "points": [{"start_time_sec": 0, "end_time_sec": duration, "team_values_by_team_id": {"team-corgi": 60, "team-verisk": -20}, "dominant_team_id": "team-corgi", "confidence": 0.9, "intensity": 0.8}]},
        },
        "spatial": {"orientation": "unproven", "heatmaps": {"status": "not_available"}, "team_shape": {"status": "not_available"}, "pitch_dimensions_m": {"width_m": 30.0, "length_m": 50.0}},
        "metric_readiness": {"team_movement": {"status": "ready"}, "player_movement": {"status": "ready"}, "possession": {"status": "ready"}, "passes": {"status": "ready"}},
    }
    aggregate["source"]["aggregation_input_semantic_digest"] = canonical_json_sha256(aggregate)
    package = {
        "match": {"id": source_match_id, "title": source_match_id},
        "reviewed_player_heatmaps": {
            "pitch_dimensions_m": {"width_m": 30.0, "length_m": 50.0},
            "heatmaps": [{"player_id": "player-one", "positions_m": [[5.0 + index, 10.0 + index] for index in range(4)]}],
        },
    }
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


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
