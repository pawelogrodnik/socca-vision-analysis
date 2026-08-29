from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.aggregate_inputs import AggregateInputsError, build_aggregate_inputs
from app.services.artifact_lineage import canonical_json_sha256


class AggregateInputsTests(unittest.TestCase):
    def test_reviewed_source_emits_exact_primitives_and_stable_ids(self) -> None:
        inputs = build_aggregate_inputs(_package(), public_report=_public_report(), published_id="published-match-1")

        self.assertEqual(inputs["schema_version"], "1.0.0")
        self.assertEqual(inputs["source"]["source_match_id"], "match-1")
        self.assertEqual(inputs["source"]["published_id"], "published-match-1")
        self.assertEqual(inputs["source"]["reviewed_identity_digest"], "reviewed-digest")
        self.assertEqual(inputs["teams"], [
            {
                "team_id": "team-corgi",
                "source_team_label": "A",
                "movement": {
                    "total_distance_m": 110.0,
                    "high_intensity_distance_m": 20.0,
                    "sprint_count": 2,
                    "peak_speed_kmh": 23.0,
                    "average_speed": {
                        "status": "not_available",
                        "reason": "canonical_team_movement_time_missing",
                    },
                },
            },
            {
                "team_id": "team-verisk",
                "source_team_label": "B",
                "movement": {
                    "total_distance_m": 120.0,
                    "high_intensity_distance_m": 24.0,
                    "sprint_count": 3,
                    "peak_speed_kmh": 24.0,
                    "average_speed": {
                        "status": "not_available",
                        "reason": "canonical_team_movement_time_missing",
                    },
                },
            },
        ])
        player = inputs["players"][0]
        self.assertEqual(player["player_id"], "player-corgi-1")
        self.assertEqual(player["team_id"], "team-corgi")
        self.assertEqual(
            player["movement"],
            {
                "total_distance_m": 50.0,
                "observed_distance_m": 40.0,
                "estimated_short_gap_distance_m": 10.0,
                "detected_time_sec": 12.0,
                "movement_time_sec": 10.0,
                "high_intensity_distance_m": 8.0,
                "sprint_count": 1,
                "peak_speed_kmh": 21.0,
            },
        )
        self.assertNotIn("avg_speed_kmh", player["movement"])
        self.assertEqual(inputs["ball"]["possession"]["controlled_frames_by_team_id"], {"team-corgi": 12, "team-verisk": 18})
        self.assertEqual(inputs["ball"]["passes"]["attempts_by_team_id"], {"team-corgi": 4, "team-verisk": 6})
        self.assertEqual(inputs["ball"]["passes"]["restart_attempts_by_team_id"], {"team-corgi": 1, "team-verisk": 0})
        self.assertEqual(inputs["ball"]["passes"]["accepted_by_team_id"], {"team-corgi": 2, "team-verisk": 2})
        self.assertEqual(
            sum(inputs["ball"]["passes"]["restart_attempts_by_team_id"].values()),
            inputs["ball"]["passes"]["restart_attempts"],
        )
        self.assertEqual(
            sum(inputs["ball"]["passes"]["accepted_by_team_id"].values()),
            inputs["ball"]["passes"]["accepted"],
        )
        self.assertNotIn("completion_rate", json.dumps(inputs))
        self.assertNotIn("possession_share_percent", json.dumps(inputs))
        self.assertEqual(inputs["spatial"]["orientation"], "unproven")
        self.assertEqual(inputs["spatial"]["heatmaps"]["status"], "not_available")
        self.assertEqual(inputs["spatial"]["team_shape"]["status"], "not_available")

    def test_swapped_labels_map_counts_to_the_same_stable_teams(self) -> None:
        inputs = build_aggregate_inputs(
            _package(labels={"A": "team-verisk", "B": "team-corgi"}, swap_players=True),
            public_report=_public_report(),
            published_id="published-match-1",
        )

        by_team = {row["team_id"]: row for row in inputs["teams"]}
        self.assertEqual(by_team["team-verisk"]["source_team_label"], "A")
        self.assertEqual(by_team["team-corgi"]["source_team_label"], "B")
        self.assertEqual(inputs["ball"]["possession"]["controlled_frames_by_team_id"], {"team-corgi": 18, "team-verisk": 12})
        self.assertEqual(inputs["ball"]["passes"]["completed_by_team_id"], {"team-corgi": 3, "team-verisk": 2})
        self.assertEqual(inputs["ball"]["passes"]["restart_attempts_by_team_id"], {"team-corgi": 0, "team-verisk": 1})
        self.assertEqual(inputs["ball"]["passes"]["accepted_by_team_id"], {"team-corgi": 2, "team-verisk": 2})

    def test_missing_stable_mapping_fails_closed(self) -> None:
        package = _package()
        package["team_config"]["teams"] = []
        with self.assertRaisesRegex(AggregateInputsError, "cannot map local team label"):
            build_aggregate_inputs(package, public_report=_public_report(), published_id="published-match-1")

    def test_mismatched_reviewed_artifact_digest_fails_closed(self) -> None:
        package = _package()
        package["reviewed_player_heatmaps"]["source_snapshot_digest"] = "older-review"
        with self.assertRaisesRegex(AggregateInputsError, "not from the published Reviewed Identity generation"):
            build_aggregate_inputs(package, public_report=_public_report(), published_id="published-match-1")

    def test_missing_movement_denominator_is_unavailable_not_zero_filled(self) -> None:
        package = _package()
        package["reviewed_player_stats"]["players"][0].pop("movement_time_sec")
        inputs = build_aggregate_inputs(package, public_report=_public_report(), published_id="published-match-1")

        movement = inputs["players"][0]["movement"]
        self.assertNotIn("movement_time_sec", movement)
        self.assertEqual(movement["average_speed"]["status"], "not_available")
        self.assertNotIn("avg_speed_kmh", movement)

    def test_unavailable_ball_metrics_do_not_invent_zeroes(self) -> None:
        package = _package()
        package["possession_report"] = {"status": "missing"}
        package["pass_candidates"] = {"summary": {}}
        inputs = build_aggregate_inputs(package, public_report=_public_report(), published_id="published-match-1")

        self.assertEqual(inputs["ball"]["possession"]["status"], "not_available")
        self.assertNotIn("controlled_frames_by_team_id", inputs["ball"]["possession"])
        self.assertEqual(inputs["ball"]["passes"]["status"], "not_available")
        self.assertNotIn("attempts", inputs["ball"]["passes"])

    def test_passes_with_team_totals_but_no_candidate_rows_fail_closed(self) -> None:
        package = _package()
        package["pass_candidates"].pop("candidates")
        with self.assertRaisesRegex(AggregateInputsError, "pass_candidates.candidates is required"):
            build_aggregate_inputs(package, public_report=_public_report(), published_id="published-match-1")

    def test_semantic_digest_ignores_technical_timestamps_but_changes_for_primitives(self) -> None:
        package = _package()
        report = _public_report()
        first = build_aggregate_inputs(package, public_report=report, published_id="published-match-1")

        package["generated_at"] = "2030-01-01T00:00:00+00:00"
        package["reviewed_player_stats"]["generated_at"] = "2030-01-01T00:00:00+00:00"
        report["generated_at"] = "2030-01-01T00:00:00+00:00"
        same = build_aggregate_inputs(package, public_report=report, published_id="published-match-1")
        self.assertEqual(
            first["source"]["aggregation_input_semantic_digest"],
            same["source"]["aggregation_input_semantic_digest"],
        )

        package["reviewed_player_stats"]["players"][0]["total_distance_m"] = 50.5
        changed = build_aggregate_inputs(package, public_report=report, published_id="published-match-1")
        self.assertNotEqual(
            first["source"]["aggregation_input_semantic_digest"],
            changed["source"]["aggregation_input_semantic_digest"],
        )

    def test_input_digest_is_calculated_without_its_own_field(self) -> None:
        inputs = build_aggregate_inputs(_package(), public_report=_public_report(), published_id="published-match-1")
        digest = inputs["source"].pop("aggregation_input_semantic_digest")
        self.assertEqual(digest, canonical_json_sha256(inputs))

    def test_contract_has_no_raw_review_or_trajectory_data(self) -> None:
        package = _package()
        package["tracklets"] = {"tracklets": [{"tracklet_id": "secret", "positions_m": [[1, 2]]}]}
        package["reviewed_identity_snapshot"] = {"tracklet_assignments": [{"tracklet_id": "secret"}]}
        inputs = build_aggregate_inputs(package, public_report=_public_report(), published_id="published-match-1")

        serialized = json.dumps(inputs, sort_keys=True)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("positions_m", serialized)
        self.assertNotIn("trajectory", serialized)

    def test_publish_writes_server_only_input_and_successful_replace_swaps_one_complete_generation(self) -> None:
        from app.services import json_publish_store
        from app.services.json_publish_store import import_match_package
        from app.services.public_match_report import build_public_match_report

        package = _package()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch.object(json_publish_store, "PUBLISHED_MATCHES_DIR", root / "published"),
                patch("app.services.json_publish_store.MATCHES_DIR", root / "source-matches"),
                patch("app.services.public_match_report.CLIENT_PUBLIC_MATCHES_DIR", root / "public-mirror"),
            ):
                expected_public_report = build_public_match_report(
                    package,
                    published_id="published-match-1",
                    source_match_dir=None,
                    heatmap_dir=None,
                    public_heatmap_base="published/matches/published-match-1/heatmaps",
                )
                first = import_match_package(package)
                published_root = root / "published" / "published-match-1"
                mirror_root = root / "public-mirror" / "published-match-1"
                artifact = published_root / "aggregate_inputs.json"
                first_input = json.loads(artifact.read_text(encoding="utf-8"))
                self.assertNotIn("aggregate_inputs", first)
                self.assertEqual(
                    canonical_json_sha256(first["public_report"]),
                    canonical_json_sha256(expected_public_report),
                )
                self.assertFalse((root / "public-mirror" / "published-match-1" / "aggregate_inputs.json").exists())
                self.assertFalse(artifact.with_suffix(".json.tmp").exists())
                self.assertFalse((root / "source-matches" / "match-1" / "aggregate_inputs.json").exists())
                before = _artifact_bytes(published_root, mirror_root)

                package["match"]["title"] = "Updated reviewed match"
                package["reviewed_player_stats"]["players"][0]["total_distance_m"] = 51.0
                second = import_match_package(package, replace=True)
                second_input = json.loads(artifact.read_text(encoding="utf-8"))
                self.assertEqual(second["public_report"]["report_type"], "public_match_report")
                self.assertNotEqual(
                    first_input["source"]["aggregation_input_semantic_digest"],
                    second_input["source"]["aggregation_input_semantic_digest"],
                )
                self.assertEqual(
                    second_input["source"]["public_report_semantic_digest"],
                    canonical_json_sha256(second["public_report"]),
                )
                after = _artifact_bytes(published_root, mirror_root)
                self.assertEqual(set(before), set(after))
                for path in before:
                    self.assertNotEqual(before[path], after[path], path)
                self.assertFalse(artifact.with_suffix(".json.tmp").exists())
                _assert_no_publish_staging_artifacts(root)

    def test_invalid_first_reviewed_publish_leaves_no_authoritative_generation(self) -> None:
        from app.services import json_publish_store
        from app.services.json_publish_store import import_match_package

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch.object(json_publish_store, "PUBLISHED_MATCHES_DIR", root / "published"),
                patch("app.services.json_publish_store.MATCHES_DIR", root / "source-matches"),
                patch("app.services.public_match_report.CLIENT_PUBLIC_MATCHES_DIR", root / "public-mirror"),
                patch(
                    "app.services.json_publish_store.build_aggregate_inputs",
                    side_effect=AggregateInputsError("invalid reviewed generation"),
                ),
            ):
                with self.assertRaisesRegex(AggregateInputsError, "invalid reviewed generation"):
                    import_match_package(_package())

                self.assertFalse((root / "published" / "published-match-1").exists())
                self.assertFalse((root / "public-mirror" / "published-match-1").exists())
                _assert_no_publish_staging_artifacts(root)

    def test_failed_replacement_preserves_every_previous_private_and_public_artifact(self) -> None:
        from app.services import json_publish_store
        from app.services.json_publish_store import import_match_package

        package = _package()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch.object(json_publish_store, "PUBLISHED_MATCHES_DIR", root / "published"),
                patch("app.services.json_publish_store.MATCHES_DIR", root / "source-matches"),
                patch("app.services.public_match_report.CLIENT_PUBLIC_MATCHES_DIR", root / "public-mirror"),
            ):
                import_match_package(package)
                published_root = root / "published" / "published-match-1"
                mirror_root = root / "public-mirror" / "published-match-1"
                before = _artifact_bytes(published_root, mirror_root)

                package["match"]["title"] = "Broken replacement"
                package["reviewed_player_stats"]["players"][0]["total_distance_m"] = 51.0
                with patch(
                    "app.services.json_publish_store.build_aggregate_inputs",
                    side_effect=AggregateInputsError("replacement generation invalid"),
                ):
                    with self.assertRaisesRegex(AggregateInputsError, "replacement generation invalid"):
                        import_match_package(package, replace=True)

                self.assertEqual(before, _artifact_bytes(published_root, mirror_root))
                _assert_no_publish_staging_artifacts(root)


def _public_report() -> dict:
    return {
        "schema_version": "0.1.0",
        "generated_at": "2026-08-29T10:00:00+00:00",
        "id": "published-match-1",
        "source_match_id": "match-1",
        "report_type": "public_match_report",
    }


def _artifact_bytes(published_root: Path, mirror_root: Path) -> dict[str, bytes]:
    roots = (("published", published_root), ("mirror", mirror_root))
    return {
        f"{name}/{path.relative_to(root)}": path.read_bytes()
        for name, root in roots
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _assert_no_publish_staging_artifacts(root: Path) -> None:
    staging_root = root / ".staging"
    assert not staging_root.exists()
    assert not list(root.rglob("*.tmp"))
    assert not list(root.rglob("*.backup-*"))


def _package(*, labels: dict[str, str] | None = None, swap_players: bool = False) -> dict:
    labels = labels or {"A": "team-corgi", "B": "team-verisk"}
    team_for_corgi = next(label for label, team_id in labels.items() if team_id == "team-corgi")
    team_for_verisk = next(label for label, team_id in labels.items() if team_id == "team-verisk")
    if swap_players:
        player_rows = [
            _player("player-verisk-1", team_for_verisk, 60.0, 48.0, 12.0, 14.0, 12.0, 9.0, 2, 22.0),
            _player("player-corgi-1", team_for_corgi, 50.0, 40.0, 10.0, 12.0, 10.0, 8.0, 1, 21.0),
        ]
    else:
        player_rows = [
            _player("player-corgi-1", team_for_corgi, 50.0, 40.0, 10.0, 12.0, 10.0, 8.0, 1, 21.0),
            _player("player-verisk-1", team_for_verisk, 60.0, 48.0, 12.0, 14.0, 12.0, 9.0, 2, 22.0),
        ]
    return {
        "identity_report_source": "reviewed_identity",
        "reviewed_identity_digest": "reviewed-digest",
        "match": {
            "id": "match-1",
            "video": {"duration_sec": 40.0, "fps": 25.0, "frame_count": 1000},
            "teams": [
                {"id": "team-corgi", "name": "Corgi", "players": [{"id": "player-corgi-1", "name": "Corgi player"}]},
                {"id": "team-verisk", "name": "Verisk", "players": [{"id": "player-verisk-1", "name": "Verisk player"}]},
            ],
        },
        "team_config": {"teams": [{"team_label": label, "team_id": team_id} for label, team_id in labels.items()]},
        "team_stats": {
            "source": "conservative_identity_v2",
            "teams": [
                _team_row("A", labels["A"], 110.0, 20.0, 2, 23.0),
                _team_row("B", labels["B"], 120.0, 24.0, 3, 24.0),
            ],
        },
        "reviewed_player_stats": {
            "source_snapshot_digest": "reviewed-digest",
            "generated_at": "2026-08-29T10:00:00+00:00",
            "video_timing": {"duration_sec": 40.0, "fps": 25.0, "frame_count": 1000},
            "players": player_rows,
            "identity_coverage": {
                "coverage_unit": "unique_detected_tracklet_frame_observation",
                "confirmed_observations": 50,
                "reliable_player_observations_total": 100,
                "unresolved_observations": 30,
                "conflicted_observations": 10,
                "ignored_observations": 2,
            },
        },
        "reviewed_player_heatmaps": {
            "source_snapshot_digest": "reviewed-digest",
            "pitch_dimensions_m": {"width_m": 30.0, "length_m": 47.4},
        },
        "reviewed_stats_readiness": {
            "source_snapshot_digest": "reviewed-digest",
            "status": "completed",
            "possession": {"status": "not_available"},
            "passes": {"status": "not_available"},
        },
        "reviewed_output_manifest": {
            "reviewed_identity": {"digest": "reviewed-digest", "status": "fresh"},
            "stats": {"source_snapshot_digest": "reviewed-digest", "status": "completed"},
            "stale": False,
        },
        "pitch_config": {"width_m": 30.0, "length_m": 47.4},
        "analytics_readiness": {
            "features": {
                "possession": {"status": "fresh"},
                "passes": {"status": "fresh"},
                "momentum": {"status": "fresh"},
            }
        },
        "possession_report": {
            "status": "completed",
            "summary": {
                "team_controlled_frames": {"A": 12, "B": 18},
                "known_possession_frames": 35,
                "free_frames": 4,
                "unknown_frames": 1,
                "contested_frames": 2,
                "processed_frames": 1000,
            },
            "possession_timeline": [
                {
                    "start_time_sec": 0.0,
                    "end_time_sec": 20.0,
                    "team_controlled_frames": {"A": 6, "B": 9},
                    "free_frames": 3,
                    "unknown_frames": 1,
                }
            ],
        },
        "pass_candidates": {
            "summary": {
                "team_pass_attempts": {"A": 4, "B": 6},
                "team_completed_passes": {"A": 2, "B": 3},
                "team_failed_passes": {"A": 2, "B": 3},
                "pass_attempts": 10,
                "completed_passes": 5,
                "failed_passes": 5,
                "restart_pass_attempts": 1,
                "final_stat_passes": 4,
                "completion_rate": 0.5,
            },
            "candidates": _pass_candidates(),
        },
        "attacking_momentum": {
            "status": "completed",
            "product_readiness": "experimental",
            "signal_quality": "medium",
            "quality": "medium",
            "points": [
                {
                    "start_time_sec": 0.0,
                    "end_time_sec": 5.0,
                    "team_a_value": 2.0,
                    "team_b_value": -2.0,
                    "dominant_team_label": "A",
                    "confidence": 0.4,
                }
            ],
        },
    }


def _team_row(label: str, team_id: str, distance: float, high_intensity: float, sprint_count: int, peak: float) -> dict:
    return {
        "team_label": label,
        "team_id": team_id,
        "total_distance_m": distance,
        "high_intensity_distance_m": high_intensity,
        "sprint_count": sprint_count,
        "peak_sustained_speed_kmh": peak,
    }


def _pass_candidates() -> list[dict]:
    return [
        {
            "count_for_team_label": "A",
            "pass_type": "same_team_pass",
            "outcome": "completed_pass",
            "completed": True,
            "from_restart": True,
            "final_stat_eligible": True,
        },
        {
            "count_for_team_label": "A",
            "pass_type": "same_team_pass",
            "outcome": "completed_pass",
            "completed": True,
            "review_status": "accepted",
        },
        {"count_for_team_label": "A", "pass_type": "same_team_pass", "outcome": "failed_pass", "failed": True},
        {"count_for_team_label": "A", "pass_type": "same_team_pass", "outcome": "failed_pass", "failed": True},
        {
            "count_for_team_label": "B",
            "pass_type": "same_team_pass",
            "outcome": "completed_pass",
            "completed": True,
            "final_stat_eligible": True,
        },
        {
            "count_for_team_label": "B",
            "pass_type": "same_team_pass",
            "outcome": "completed_pass",
            "completed": True,
            "review_status": "accepted",
        },
        {"count_for_team_label": "B", "pass_type": "same_team_pass", "outcome": "completed_pass", "completed": True},
        {"count_for_team_label": "B", "pass_type": "same_team_pass", "outcome": "failed_pass", "failed": True},
        {"count_for_team_label": "B", "pass_type": "same_team_pass", "outcome": "failed_pass", "failed": True},
        {"count_for_team_label": "B", "pass_type": "same_team_pass", "outcome": "failed_pass", "failed": True},
    ]


def _player(
    player_id: str,
    label: str,
    total: float,
    observed: float,
    estimated: float,
    detected: float,
    movement: float,
    high_intensity: float,
    sprints: int,
    peak: float,
) -> dict:
    return {
        "player_id": player_id,
        "team_label": label,
        "total_distance_m": total,
        "observed_distance_m": observed,
        "estimated_short_gap_distance_m": estimated,
        "detected_time_sec": detected,
        "movement_time_sec": movement,
        "confirmed_detected_observations": 25,
        "intensity": {"high_intensity_distance_m": high_intensity, "sprint_count": sprints},
        "speed": {"peak_sustained_speed_kmh": peak, "avg_speed_kmh": 18.0},
    }


if __name__ == "__main__":
    unittest.main()
