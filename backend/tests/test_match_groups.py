from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.artifact_lineage import canonical_json_sha256
from app.services.public_match_report import PUBLIC_MATCH_REPORT_SCHEMA_VERSION, PUBLIC_MATCH_REPORT_TYPE
from app.services.match_groups import (
    MatchGroupError,
    create_match_group,
    delete_match_group,
    get_match_group,
    update_match_group,
    validate_match_group,
)
from app.services.player_profiles import build_player_profile_stats
from app.services.team_profiles import build_team_profile_stats


class MatchGroupStoreTests(unittest.TestCase):
    def test_creates_valid_two_fragment_group_with_pinned_generations(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=120.0)
            _write_source(root, "published-two", "physical-two", duration=180.0)

            manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

            self.assertTrue(manifest["group_id"].startswith("match-group-"))
            self.assertEqual([member["sequence_index"] for member in manifest["members"]], [0, 1])
            self.assertEqual([member["logical_start_sec"] for member in manifest["members"]], [0.0, 120.0])
            self.assertEqual([member["logical_end_sec"] for member in manifest["members"]], [120.0, 300.0])
            self.assertEqual(manifest["timing"]["analyzed_duration_sec"], 300.0)
            self.assertEqual(manifest["timing"]["timeline_span_sec"], 300.0)
            self.assertEqual(manifest["compatibility"]["status"], "compatible")
            self.assertEqual(manifest["members"][0]["public_report_schema_version"], PUBLIC_MATCH_REPORT_SCHEMA_VERSION)
            self.assertEqual(validate_match_group(manifest["group_id"])["status"], "compatible")
            self.assertTrue((root / "groups" / manifest["group_id"] / "manifest.json").is_file())

    def test_three_fragment_offsets_are_cumulative_and_order_is_caller_owned(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", duration=10.0)
            _write_source(root, "published-two", "physical-two", duration=20.0)
            _write_source(root, "published-three", "physical-three", duration=30.0)

            manifest = create_match_group(
                member_published_ids=["published-three", "published-one", "published-two"], metadata=_metadata()
            )

            self.assertEqual(
                [(member["published_id"], member["logical_start_sec"], member["logical_end_sec"]) for member in manifest["members"]],
                [("published-three", 0.0, 30.0), ("published-one", 30.0, 40.0), ("published-two", 40.0, 60.0)],
            )

    def test_swapped_source_labels_with_same_stable_team_ids_pass(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", labels=("A", "B"))
            _write_source(root, "published-two", "physical-two", labels=("B", "A"))

            manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

            self.assertEqual(manifest["compatibility"]["team_ids"], ["team-corgi", "team-verisk"])

    def test_different_team_sets_and_cross_member_player_teams_fail_closed(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-other", "physical-other", teams=("team-corgi", "team-other"))
            with self.assertRaisesRegex(MatchGroupError, "Stable team_id set differs"):
                create_match_group(member_published_ids=["published-one", "published-other"], metadata=_metadata())

            _write_source(root, "published-two", "physical-two", players=[("player-corgi", "team-verisk")])
            with self.assertRaisesRegex(MatchGroupError, "maps to different stable team_ids"):
                create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

    def test_player_present_in_only_one_source_is_valid(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", players=[("player-corgi", "team-corgi")])
            _write_source(root, "published-two", "physical-two", players=[("player-verisk", "team-verisk")])

            self.assertEqual(
                create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())["compatibility"]["status"],
                "compatible",
            )

    def test_duplicate_published_or_physical_sources_fail_closed(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-one")
            with self.assertRaisesRegex(MatchGroupError, "published source cannot appear more than once"):
                create_match_group(member_published_ids=["published-one", "published-one"], metadata=_metadata())
            with self.assertRaisesRegex(MatchGroupError, "physical source_match_id cannot appear more than once"):
                create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

    def test_missing_or_unsupported_or_tampered_source_contract_fails_closed(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            (root / "published" / "published-two").mkdir(parents=True)
            with self.assertRaisesRegex(MatchGroupError, "no aggregate_inputs.json"):
                create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

            _write_source(root, "published-two", "physical-two", schema_version="9.0.0")
            with self.assertRaisesRegex(MatchGroupError, "not supported"):
                create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

            _write_source(root, "published-two", "physical-two")
            aggregate_path = root / "published" / "published-two" / "aggregate_inputs.json"
            aggregate = _read(aggregate_path)
            aggregate["timing"]["analyzed_duration_sec"] = 999.0
            _write(aggregate_path, aggregate)
            with self.assertRaisesRegex(MatchGroupError, "semantic digest does not match"):
                create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

    def test_public_report_digest_mismatch_fails_closed(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two")
            public_path = root / "published" / "published-two" / "public_report.json"
            public = _read(public_path)
            public["match"]["title"] = "tampered"
            _write(public_path, public)

            with self.assertRaisesRegex(MatchGroupError, "does not match the digest"):
                create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

    def test_public_report_schema_and_type_are_pinned_and_fail_closed(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two", public_schema_version="9.0.0")
            with self.assertRaises(MatchGroupError) as unsupported_schema:
                create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            self.assertEqual(unsupported_schema.exception.code, "unsupported_public_report_schema")

            _write_source(root, "published-two", "physical-two", report_type="public_aggregate_match_report")
            with self.assertRaises(MatchGroupError) as unsupported_type:
                create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            self.assertEqual(unsupported_type.exception.code, "unsupported_public_report_type")

            _write_source(root, "published-two", "physical-two", report_type=None)
            with self.assertRaises(MatchGroupError) as missing_type:
                create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            self.assertEqual(missing_type.exception.code, "required_source_field_missing")

    def test_republished_supported_public_report_becomes_stale_without_repinning(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two", public_title="Original")
            manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            original_pin = manifest["members"][1]["public_report_semantic_digest"]

            _write_source(root, "published-two", "physical-two", public_title="Republished")
            validation = validate_match_group(manifest["group_id"])

            self.assertEqual(validation["status"], "stale")
            self.assertEqual(validation["blocking_reasons"][0]["code"], "source_generation_changed")
            self.assertEqual(get_match_group(manifest["group_id"])["members"][1]["public_report_semantic_digest"], original_pin)

    def test_manifest_requires_pinned_public_report_schema_version(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two")
            manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            manifest_path = root / "groups" / manifest["group_id"] / "manifest.json"
            tampered = _read(manifest_path)
            tampered["members"][0].pop("public_report_schema_version")
            tampered["aggregate_semantic_digest"] = _self_excluding_digest(tampered, "aggregate_semantic_digest")
            _write(manifest_path, tampered)

            validation = validate_match_group(manifest["group_id"])
            self.assertEqual(validation["status"], "invalid")
            self.assertEqual(validation["blocking_reasons"][0]["code"], "required_source_field_missing")

    def test_validate_detects_missing_and_republished_sources_without_repinning(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two")
            manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            original_pin = manifest["members"][1]["aggregation_input_semantic_digest"]

            _write_source(root, "published-two", "physical-two", duration=121.0)
            stale = validate_match_group(manifest["group_id"])
            self.assertEqual(stale["status"], "stale")
            self.assertEqual(stale["blocking_reasons"][0]["code"], "source_generation_changed")
            self.assertEqual(get_match_group(manifest["group_id"])["members"][1]["aggregation_input_semantic_digest"], original_pin)

            shutil.rmtree(root / "published" / "published-two")
            missing = validate_match_group(manifest["group_id"])
            self.assertEqual(missing["status"], "stale")
            self.assertEqual(missing["blocking_reasons"][0]["code"], "published_source_missing")

    def test_validate_marks_a_later_unsupported_source_contract_incompatible(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two")
            manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

            _write_source(root, "published-two", "physical-two", schema_version="2.0.0")
            validation = validate_match_group(manifest["group_id"])

            self.assertEqual(validation["status"], "incompatible")
            self.assertEqual(validation["blocking_reasons"][0]["code"], "unsupported_aggregate_input_schema")

    def test_tampered_contract_fields_are_invalid_before_compatibility_is_considered(self) -> None:
        cases = (
            ("aggregate_inputs.json", "schema_version", "9.0.0", "aggregation_input_digest_mismatch"),
            ("aggregate_inputs.json", "aggregation_policy_version", "9.0.0", "aggregation_input_digest_mismatch"),
            ("public_report.json", "schema_version", "9.0.0", "public_report_digest_mismatch"),
            ("public_report.json", "report_type", "public_aggregate_match_report", "public_report_digest_mismatch"),
        )
        for filename, field, replacement, expected_code in cases:
            with self.subTest(filename=filename, field=field), self._store() as root:
                _write_source(root, "published-one", "physical-one")
                _write_source(root, "published-two", "physical-two")
                manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
                source_path = root / "published" / "published-two" / filename
                tampered = _read(source_path)
                tampered[field] = replacement
                _write(source_path, tampered)

                validation = validate_match_group(manifest["group_id"])

                self.assertEqual(validation["status"], "invalid")
                self.assertIn(expected_code, {reason["code"] for reason in validation["blocking_reasons"]})

    def test_self_consistent_unsupported_contracts_remain_incompatible(self) -> None:
        cases = (
            {"schema_version": "9.0.0"},
            {"aggregation_policy_version": "9.0.0"},
            {"public_schema_version": "9.0.0"},
            {"report_type": "public_aggregate_match_report"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides), self._store() as root:
                _write_source(root, "published-one", "physical-one")
                _write_source(root, "published-two", "physical-two")
                manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())

                _write_source(root, "published-two", "physical-two", **overrides)
                validation = validate_match_group(manifest["group_id"])

                self.assertEqual(validation["status"], "incompatible")
                self.assertIn(
                    next(iter(validation["blocking_reasons"]))["code"],
                    {
                        "unsupported_aggregate_input_schema",
                        "unsupported_aggregation_policy",
                        "unsupported_public_report_schema",
                        "unsupported_public_report_type",
                    },
                )

    def test_validation_status_precedence_is_independent_of_member_order(self) -> None:
        with self._store() as root:
            _write_source(root, "published-invalid", "physical-invalid")
            _write_source(root, "published-missing", "physical-missing")
            _write_source(root, "published-valid", "physical-valid")
            first = create_match_group(
                member_published_ids=["published-invalid", "published-missing", "published-valid"], metadata=_metadata()
            )
            reversed_order = create_match_group(
                member_published_ids=["published-missing", "published-invalid", "published-valid"], metadata=_metadata()
            )
            invalid_path = root / "published" / "published-invalid" / "aggregate_inputs.json"
            invalid = _read(invalid_path)
            invalid["timing"]["analyzed_duration_sec"] = 999.0
            _write(invalid_path, invalid)
            shutil.rmtree(root / "published" / "published-missing")

            first_validation = validate_match_group(first["group_id"])
            reversed_validation = validate_match_group(reversed_order["group_id"])

            self.assertEqual(first_validation["status"], "invalid")
            self.assertEqual(reversed_validation["status"], "invalid")
            expected_codes = {"aggregation_input_digest_mismatch", "published_source_missing"}
            self.assertEqual({reason["code"] for reason in first_validation["blocking_reasons"]}, expected_codes)
            self.assertEqual({reason["code"] for reason in reversed_validation["blocking_reasons"]}, expected_codes)

    def test_order_changes_digest_while_technical_source_timestamps_do_not(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two")
            manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            original_digest = manifest["aggregate_semantic_digest"]

            updated = update_match_group(
                manifest["group_id"], member_published_ids=["published-two", "published-one"], metadata=_metadata()
            )
            self.assertNotEqual(updated["aggregate_semantic_digest"], original_digest)

            _set_technical_timestamp(root / "published" / "published-one" / "aggregate_inputs.json")
            _set_technical_timestamp(root / "published" / "published-one" / "public_report.json")
            self.assertEqual(validate_match_group(updated["group_id"])["status"], "compatible")

    def test_spatial_limitations_are_capabilities_not_core_blockers(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one", pitch=(30.0, 47.4))
            _write_source(root, "published-two", "physical-two", pitch=(30.0, 47.4))
            manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            self.assertEqual(manifest["compatibility"]["status"], "compatible")
            self.assertEqual(manifest["compatibility"]["capabilities"]["spatial"]["status"], "not_available")

            _write_source(root, "published-two", "physical-two", pitch=(32.0, 47.4))
            changed = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            self.assertEqual(changed["compatibility"]["status"], "compatible")
            self.assertEqual(changed["compatibility"]["capabilities"]["spatial"], {"status": "incompatible", "reason": "pitch_dimensions_mismatch"})

    def test_capabilities_remain_granular_and_report_partial_coverage(self) -> None:
        with self._store() as root:
            _write_source(
                root,
                "published-one",
                "physical-one",
                team_movement_status="not_available",
                player_movement_status="available",
                possession_timeline_status="completed",
                momentum_status="not_available",
            )
            _write_source(
                root,
                "published-two",
                "physical-two",
                team_movement_status="not_available",
                player_movement_status="available",
                possession_timeline_status="not_available",
                momentum_status="not_available",
            )

            capabilities = create_match_group(
                member_published_ids=["published-one", "published-two"], metadata=_metadata()
            )["compatibility"]["capabilities"]

            self.assertEqual(capabilities["movement"]["team"]["status"], "not_available")
            self.assertEqual(capabilities["movement"]["player"]["status"], "available")
            self.assertEqual(capabilities["timelines"]["possession"]["status"], "partial")
            self.assertEqual(capabilities["timelines"]["attacking_momentum"]["status"], "not_available")

    def test_update_failure_is_atomic_and_caller_cannot_inject_pins_or_statistics(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two")
            manifest = create_match_group(
                member_published_ids=["published-one", "published-two"],
                metadata={**_metadata(), "aggregate_statistics": {"forged": True}, "source_digest": "forged"},
            )
            manifest_path = root / "groups" / manifest["group_id"] / "manifest.json"
            before = manifest_path.read_bytes()
            self.assertEqual(set(manifest["metadata"]), {"title", "match_date", "season", "venue", "format"})

            with self.assertRaises(MatchGroupError):
                update_match_group(
                    manifest["group_id"], member_published_ids=["published-one", "published-missing"], metadata=_metadata()
                )
            self.assertEqual(before, manifest_path.read_bytes())
            self.assertFalse(list(root.rglob("*.tmp")))
            self.assertFalse((root / "groups" / ".staging").exists())

    def test_delete_leaves_sources_and_physical_longitudinal_profiles_unchanged(self) -> None:
        with self._store() as root:
            _write_source(root, "published-one", "physical-one")
            _write_source(root, "published-two", "physical-two")
            matches_dir = root / "matches"
            _write_physical_match(matches_dir)
            registry = [{"id": "team-corgi", "name": "Corgi", "players": [{"id": "player-corgi", "name": "Pawel"}]}]
            before_player = build_player_profile_stats(matches_dir, "player-corgi", registry_teams=registry)["summary"]
            before_team = build_team_profile_stats(matches_dir, "team-corgi", registry_teams=registry)["summary"]
            source_before = _artifact_bytes(root / "published")

            manifest = create_match_group(member_published_ids=["published-one", "published-two"], metadata=_metadata())
            self.assertEqual(before_player, build_player_profile_stats(matches_dir, "player-corgi", registry_teams=registry)["summary"])
            self.assertEqual(before_team, build_team_profile_stats(matches_dir, "team-corgi", registry_teams=registry)["summary"])
            self.assertEqual(source_before, _artifact_bytes(root / "published"))
            delete_match_group(manifest["group_id"])

            after_player = build_player_profile_stats(matches_dir, "player-corgi", registry_teams=registry)["summary"]
            after_team = build_team_profile_stats(matches_dir, "team-corgi", registry_teams=registry)["summary"]
            self.assertEqual(before_player, after_player)
            self.assertEqual(before_team, after_team)
            self.assertEqual(source_before, _artifact_bytes(root / "published"))
            self.assertFalse((root / "groups" / manifest["group_id"]).exists())

    def _store(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        patches = (
            patch("app.services.match_groups.PUBLISHED_MATCHES_DIR", root / "published"),
            patch("app.services.match_groups.MATCH_GROUPS_DIR", root / "groups"),
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


def _metadata() -> dict[str, str]:
    return {"title": "One logical match", "match_date": "2026-08-29", "season": "2026", "venue": "Orlik", "format": "7v7"}


def _write_source(
    root: Path,
    published_id: str,
    source_match_id: str,
    *,
    duration: float = 120.0,
    teams: tuple[str, str] = ("team-corgi", "team-verisk"),
    labels: tuple[str, str] = ("A", "B"),
    players: list[tuple[str, str]] | None = None,
    schema_version: str = "1.0.0",
    aggregation_policy_version: str = "1.0.0",
    public_schema_version: str = PUBLIC_MATCH_REPORT_SCHEMA_VERSION,
    report_type: str | None = PUBLIC_MATCH_REPORT_TYPE,
    public_title: str | None = None,
    pitch: tuple[float, float] = (30.0, 47.4),
    team_movement_status: str = "available",
    player_movement_status: str = "available",
    possession_timeline_status: str = "completed",
    momentum_status: str = "completed",
) -> None:
    source_dir = root / "published" / published_id
    if source_dir.exists():
        shutil.rmtree(source_dir)
    source_dir.mkdir(parents=True)
    public_report = {
        "schema_version": public_schema_version,
        "generated_at": "2026-08-29T10:00:00+00:00",
        "id": published_id,
        "source_match_id": source_match_id,
        "match": {"id": source_match_id, "title": public_title or source_match_id},
    }
    if report_type is not None:
        public_report["report_type"] = report_type
    player_rows = players if players is not None else [("player-corgi", teams[0])]
    aggregate = {
        "schema_version": schema_version,
        "aggregation_policy_version": aggregation_policy_version,
        "source": {
            "source_match_id": source_match_id,
            "published_id": published_id,
            "reviewed_identity_digest": f"reviewed-{source_match_id}",
            "public_report_semantic_digest": canonical_json_sha256(public_report),
        },
        "timing": {"analyzed_duration_sec": duration, "fps": 25.0, "frame_count": int(duration * 25)},
        "teams": [
            {"team_id": teams[0], "source_team_label": labels[0], "movement": {"total_distance_m": 10.0}},
            {"team_id": teams[1], "source_team_label": labels[1], "movement": {"total_distance_m": 12.0}},
        ],
        "players": [
            {"player_id": player_id, "team_id": team_id, "movement": {"total_distance_m": 5.0}}
            for player_id, team_id in player_rows
        ],
        "identity_coverage": {"status": "completed", "confirmed_observations": 2},
        "ball": {"possession": {"status": "completed"}, "passes": {"status": "completed"}},
        "timelines": {
            "possession": {"status": possession_timeline_status, "windows": []},
            "attacking_momentum": {"status": momentum_status, "points": []},
        },
        "spatial": {
            "orientation": "unproven",
            "pitch_dimensions_m": {"width_m": pitch[0], "length_m": pitch[1]},
            "heatmaps": {"status": "not_available", "reason": "canonical_orientation_not_proven"},
            "team_shape": {"status": "not_available"},
        },
        "metric_readiness": {
            "team_movement": {"status": team_movement_status},
            "player_movement": {"status": player_movement_status},
        },
    }
    aggregate["source"]["aggregation_input_semantic_digest"] = canonical_json_sha256(aggregate)
    _write(source_dir / "aggregate_inputs.json", aggregate)
    _write(source_dir / "public_report.json", public_report)


def _write_physical_match(matches_dir: Path) -> None:
    match_dir = matches_dir / "physical-one"
    match_dir.mkdir(parents=True)
    _write(
        match_dir / "match.json",
        {"id": "physical-one", "title": "Physical", "teams": [{"id": "team-corgi", "name": "Corgi"}]},
    )
    _write(
        match_dir / "resolved_player_stats.json",
        {
            "players": [
                {
                    "player_id": "player-corgi",
                    "team_id": "team-corgi",
                    "player_name": "Pawel",
                    "time": {"playing_time_sec": 10.0, "detected_time_sec": 10.0},
                    "distance": {"total_distance_m": 20.0, "observed_distance_m": 20.0},
                    "speed": {"peak_sustained_speed_kmh": 10.0},
                    "intensity": {"sprint_count": 1},
                }
            ]
        },
    )


def _set_technical_timestamp(path: Path) -> None:
    document = _read(path)
    document["generated_at"] = "2030-01-01T00:00:00+00:00"
    _write(path, document)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _artifact_bytes(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _self_excluding_digest(document: dict, field: str) -> str:
    digest_document = copy.deepcopy(document)
    digest_document.pop(field, None)
    return canonical_json_sha256(digest_document)


if __name__ == "__main__":
    unittest.main()
