from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from app.services.artifact_lineage import canonical_json_sha256
from app.services.match_groups import create_match_group
from app.services.merged_match_acceptance import audit_merged_match
from app.services.merged_public_match import ensure_merged_published_match
from test_merged_public_match import _metadata, _write_source


class MergedMatchAcceptanceTests(unittest.TestCase):
    def test_valid_canonical_merged_fixture_passes(self) -> None:
        with self._store() as store:
            group, merged_id = self._build(store)
            result = audit_merged_match(store, merged_id)
            manifest = _read(store / "published" / "match-groups" / group["group_id"] / "manifest.json")

            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["summary"]["failed"], 0)
            self.assertEqual(manifest["compatibility"]["capabilities"]["spatial"]["status"], "not_available")
            for check in (
                "source published-one aggregate public digest",
                "merged analyzed duration",
                "merged team IDs",
                "merged player IDs",
                "player player-one avg_speed_kmh",
                "pass completion rate",
                "possession controlled coverage",
                "possession timeline exact source rebasing",
                "momentum timeline exact source rebasing",
                "player player-one workload timeline",
                "key moments ready cardinality",
            ):
                with self.subTest(check=check):
                    self.assertEqual(_status(result, check), "pass")
            self.assertEqual(_status(result, "spatial mathematical reconciliation"), "not_audited")
            self.assertEqual(_status(result, "team shape mathematical reconciliation"), "not_audited")

    def test_explicit_spatial_unavailability_with_absent_heatmaps_passes(self) -> None:
        with self._store() as store:
            _, merged_id = self._build(store)
            report_path = store / "published" / "matches" / merged_id / "public_report.json"
            report = _read(report_path)
            for player in report["players"]:
                player["heatmap"] = None
            report["merged_provenance"]["spatial_heatmaps"] = "unavailable:canonical_orientation_not_proven"
            _write(report_path, report)

            result = audit_merged_match(store, merged_id)

            self.assertEqual(result["status"], "pass")
            self.assertEqual(_status(result, "spatial final provenance consistency"), "pass")

    def test_spatial_output_contradicting_final_unavailability_fails(self) -> None:
        with self._store() as store:
            _, merged_id = self._build(store)
            report_path = store / "published" / "matches" / merged_id / "public_report.json"
            report = _read(report_path)
            report["merged_provenance"]["spatial_heatmaps"] = "unavailable:canonical_orientation_not_proven"
            _write(report_path, report)

            result = audit_merged_match(store, merged_id)

            self.assertEqual(result["status"], "fail")
            self.assertEqual(_status(result, "spatial final provenance consistency"), "fail")

    def test_key_moments_not_available_requires_an_empty_list(self) -> None:
        for moments, expected in (([], "pass"), ([_moment("km-a", 0.9, 10)], "fail")):
            with self.subTest(moments=moments), self._store() as store:
                _, merged_id = self._build(store)
                report_path = store / "published" / "matches" / merged_id / "public_report.json"
                report = _read(report_path)
                report["key_moments"] = {"status": "not_available", "moments": moments}
                _write(report_path, report)

                result = audit_merged_match(store, merged_id)

                self.assertEqual(_status(result, "key moments unavailable list"), expected)
                self.assertEqual(result["status"], expected)

    def test_key_moments_ready_requires_a_nonempty_list(self) -> None:
        with self._store() as store:
            _, merged_id = self._build(store)
            report_path = store / "published" / "matches" / merged_id / "public_report.json"
            report = _read(report_path)
            report["key_moments"] = {"status": "ready", "moments": []}
            _write(report_path, report)

            result = audit_merged_match(store, merged_id)

            self.assertEqual(result["status"], "fail")
            self.assertEqual(_status(result, "key moments ready cardinality"), "fail")

    def test_key_moments_unknown_status_fails(self) -> None:
        with self._store() as store:
            _, merged_id = self._build(store)
            report_path = store / "published" / "matches" / merged_id / "public_report.json"
            report = _read(report_path)
            report["key_moments"] = {"status": "pending", "moments": [_moment("km-a", 0.9, 10)]}
            _write(report_path, report)

            result = audit_merged_match(store, merged_id)

            self.assertEqual(result["status"], "fail")
            self.assertEqual(_status(result, "key moments status"), "fail")

    def test_key_moment_bounds_and_order_do_not_skip_dict_shape(self) -> None:
        with self._store() as store:
            _, merged_id = self._build(store)
            report_path = store / "published" / "matches" / merged_id / "public_report.json"
            report = _read(report_path)
            report["key_moments"] = {"status": "ready", "moments": [_moment("late", 0.2, 30), _moment("early", 0.9, 10, end=999)]}
            _write(report_path, report)
            result = audit_merged_match(store, merged_id)
            self.assertEqual(_status(result, "key moments bounds"), "fail")
            self.assertEqual(_status(result, "key moments deterministic ordering"), "fail")

    def test_missing_and_duplicate_player_ids_fail(self) -> None:
        for mutation, expected in ((lambda rows: rows.pop(1), "merged player IDs"), (lambda rows: rows.append(copy.deepcopy(rows[0])), "merged player IDs unique")):
            with self.subTest(expected=expected), self._store() as store:
                _, merged_id = self._build(store, subset_players=True)
                report_path = store / "published" / "matches" / merged_id / "public_report.json"
                report = _read(report_path)
                mutation(report["players"])
                _write(report_path, report)
                result = audit_merged_match(store, merged_id)
                self.assertEqual(_status(result, expected), "fail")

    def test_missing_and_duplicate_team_ids_fail(self) -> None:
        for mutation, expected in ((lambda rows: rows.pop(), "merged team IDs"), (lambda rows: rows.append(copy.deepcopy(rows[0])), "merged team IDs unique")):
            with self.subTest(expected=expected), self._store() as store:
                _, merged_id = self._build(store)
                report_path = store / "published" / "matches" / merged_id / "public_report.json"
                report = _read(report_path)
                mutation(report["teams"])
                _write(report_path, report)
                result = audit_merged_match(store, merged_id)
                self.assertEqual(_status(result, expected), "fail")

    def test_zero_required_metric_is_not_confused_with_missing_null_or_string(self) -> None:
        for value, expected in ((0, "pass"), (None, "fail"), ("0", "fail")):
            with self.subTest(value=value), self._store() as store:
                _, merged_id = self._build(store, zero_sprints=True)
                report_path = store / "published" / "matches" / merged_id / "public_report.json"
                report = _read(report_path)
                report["players"][0]["sprint_count"] = value
                _write(report_path, report)
                result = audit_merged_match(store, merged_id)
                self.assertEqual(_status(result, "player player-one sprint_count"), expected)
        with self._store() as store:
            _, merged_id = self._build(store, zero_sprints=True)
            report_path = store / "published" / "matches" / merged_id / "public_report.json"
            report = _read(report_path)
            report["players"][0].pop("sprint_count")
            _write(report_path, report)
            self.assertEqual(_status(audit_merged_match(store, merged_id), "player player-one sprint_count actual required"), "fail")

    def test_sparse_team_pass_maps_treat_an_absent_team_key_as_zero(self) -> None:
        with self._store() as store:
            _, merged_id = self._build(store, source_mutator=_remove_verisk_from_pass_maps)

            result = audit_merged_match(store, merged_id)

            self.assertEqual(result["status"], "pass")
            for check in (
                "team team-verisk pass_attempts",
                "team team-verisk completed_passes",
                "team team-verisk failed_passes",
                "team team-verisk restart_passes",
                "team team-verisk accepted_passes",
            ):
                with self.subTest(check=check):
                    self.assertEqual(_status(result, check), "pass")

    def test_sparse_team_pass_maps_reject_missing_or_invalid_entries(self) -> None:
        cases: tuple[tuple[str, Callable[[Path], None], str, str], ...] = (
            ("map missing", _remove_attempts_map, "source published-one attempts_by_team_id required", "fail"),
            ("string value", _set_verisk_attempts("0"), "source published-one attempts_by_team_id team-verisk", "fail"),
            ("negative value", _set_verisk_attempts(-1), "source published-one attempts_by_team_id team-verisk", "fail"),
        )
        for label, mutation, check, expected in cases:
            with self.subTest(label=label), self._store() as store:
                _, merged_id = self._build(store)
                mutation(store / "published" / "matches" / "published-one")

                result = audit_merged_match(store, merged_id)

                self.assertEqual(_status(result, check), expected)
                self.assertEqual(result["status"], expected)

    def test_sparse_team_pass_maps_accept_an_explicit_zero(self) -> None:
        with self._store() as store:
            _, merged_id = self._build(store, source_mutator=_set_verisk_attempts(0))

            result = audit_merged_match(store, merged_id)

            self.assertEqual(result["status"], "pass")
            self.assertEqual(_status(result, "team team-verisk pass_attempts"), "pass")

    def test_workload_absence_and_empty_source_windows_match_canonical_output(self) -> None:
        cases: tuple[tuple[str, Callable[[Path], None], bool], ...] = (
            ("absent", _remove_workload, False),
            ("empty windows", _empty_workload_windows, True),
        )
        for label, mutation, expects_object in cases:
            with self.subTest(label=label), self._store() as store:
                _, merged_id = self._build(store, source_mutator=mutation)
                report = _read(store / "published" / "matches" / merged_id / "public_report.json")

                self.assertEqual(isinstance(report["players"][0].get("workload"), dict), expects_object)
                result = audit_merged_match(store, merged_id)

                self.assertEqual(result["status"], "pass")
                self.assertEqual(_status(result, "player player-one workload presence"), "pass")
                if expects_object:
                    self.assertEqual(_status(result, "player player-one workload timeline"), "pass")

    def test_empty_expected_workload_rejects_a_fabricated_merged_window(self) -> None:
        with self._store() as store:
            _, merged_id = self._build(store, source_mutator=_empty_workload_windows)
            report_path = store / "published" / "matches" / merged_id / "public_report.json"
            report = _read(report_path)
            report["players"][0]["workload"]["activity_windows"] = [{"start_time_sec": 1, "end_time_sec": 2}]
            _write(report_path, report)

            result = audit_merged_match(store, merged_id)

            self.assertEqual(result["status"], "fail")
            self.assertEqual(_status(result, "player player-one workload timeline"), "fail")

    def test_nonempty_expected_workload_requires_merged_workload(self) -> None:
        with self._store() as store:
            _, merged_id = self._build(store)
            report_path = store / "published" / "matches" / merged_id / "public_report.json"
            report = _read(report_path)
            report["players"][0]["workload"] = None
            _write(report_path, report)

            result = audit_merged_match(store, merged_id)

            self.assertEqual(result["status"], "fail")
            self.assertEqual(_status(result, "player player-one workload presence"), "fail")

    def test_stale_source_public_and_aggregate_digests_fail(self) -> None:
        for path_name, mutation, check in (("public_report.json", lambda doc: doc["match"].update({"title": "stale"}), "source published-one aggregate public digest"), ("aggregate_inputs.json", lambda doc: doc["timing"].update({"analyzed_duration_sec": 999}), "source published-one aggregate self digest")):
            with self.subTest(path_name=path_name), self._store() as store:
                _, merged_id = self._build(store)
                path = store / "published" / "matches" / "published-one" / path_name
                document = _read(path)
                mutation(document)
                _write(path, document)
                self.assertEqual(_status(audit_merged_match(store, merged_id), check), "fail")

    def test_exact_possession_momentum_and_workload_offsets_fail(self) -> None:
        cases = (("possession_timeline", 1, "possession timeline exact source rebasing"), ("attacking_momentum", 2, "momentum timeline exact source rebasing"), ("workload", 1, "player player-one workload timeline"))
        for target, index, check in cases:
            with self.subTest(target=target), self._store() as store:
                _, merged_id = self._build(store)
                report_path = store / "published" / "matches" / merged_id / "public_report.json"
                report = _read(report_path)
                if target == "possession_timeline": report["ball"][target][index]["start_time_sec"] += 1
                elif target == "attacking_momentum": report["ball"][target]["timeline"][index]["start_time_sec"] += 1
                else: report["players"][0][target]["activity_windows"][index]["start_time_sec"] += 1
                _write(report_path, report)
                self.assertEqual(_status(audit_merged_match(store, merged_id), check), "fail")

    def test_player_speed_and_team_completion_rate_fail(self) -> None:
        for mutate, check in ((lambda report: report["players"][0].update({"avg_speed_kmh": 99}), "player player-one avg_speed_kmh"), (lambda report: report["teams"][0].update({"completion_rate": 99}), "team team-corgi completion rate")):
            with self.subTest(check=check), self._store() as store:
                _, merged_id = self._build(store)
                path = store / "published" / "matches" / merged_id / "public_report.json"
                report = _read(path)
                mutate(report)
                _write(path, report)
                self.assertEqual(_status(audit_merged_match(store, merged_id), check), "fail")

    def _build(self, store: Path, *, subset_players: bool = False, zero_sprints: bool = False, source_mutator: Callable[[Path], None] | None = None) -> tuple[dict, str]:
        fixture = store / "fixture"
        source_directories: list[Path] = []
        for published_id, source_id, duration in (("published-one", "one", 10), ("published-two", "two", 20), ("published-three", "three", 30)):
            _write_source(fixture, published_id, source_id, duration=duration)
            source_dir = fixture / "published" / published_id
            target = store / "published" / "matches" / published_id
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_dir), target)
            self._normalize_source(target)
            if zero_sprints:
                self._zero_sprints(target)
            source_directories.append(target)
        if subset_players:
            self._add_player(store / "published" / "matches" / "published-one", "player-b")
            self._add_player(store / "published" / "matches" / "published-three", "player-c")
        if source_mutator:
            for directory in source_directories:
                source_mutator(directory)
        group = create_match_group(member_published_ids=["published-one", "published-two", "published-three"], metadata=_metadata())
        merged_id = ensure_merged_published_match(str(group["group_id"]))["merged_published_match_id"]
        return group, merged_id

    def _normalize_source(self, directory: Path) -> None:
        aggregate = _read(directory / "aggregate_inputs.json")
        for window in aggregate["timelines"]["possession"]["windows"]:
            window["total_frames"] = window.pop("frames")
        _refresh_source_digests(directory, aggregate)

    def _add_player(self, directory: Path, player_id: str) -> None:
        public, aggregate = _read(directory / "public_report.json"), _read(directory / "aggregate_inputs.json")
        player = copy.deepcopy(public["players"][0]); player["player_id"] = player_id; player["player_name"] = player_id
        movement = copy.deepcopy(aggregate["players"][0]); movement["player_id"] = player_id
        public["players"].append(player); aggregate["players"].append(movement)
        _write(directory / "public_report.json", public)
        _refresh_source_digests(directory, aggregate)

    def _zero_sprints(self, directory: Path) -> None:
        public, aggregate = _read(directory / "public_report.json"), _read(directory / "aggregate_inputs.json")
        public["players"][0]["sprint_count"] = 0
        aggregate["players"][0]["movement"]["sprint_count"] = 0
        _write(directory / "public_report.json", public)
        _refresh_source_digests(directory, aggregate)

    def _store(self):
        temporary = tempfile.TemporaryDirectory(); root = Path(temporary.name)
        (root / "published" / "matches").mkdir(parents=True); (root / "published" / "match-groups").mkdir(parents=True); (root / "client-public").mkdir()
        patches = [patch("app.services.match_groups.PUBLISHED_MATCHES_DIR", root / "published" / "matches"), patch("app.services.match_groups.MATCH_GROUPS_DIR", root / "published" / "match-groups"), patch("app.services.match_group_aggregation.PUBLISHED_MATCHES_DIR", root / "published" / "matches"), patch("app.services.match_group_aggregation.MATCH_GROUPS_DIR", root / "published" / "match-groups"), patch("app.services.match_group_video.MATCH_GROUPS_DIR", root / "published" / "match-groups"), patch("app.services.match_group_video.PUBLISHED_MATCHES_DIR", root / "published" / "matches"), patch("app.services.merged_public_match.PUBLISHED_MATCHES_DIR", root / "published" / "matches"), patch("app.services.merged_public_match.MATCH_GROUPS_DIR", root / "published" / "match-groups"), patch("app.services.merged_public_match.CLIENT_PUBLIC_MATCHES_DIR", root / "client-public")]
        class Context:
            def __enter__(self):
                for item in patches: item.__enter__()
                return root
            def __exit__(self, *args):
                for item in reversed(patches): item.__exit__(*args)
                temporary.cleanup()
        return Context()


_TEAM_PASS_MAP_KEYS = (
    "attempts_by_team_id",
    "completed_by_team_id",
    "failed_by_team_id",
    "restart_attempts_by_team_id",
    "accepted_by_team_id",
)


def _remove_verisk_from_pass_maps(directory: Path) -> None:
    aggregate = _read(directory / "aggregate_inputs.json")
    for key in _TEAM_PASS_MAP_KEYS:
        aggregate["ball"]["passes"][key].pop("team-verisk", None)
    _refresh_source_digests(directory, aggregate)


def _remove_attempts_map(directory: Path) -> None:
    if directory.name != "published-one":
        return
    aggregate = _read(directory / "aggregate_inputs.json")
    aggregate["ball"]["passes"].pop("attempts_by_team_id")
    _refresh_source_digests(directory, aggregate)


def _set_verisk_attempts(value: object) -> Callable[[Path], None]:
    def mutate(directory: Path) -> None:
        if directory.name != "published-one":
            return
        aggregate = _read(directory / "aggregate_inputs.json")
        aggregate["ball"]["passes"]["attempts_by_team_id"]["team-verisk"] = value
        _refresh_source_digests(directory, aggregate)

    return mutate


def _remove_workload(directory: Path) -> None:
    public, aggregate = _read(directory / "public_report.json"), _read(directory / "aggregate_inputs.json")
    for player in public["players"]:
        player.pop("workload", None)
    _write(directory / "public_report.json", public)
    _refresh_source_digests(directory, aggregate)


def _empty_workload_windows(directory: Path) -> None:
    public, aggregate = _read(directory / "public_report.json"), _read(directory / "aggregate_inputs.json")
    for player in public["players"]:
        player["workload"]["activity_windows"] = []
    _write(directory / "public_report.json", public)
    _refresh_source_digests(directory, aggregate)


def _moment(moment_id: str, importance: float, time: float, *, end: float | None = None) -> dict:
    return {"moment_id": moment_id, "importance_score": importance, "time_sec": time, "window_start_sec": time - 1, "window_end_sec": end if end is not None else time + 1, "type": "momentum_peak", "team_id": "team-corgi"}
def _read(path: Path) -> dict: return json.loads(path.read_text(encoding="utf-8"))
def _write(path: Path, document: dict) -> None: path.write_text(json.dumps(document), encoding="utf-8")
def _refresh_source_digests(directory: Path, aggregate: dict) -> None:
    public = _read(directory / "public_report.json")
    aggregate["source"]["public_report_semantic_digest"] = canonical_json_sha256(public)
    domain = copy.deepcopy(aggregate); domain["source"].pop("aggregation_input_semantic_digest", None)
    aggregate["source"]["aggregation_input_semantic_digest"] = canonical_json_sha256(domain)
    _write(directory / "aggregate_inputs.json", aggregate)
def _status(result: dict, name: str) -> str | None: return next((row["status"] for row in result["checks"] if row["name"] == name), None)
