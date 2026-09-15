from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import app
from app.services.artifact_lineage import canonical_json_sha256
from app.services import shot_review_editor as editor
from app.services import shot_frame_location as frame_location
from app.services.shot_candidates import POLICY_VERSION, V2_POLICY_VERSION


def _report(duration: float = 100.0) -> dict:
    return {
        "match": {"duration_sec": duration},
        "teams": [{"team_id": "team-a"}, {"team_id": "team-b"}],
        "players": [{"player_id": "p-a-1", "team_id": "team-a"}, {"player_id": "p-a-2", "team_id": "team-a"}, {"player_id": "p-b-1", "team_id": "team-b"}],
    }


def _match(*, source_kind: str = "physical") -> dict:
    return {"source_kind": source_kind, "source_match_id": "source-one", "public_report": _report()}


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class ShotReviewEditorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.matches = self.root / "matches"
        _write(self.matches / "source-one" / "shot_candidates.json", {"policy_version": POLICY_VERSION, "candidates": [{"candidate_id": "candidate-1", "candidate_timestamp_sec": 10.0}]})
        self.patches = [
            patch("app.services.shot_review_editor.EDITORIAL_DIRECTORY", self.root / "editorial"),
            patch.object(editor.config, "MATCHES_DIR", self.matches),
            patch("app.services.shot_review_editor.get_published_match", return_value=_match()),
        ]
        for value in self.patches:
            value.start()
        self.public_projection_refresh = patch("app.services.shot_review_editor.refresh_published_public_report_shots")
        self.public_projection_refresh_mock = self.public_projection_refresh.start()
        self.patches.append(self.public_projection_refresh)

    def tearDown(self) -> None:
        for value in reversed(self.patches):
            value.stop()
        self.temporary.cleanup()

    def _initial(self) -> dict:
        return editor.editor_state("published-one")

    def _accept(self, state: dict | None = None) -> dict:
        state = state or self._initial()
        return editor.accept_suggestion("published-one", {
            "expected_revision": state["revision"],
            "candidate_id": "candidate-1",
            "candidate_generation_digest": state["suggestions"]["candidate_generation_digest"],
            "shot": {"team_id": "team-a", "outcome": "off_target"},
        })

    def _write_candidates(self, rows: list[dict]) -> None:
        _write(self.matches / "source-one" / "shot_candidates.json", {"policy_version": POLICY_VERSION, "candidates": rows})

    def _install_public_report(self, *, source_kind: str = "physical") -> tuple[Path, Path, dict]:
        published_root = self.root / "published" / "matches"
        public_root = self.root / "client-public" / "matches"
        target = published_root / "published-one"
        mirror = public_root / "published-one"
        report = {
            "id": "published-one",
            "source_match_id": "source-one",
            "match": {"duration_sec": 100.0},
            "teams": [{"team_id": "team-a"}, {"team_id": "team-b"}],
            "players": [{"player_id": "p-a-1", "team_id": "team-a"}],
            "key_moments": [{"moment_id": "unchanged"}],
            "shots": [{"shot_id": "legacy-shot"}],
            "unrelated_public_field": {"must": "stay"},
        }
        _write(target / "summary.json", {"id": "published-one", "source_kind": source_kind})
        _write(target / "public_report.json", report)
        _write(mirror / "public_report.json", report)
        if source_kind == "physical":
            _write(target / "package.json", {"match": {"id": "one"}})
            aggregate = {"source": {"public_report_semantic_digest": canonical_json_sha256(report)}}
            aggregate["source"]["aggregation_input_semantic_digest"] = canonical_json_sha256(aggregate)
            _write(target / "aggregate_inputs.json", aggregate)
        else:
            _write(target / "provenance.json", {"report_digest": canonical_json_sha256(report), "unchanged": True})
        return target, mirror, report

    def _enable_real_public_projection_refresh(self):
        self.public_projection_refresh.stop()
        return [
            patch("app.services.json_publish_store.PUBLISHED_MATCHES_DIR", self.root / "published" / "matches"),
            patch("app.services.public_match_report.CLIENT_PUBLIC_MATCHES_DIR", self.root / "client-public" / "matches"),
        ]

    def test_accept_creates_canonical_shot_hides_suggestion_and_changes_revision(self) -> None:
        initial = self._initial()
        accepted = self._accept(initial)
        self.assertNotEqual(initial["revision"], accepted["revision"])
        self.assertEqual(accepted["suggestions"]["unreviewed_count"], 0)
        self.assertEqual(accepted["suggestions"]["accepted_count"], 1)
        shot = accepted["canonical_shots"][0]
        self.assertEqual((shot["origin"], shot["time_sec"], shot["team_id"]), ("accepted_suggestion", 10.0, "team-a"))

    def test_public_canonical_projection_is_minimal_sorted_and_keeps_null_location(self) -> None:
        initial = self._initial()
        accepted = self._accept(initial)
        saved = editor.create_manual_shot("published-one", {
            "expected_revision": accepted["revision"],
            "shot": {"time_sec": 4, "team_id": "team-b", "outcome": "goal", "player_id": "p-b-1"},
        })
        editor.create_manual_shot("published-one", {
            "expected_revision": saved["revision"],
            "shot": {"time_sec": 20, "team_id": "team-a", "outcome": "blocked", "player_id": "p-a-1", "location_m": {"x": 2, "y": 3}},
        })
        state = editor.editor_state("published-one")
        editor.create_manual_shot("published-one", {
            "expected_revision": state["revision"],
            "shot": {"time_sec": 17, "team_id": "team-a", "outcome": "on_target"},
        })
        rows = editor.public_canonical_shots_projection("published-one")
        self.assertEqual([row["time_sec"] for row in rows or []], [4.0, 10.0, 17.0, 20.0])
        self.assertEqual({key for row in rows or [] for key in row}, {"shot_id", "time_sec", "team_id", "outcome", "player_id", "location_m"})
        self.assertEqual({row["outcome"] for row in rows or []}, {"goal", "on_target", "off_target", "blocked"})
        self.assertIsNone((rows or [])[1]["location_m"])
        self.assertEqual((rows or [])[3]["location_m"], {"x": 2.0, "y": 3.0})

    def test_manual_create_frame_location_edit_and_delete_refresh_the_static_report(self) -> None:
        target, mirror, original_report = self._install_public_report()
        _write(self.matches / "source-one" / "pitch_config.json", {
            "image_points": [[0, 0], [100, 0], [100, 200], [0, 200]],
            "width_m": 30,
            "length_m": 40,
        })
        publication_patch, mirror_patch = self._enable_real_public_projection_refresh()
        with publication_patch, mirror_patch, patch("app.services.shot_frame_location.get_published_match", return_value=_match()), patch("app.services.json_publish_store.build_aggregate_inputs") as rebuild:
            created = editor.create_manual_shot("published-one", {
                "expected_revision": self._initial()["revision"],
                "shot": {"time_sec": 10, "team_id": "team-a", "outcome": "blocked"},
            })
            created_report = json.loads((mirror / "public_report.json").read_text(encoding="utf-8"))
            self.assertEqual(created_report["shots"][0]["location_m"], None)
            self.assertEqual(created_report["key_moments"], original_report["key_moments"])
            self.assertEqual(created_report["unrelated_public_field"], original_report["unrelated_public_field"])
            self.assertEqual(created["revision"], editor.load_shot_review_document("published-one")["revision"])

            edited = editor.edit_canonical_shot("published-one", created["saved_shot"]["shot_id"], {
                "expected_revision": created["revision"],
                "shot": {
                    "time_sec": 10,
                    "team_id": "team-a",
                    "outcome": "on_target",
                    "frame_location_override": {
                        "logical_frame_time_sec": 10,
                        "x_px": 50,
                        "y_px": 100,
                        "frame_width": 100,
                        "frame_height": 200,
                    },
                },
            })
            projected = json.loads((mirror / "public_report.json").read_text(encoding="utf-8"))["shots"]
            self.assertEqual((projected[0]["outcome"], projected[0]["location_m"]), ("on_target", {"x": 15.0, "y": 20.0}))
            self.assertNotIn("location_correction_provenance", projected[0])
            self.assertEqual(json.loads((target / "public_report.json").read_text(encoding="utf-8")), json.loads((mirror / "public_report.json").read_text(encoding="utf-8")))

            deleted = editor.delete_canonical_shot("published-one", edited["saved_shot"]["shot_id"], {"expected_revision": edited["revision"]})
            self.assertEqual(json.loads((mirror / "public_report.json").read_text(encoding="utf-8"))["shots"], [])
            self.assertEqual(deleted["revision"], editor.load_shot_review_document("published-one")["revision"])
            rebuild.assert_not_called()

    def test_suggestion_acceptance_refreshes_the_static_report(self) -> None:
        _, mirror, _ = self._install_public_report()
        publication_patch, mirror_patch = self._enable_real_public_projection_refresh()
        with publication_patch, mirror_patch:
            accepted = self._accept()
            accepted_shots = json.loads((mirror / "public_report.json").read_text(encoding="utf-8"))["shots"]
            self.assertEqual(accepted_shots[0]["shot_id"], accepted["accepted_shot"]["shot_id"])

    def test_cluster_acceptance_refreshes_the_static_report(self) -> None:
        self._write_candidates([
            {"candidate_id": "candidate-1", "candidate_timestamp_sec": 10.0, "confidence": .4},
            {"candidate_id": "candidate-2", "candidate_timestamp_sec": 11.0, "confidence": .8},
        ])
        _, mirror, _ = self._install_public_report()
        publication_patch, mirror_patch = self._enable_real_public_projection_refresh()
        with publication_patch, mirror_patch:
            state = self._initial()
            cluster = state["unreviewed_suggestion_clusters"][0]
            accepted = editor.accept_cluster("published-one", {
                "expected_revision": state["revision"],
                "candidate_generation_digest": state["candidate_generation_digest"],
                "cluster_id": cluster["cluster_id"],
                "shot": {"team_id": "team-a", "outcome": "goal"},
            })
        shots = json.loads((mirror / "public_report.json").read_text(encoding="utf-8"))["shots"]
        self.assertEqual(shots[0]["shot_id"], accepted["accepted_shot"]["shot_id"])

    def test_merged_publication_uses_its_own_report_authority(self) -> None:
        target, mirror, _ = self._install_public_report(source_kind="merged")
        publication_patch, mirror_patch = self._enable_real_public_projection_refresh()
        merged_match = _match(source_kind="merged")
        with publication_patch, mirror_patch, patch("app.services.shot_review_editor.get_published_match", return_value=merged_match):
            created = editor.create_manual_shot("published-one", {
                "expected_revision": self._initial()["revision"],
                "shot": {"time_sec": 10, "team_id": "team-a", "outcome": "goal"},
            })
        self.assertFalse((target / "package.json").exists())
        report = json.loads((mirror / "public_report.json").read_text(encoding="utf-8"))
        provenance = json.loads((target / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(report["shots"][0]["shot_id"], created["saved_shot"]["shot_id"])
        self.assertEqual(provenance["report_digest"], canonical_json_sha256(report))

    def test_public_projection_failure_is_loud_after_canonical_mutation(self) -> None:
        self.public_projection_refresh_mock.side_effect = OSError("static mirror unavailable")
        with self.assertRaises(editor.ShotReviewError) as failure:
            editor.create_manual_shot("published-one", {
                "expected_revision": self._initial()["revision"],
                "shot": {"time_sec": 10, "team_id": "team-a", "outcome": "goal"},
            })
        self.assertEqual(failure.exception.code, "shot_review_public_projection_refresh_failed")
        self.assertEqual(len(editor.load_shot_review_document("published-one")["canonical_shots"]), 1)

    def test_public_projection_preserves_legacy_absence_and_fails_closed_for_recovery(self) -> None:
        self.assertIsNone(editor.public_canonical_shots_projection("published-one"))
        editorial = self.root / "editorial"
        editorial.mkdir(parents=True, exist_ok=True)
        (editorial / "published-one.authority.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(editor.ShotReviewError) as error:
            editor.public_canonical_shots_projection("published-one")
        self.assertEqual(error.exception.code, "shot_review_recovery_required")

    def test_public_map_location_uses_saved_phase_direction_without_mutating_canonical_point(self) -> None:
        _write(self.matches / "source-one" / "pitch_config.json", {"width_m": 30, "length_m": 40})
        point = {"x": 6, "y": 12}
        expected = {
            "towards_y_min": {"x": 0.2, "y": 0.3},
            "towards_y_max": {"x": 0.8, "y": 0.7},
            "towards_x_min": {"x": 0.3, "y": 0.2},
            "towards_x_max": {"x": 0.7, "y": 0.8},
        }
        report = {"teams": [{"team_id": "team-a", "team_label": "A"}]}
        for direction, map_point in expected.items():
            _write(self.matches / "source-one" / "match_phase_config.json", {"periods": [{"start_time_sec": 0, "end_time_sec": 100, "team_attack_directions": {"A": direction}}]})
            shot = {"time_sec": 10, "team_id": "team-a", "location_m": dict(point)}
            self.assertEqual(editor._public_map_location("published-one", report, shot), map_point)
            self.assertEqual(shot["location_m"], point)

    def test_public_map_location_fails_closed_without_safe_display_context(self) -> None:
        _write(self.matches / "source-one" / "pitch_config.json", {"width_m": 30, "length_m": 40})
        report = {"teams": [{"team_id": "team-a", "team_label": "A"}]}
        shot = {"time_sec": 10, "team_id": "team-a", "location_m": {"x": 6, "y": 12}}
        _write(self.matches / "source-one" / "match_phase_config.json", {"periods": [{"start_time_sec": 0, "end_time_sec": 100, "team_attack_directions": {"A": "unknown"}}]})
        self.assertIsNone(editor._public_map_location("published-one", report, shot))
        with patch("app.services.shot_review_editor._source_context", return_value=None):
            self.assertIsNone(editor._public_map_location("published-one", report, shot))
        self.assertIsNone(editor._public_map_location("published-one", report, {**shot, "location_m": {"x": 31, "y": 12}}))

    def test_public_map_location_resolves_merged_logical_time_to_the_member_phase(self) -> None:
        _write(self.matches / "source-two" / "pitch_config.json", {"width_m": 30, "length_m": 40})
        _write(self.matches / "source-two" / "match_phase_config.json", {"periods": [{"start_time_sec": 0, "end_time_sec": 100, "team_attack_directions": {"A": "towards_y_max"}}]})
        merged_match = {"source_kind": "merged", "public_report": {"teams": [{"team_id": "team-a", "team_label": "A"}]}}
        with patch("app.services.shot_review_editor.get_published_match", return_value=merged_match), patch("app.services.shot_review_editor.group_id_for_merged_published_id", return_value="group-one"), patch("app.services.shot_review_editor.get_match_group", return_value={"members": [{"source_match_id": "source-two", "logical_start_sec": 100, "logical_end_sec": 200}]}):
            self.assertEqual(
                editor._public_map_location("published-merged-one", merged_match["public_report"], {"time_sec": 112, "team_id": "team-a", "location_m": {"x": 6, "y": 12}}),
                {"x": 0.8, "y": 0.7},
            )

    def test_frame_location_context_and_projection_use_the_physical_source_pitch(self) -> None:
        _write(self.matches / "source-one" / "pitch_config.json", {
            "image_points": [[0, 0], [100, 0], [100, 200], [0, 200]],
            "width_m": 30, "length_m": 40,
        })
        with patch("app.services.shot_frame_location.get_published_match", return_value=_match()):
            context = frame_location.frame_location_context("published-one", 10.5)
            projected = frame_location.project_frame_location("published-one", {
                "logical_frame_time_sec": 10.5,
                "x_px": 50,
                "y_px": 100,
                "frame_width": 100,
                "frame_height": 200,
            })
        self.assertEqual(context, {
            "logical_frame_time_sec": 10.5,
            "source_match_id": "source-one",
            "source_time_sec": 10.5,
            "projection_available": True,
            "projection_error": None,
        })
        self.assertEqual(projected["location_m"], {"x": 15.0, "y": 20.0})
        self.assertEqual(projected["provenance"]["source_time_sec"], 10.5)

    def test_frame_location_resolves_merged_logical_time_to_the_correct_member_and_local_time(self) -> None:
        _write(self.matches / "source-two" / "pitch_config.json", {
            "image_points": [[0, 0], [100, 0], [100, 200], [0, 200]],
            "width_m": 30, "length_m": 40,
        })
        merged = {"source_kind": "merged", "public_report": _report()}
        with patch("app.services.shot_frame_location.get_published_match", return_value=merged), patch("app.services.shot_frame_location.group_id_for_merged_published_id", return_value="group-one"), patch("app.services.shot_frame_location.get_match_group", return_value={"members": [
            {"source_match_id": "source-one", "logical_start_sec": 0, "logical_end_sec": 100},
            {"source_match_id": "source-two", "logical_start_sec": 100.1, "logical_end_sec": 200},
        ]}):
            context = frame_location.frame_location_context("published-merged-one", 112.1)
            projected = frame_location.project_frame_location("published-merged-one", {
                "logical_frame_time_sec": 112.1,
                "x_px": 100,
                "y_px": 200,
                "frame_width": 100,
                "frame_height": 200,
            })
        self.assertEqual((context["source_match_id"], context["source_time_sec"]), ("source-two", 12.0))
        self.assertEqual((projected["location_m"], projected["provenance"]["source_match_id"]), ({"x": 30.0, "y": 40.0}, "source-two"))

    def test_frame_location_fails_closed_for_missing_calibration_invalid_pixel_and_out_of_pitch_projection(self) -> None:
        with patch("app.services.shot_frame_location.get_published_match", return_value=_match()):
            unavailable = frame_location.frame_location_context("published-one", 10)
            self.assertFalse(unavailable["projection_available"])
            self.assertEqual(unavailable["projection_error"]["code"], "shot_review_frame_calibration_unavailable")
            with self.assertRaises(frame_location.ShotFrameLocationError) as invalid_point:
                frame_location.project_frame_location("published-one", {"logical_frame_time_sec": 10, "x_px": 101, "y_px": 50, "frame_width": 100, "frame_height": 100})
        self.assertEqual(invalid_point.exception.code, "shot_review_frame_point_invalid")
        _write(self.matches / "source-one" / "pitch_config.json", {
            "image_points": [[20, 20], [80, 20], [80, 80], [20, 80]],
            "width_m": 30, "length_m": 40,
        })
        with patch("app.services.shot_frame_location.get_published_match", return_value=_match()):
            with self.assertRaises(frame_location.ShotFrameLocationError) as out_of_pitch:
                frame_location.project_frame_location("published-one", {"logical_frame_time_sec": 10, "x_px": 0, "y_px": 0, "frame_width": 100, "frame_height": 100})
        self.assertEqual(out_of_pitch.exception.code, "shot_review_frame_projection_out_of_pitch")

    def test_frame_location_override_reprojects_on_save_preserves_shot_anchor_and_stays_internal(self) -> None:
        _write(self.matches / "source-one" / "pitch_config.json", {
            "image_points": [[0, 0], [100, 0], [100, 200], [0, 200]],
            "width_m": 30, "length_m": 40,
        })
        with patch("app.services.shot_frame_location.get_published_match", return_value=_match()):
            initial = self._initial()
            saved = editor.create_manual_shot("published-one", {
                "expected_revision": initial["revision"],
                "shot": {
                    "time_sec": 10,
                    "team_id": "team-a",
                    "outcome": "goal",
                    "frame_location_override": {
                        "logical_frame_time_sec": 12,
                        "x_px": 50,
                        "y_px": 100,
                        "frame_width": 100,
                        "frame_height": 200,
                    },
                },
            })
        shot = saved["canonical_shots"][0]
        self.assertEqual((shot["time_sec"], shot["location_m"], shot["location_source"]), (10.0, {"x": 15.0, "y": 20.0}, "manual"))
        persisted = editor.load_shot_review_document("published-one")["canonical_shots"][0]
        self.assertEqual(persisted["location_correction_provenance"]["source_time_sec"], 12.0)
        with patch("app.services.shot_review_editor.get_published_match", return_value=_match()):
            public = editor.public_canonical_shots_projection("published-one")
        self.assertEqual(public, [{"shot_id": shot["shot_id"], "time_sec": 10.0, "team_id": "team-a", "outcome": "goal", "player_id": None, "location_m": {"x": 15.0, "y": 20.0}}])

    def test_rejection_is_durable_for_same_lineage(self) -> None:
        initial = self._initial()
        rejected = editor.reject_suggestion("published-one", {"expected_revision": initial["revision"], "candidate_id": "candidate-1", "candidate_generation_digest": initial["suggestions"]["candidate_generation_digest"]})
        reopened = self._initial()
        self.assertEqual(rejected["suggestions"]["unreviewed_count"], 0)
        self.assertEqual(reopened["suggestions"]["rejected_count"], 1)
        self.assertEqual(reopened["suggestions"]["unreviewed_count"], 0)

    def test_rejection_and_canonical_shots_survive_promoted_policy_rebuild(self) -> None:
        initial = self._initial()
        accepted = self._accept(initial)
        manual = editor.create_manual_shot("published-one", {
            "expected_revision": accepted["revision"],
            "shot": {"time_sec": 25, "team_id": "team-b", "outcome": "blocked"},
        })
        self._write_candidates([
            {"candidate_id": "candidate-1", "candidate_timestamp_sec": 10.0},
            {"candidate_id": "candidate-rejected", "candidate_timestamp_sec": 30.0},
        ])
        before_rebuild = self._initial()
        rejected = editor.reject_suggestion("published-one", {
            "expected_revision": before_rebuild["revision"],
            "candidate_id": "candidate-rejected",
            "candidate_generation_digest": before_rebuild["suggestions"]["candidate_generation_digest"],
        })
        _write(self.matches / "source-one" / "shot_candidates.json", {
            "policy_version": V2_POLICY_VERSION,
            "candidates": [
                {"candidate_id": "candidate-1", "candidate_timestamp_sec": 10.0},
                {"candidate_id": "candidate-rejected", "candidate_timestamp_sec": 30.0},
            ],
        })
        rebuilt = self._initial()

        self.assertNotEqual(rejected["suggestions"]["candidate_generation_digest"], rebuilt["suggestions"]["candidate_generation_digest"])
        self.assertEqual((rebuilt["suggestions"]["accepted_count"], rebuilt["suggestions"]["rejected_count"], rebuilt["suggestions"]["unreviewed_count"]), (1, 1, 0))
        self.assertEqual({row["origin"] for row in rebuilt["canonical_shots"]}, {"accepted_suggestion", "manual"})
        self.assertEqual(manual["saved_shot"]["shot_id"], next(row["shot_id"] for row in rebuilt["canonical_shots"] if row["origin"] == "manual"))

    def test_manual_shot_needs_only_required_fields_and_survives_generation_change(self) -> None:
        initial = self._initial()
        accepted = self._accept(initial)
        saved = editor.create_manual_shot("published-one", {"expected_revision": accepted["revision"], "shot": {"time_sec": 25, "team_id": "team-b", "outcome": "blocked"}})
        _write(self.matches / "source-one" / "shot_candidates.json", {"policy_version": "shot-candidate-shadow:v2", "candidates": [{"candidate_id": "candidate-2", "candidate_timestamp_sec": 30.0}]})
        refreshed = self._initial()
        self.assertEqual(saved["saved_shot"]["origin"], "manual")
        self.assertEqual({row["origin"] for row in refreshed["canonical_shots"]}, {"accepted_suggestion", "manual"})
        self.assertIn(saved["saved_shot"]["shot_id"], {row["shot_id"] for row in refreshed["canonical_shots"]})
        self.assertEqual(refreshed["suggestions"]["unreviewed_count"], 1)

    def test_all_four_outcomes_are_valid_and_other_values_are_rejected(self) -> None:
        state = self._initial()
        for index, outcome in enumerate(("goal", "on_target", "off_target", "blocked")):
            state = editor.create_manual_shot("published-one", {"expected_revision": state["revision"], "shot": {"time_sec": 20 + index, "team_id": "team-a", "outcome": outcome}})
        with self.assertRaises(editor.ShotReviewError) as failure:
            editor.create_manual_shot("published-one", {"expected_revision": state["revision"], "shot": {"time_sec": 30, "team_id": "team-a", "outcome": "saved"}})
        self.assertEqual(failure.exception.code, "shot_review_outcome_invalid")

    def test_team_and_player_validation(self) -> None:
        state = self._initial()
        for shot, code in (({"time_sec": 10, "team_id": "missing", "outcome": "goal"}, "shot_review_team_invalid"), ({"time_sec": 10, "team_id": "team-a", "player_id": "missing", "outcome": "goal"}, "shot_review_player_invalid"), ({"time_sec": 10, "team_id": "team-a", "player_id": "p-b-1", "outcome": "goal"}, "shot_review_player_invalid")):
            with self.assertRaises(editor.ShotReviewError) as failure:
                editor.create_manual_shot("published-one", {"expected_revision": state["revision"], "shot": shot})
            self.assertEqual(failure.exception.code, code)

    def test_stale_revision_fails_and_current_revision_succeeds(self) -> None:
        initial = self._initial()
        saved = editor.create_manual_shot("published-one", {"expected_revision": initial["revision"], "shot": {"time_sec": 10, "team_id": "team-a", "outcome": "goal"}})
        with self.assertRaises(editor.ShotReviewError) as failure:
            editor.create_manual_shot("published-one", {"expected_revision": initial["revision"], "shot": {"time_sec": 11, "team_id": "team-a", "outcome": "goal"}})
        self.assertEqual(failure.exception.code, "shot_review_revision_conflict")
        self.assertEqual(len(saved["canonical_shots"]), 1)

    def test_location_derivation_ball_player_manual_unavailable_and_unknown_gap(self) -> None:
        _write(self.matches / "source-one" / "ball_tracks.json", {"positions": [{"time_sec": 10, "source": "detected", "confidence": .9, "position_m": [4, 5]}]})
        ball = editor.create_manual_shot("published-one", {"expected_revision": self._initial()["revision"], "shot": {"time_sec": 10, "team_id": "team-a", "outcome": "goal"}})
        self.assertEqual((ball["saved_shot"]["location_m"], ball["saved_shot"]["location_source"]), ({"x": 4.0, "y": 5.0}, "ball"))
        _write(self.matches / "source-one" / "ball_tracks.json", {"positions": []})
        _write(self.matches / "source-one" / "match.json", {"video": {"fps": 30}})
        _write(self.matches / "source-one" / "global_identity.json", {
            "pitch_dimensions_m": {"width_m": 30, "length_m": 47.4},
            "slots": [{"stable_subject_id": "slot-a01", "stable_player_id": "A01", "overlay_positions": [{"frame": 360, "time_sec": 12, "source": "detected", "status": "detected", "pitch_m": [6, 7]}]}],
        })
        _write(self.matches / "source-one" / "player_identity_assignments.json", {"assignments": [{"stable_subject_id": "slot-a01", "stable_player_id": "A01", "start_frame": 360, "end_frame": 360, "status": "assigned", "player_id": "p-a-1", "team_id": "team-a"}]})
        player = editor.create_manual_shot("published-one", {"expected_revision": ball["revision"], "shot": {"time_sec": 12, "team_id": "team-a", "player_id": "p-a-1", "outcome": "on_target"}})
        self.assertEqual((player["saved_shot"]["location_m"], player["saved_shot"]["location_source"]), ({"x": 6.0, "y": 7.0}, "player"))
        manual = editor.create_manual_shot("published-one", {"expected_revision": player["revision"], "shot": {"time_sec": 20, "team_id": "team-a", "outcome": "blocked", "location_m": {"x": 8, "y": 9}}})
        self.assertEqual(manual["saved_shot"]["location_source"], "manual")
        unavailable = editor.create_manual_shot("published-one", {"expected_revision": manual["revision"], "shot": {"time_sec": 30, "team_id": "team-b", "outcome": "off_target"}})
        self.assertEqual((unavailable["saved_shot"]["location_m"], unavailable["saved_shot"]["location_source"]), (None, "unavailable"))
        _write(self.matches / "source-one" / "ball_tracks.json", {"positions": [{"time_sec": 39.8, "source": "detected", "confidence": .9, "position_m": [2, 2]}, {"time_sec": 39.9, "source": "unknown", "confidence": 0, "position_m": None}]})
        blocked = editor.create_manual_shot("published-one", {"expected_revision": unavailable["revision"], "shot": {"time_sec": 40, "team_id": "team-b", "outcome": "off_target"}})
        self.assertEqual(blocked["saved_shot"]["location_source"], "unavailable")

    def test_edit_preserves_location_unless_override_time_or_player_changes(self) -> None:
        _write(self.matches / "source-one" / "ball_tracks.json", {"positions": [{"time_sec": 10, "source": "detected", "confidence": .9, "position_m": [4, 5]}, {"time_sec": 11, "source": "detected", "confidence": .9, "position_m": [9, 10]}]})
        initial = self._initial()
        ball = editor.create_manual_shot("published-one", {"expected_revision": initial["revision"], "shot": {"time_sec": 10, "team_id": "team-a", "outcome": "blocked"}})
        ball_id = ball["saved_shot"]["shot_id"]
        outcome_only = editor.edit_canonical_shot("published-one", ball_id, {"expected_revision": ball["revision"], "shot": {"time_sec": 10, "team_id": "team-a", "outcome": "off_target", "location_m": {"x": 4, "y": 5}}})
        self.assertEqual((outcome_only["saved_shot"]["location_m"], outcome_only["saved_shot"]["location_source"]), ({"x": 4.0, "y": 5.0}, "ball"))
        changed_time = editor.edit_canonical_shot("published-one", ball_id, {"expected_revision": outcome_only["revision"], "shot": {"time_sec": 11, "team_id": "team-a", "outcome": "off_target", "location_m": {"x": 4, "y": 5}}})
        self.assertEqual((changed_time["saved_shot"]["location_m"], changed_time["saved_shot"]["location_source"]), ({"x": 9.0, "y": 10.0}, "ball"))
        manual = editor.create_manual_shot("published-one", {"expected_revision": changed_time["revision"], "shot": {"time_sec": 20, "team_id": "team-a", "outcome": "goal", "location_m": {"x": 8, "y": 9}}})
        manual_id = manual["saved_shot"]["shot_id"]
        preserved = editor.edit_canonical_shot("published-one", manual_id, {"expected_revision": manual["revision"], "shot": {"time_sec": 20, "team_id": "team-a", "outcome": "on_target", "location_m": {"x": 8, "y": 9}}})
        self.assertEqual((preserved["saved_shot"]["location_m"], preserved["saved_shot"]["location_source"]), ({"x": 8.0, "y": 9.0}, "manual"))
        corrected = editor.edit_canonical_shot("published-one", manual_id, {"expected_revision": preserved["revision"], "shot": {"time_sec": 20, "team_id": "team-a", "outcome": "on_target", "manual_location_override": {"x": 11, "y": 12}}})
        self.assertEqual((corrected["saved_shot"]["location_m"], corrected["saved_shot"]["location_source"]), ({"x": 11.0, "y": 12.0}, "manual"))

    def test_player_change_rederives_through_resolved_identity_timeline(self) -> None:
        _write(self.matches / "source-one" / "match.json", {"video": {"fps": 30}})
        _write(self.matches / "source-one" / "global_identity.json", {"pitch_dimensions_m": {"width_m": 30, "length_m": 47.4}, "slots": [
            {"stable_subject_id": "slot-a01", "stable_player_id": "A01", "overlay_positions": [{"frame": 360, "time_sec": 12, "source": "detected", "pitch_m": [6, 7]}]},
            {"stable_subject_id": "slot-a02", "stable_player_id": "A02", "overlay_positions": [{"frame": 360, "time_sec": 12, "source": "detected", "pitch_m": [16, 17]}]},
        ]})
        _write(self.matches / "source-one" / "player_identity_assignments.json", {"assignments": [
            {"stable_subject_id": "slot-a01", "stable_player_id": "A01", "start_frame": 360, "end_frame": 360, "status": "assigned", "player_id": "p-a-1", "team_id": "team-a"},
            {"stable_subject_id": "slot-a02", "stable_player_id": "A02", "start_frame": 360, "end_frame": 360, "status": "assigned", "player_id": "p-a-2", "team_id": "team-a"},
        ]})
        created = editor.create_manual_shot("published-one", {"expected_revision": self._initial()["revision"], "shot": {"time_sec": 12, "team_id": "team-a", "player_id": "p-a-1", "outcome": "goal"}})
        preserved = editor.edit_canonical_shot("published-one", created["saved_shot"]["shot_id"], {"expected_revision": created["revision"], "shot": {"time_sec": 12, "team_id": "team-a", "player_id": "p-a-1", "outcome": "blocked", "location_m": {"x": 6, "y": 7}}})
        self.assertEqual((preserved["saved_shot"]["location_m"], preserved["saved_shot"]["location_source"]), ({"x": 6.0, "y": 7.0}, "player"))
        changed = editor.edit_canonical_shot("published-one", created["saved_shot"]["shot_id"], {"expected_revision": preserved["revision"], "shot": {"time_sec": 12, "team_id": "team-a", "player_id": "p-a-2", "outcome": "goal", "location_m": {"x": 6, "y": 7}}})
        self.assertEqual((changed["saved_shot"]["location_m"], changed["saved_shot"]["location_source"]), ({"x": 16.0, "y": 17.0}, "player"))

    def test_delete_manual_and_accepted_shots_keeps_review_state_consistent(self) -> None:
        accepted = self._accept()
        accepted_id = accepted["accepted_shot"]["shot_id"]
        manual = editor.create_manual_shot("published-one", {"expected_revision": accepted["revision"], "shot": {"time_sec": 30, "team_id": "team-a", "outcome": "goal"}})
        manual_deleted = editor.delete_canonical_shot("published-one", manual["saved_shot"]["shot_id"], {"expected_revision": manual["revision"]})
        self.assertEqual((len(manual_deleted["canonical_shots"]), manual_deleted["accepted_count"]), (1, 1))
        accepted_deleted = editor.delete_canonical_shot("published-one", accepted_id, {"expected_revision": manual_deleted["revision"]})
        self.assertEqual((accepted_deleted["accepted_count"], accepted_deleted["rejected_count"], accepted_deleted["unreviewed_count"], accepted_deleted["canonical_shots"]), (0, 1, 0, []))
        document = editor.load_shot_review_document("published-one")
        self.assertFalse(any(row.get("review_status") == "accepted" and row.get("canonical_shot_id") not in {shot.get("shot_id") for shot in document["canonical_shots"]} for row in document["suggested_candidate_reviews"]))
        self.assertEqual(editor.editor_state("published-one"), accepted_deleted)

    def test_manual_location_bounds_and_physical_timeline_are_validated(self) -> None:
        with self.assertRaises(editor.ShotReviewError) as location:
            editor.create_manual_shot("published-one", {"expected_revision": self._initial()["revision"], "shot": {"time_sec": 10, "team_id": "team-a", "outcome": "goal", "location_m": {"x": -1, "y": 2}}})
        self.assertEqual(location.exception.code, "shot_review_location_invalid")
        with self.assertRaises(editor.ShotReviewError) as time:
            editor.create_manual_shot("published-one", {"expected_revision": self._initial()["revision"], "shot": {"time_sec": 101, "team_id": "team-a", "outcome": "goal"}})
        self.assertEqual(time.exception.code, "shot_review_timestamp_invalid")

    def test_merged_logical_time_uses_member_source_for_location(self) -> None:
        _write(self.matches / "source-two" / "ball_tracks.json", {"positions": [{"time_sec": 20, "source": "detected", "confidence": .9, "position_m": [3, 4]}]})
        merged = {"source_kind": "merged", "public_report": _report()}
        with patch("app.services.shot_review_editor.get_published_match", return_value=merged), patch("app.services.shot_review_editor.group_id_for_merged_published_id", return_value="group-one"), patch("app.services.shot_review_editor.get_match_group", return_value={"members": [{"source_match_id": "source-two", "logical_start_sec": 60, "logical_end_sec": 100}]}):
            state = editor.editor_state("published-merged-one")
            saved = editor.create_manual_shot("published-merged-one", {"expected_revision": state["revision"], "shot": {"time_sec": 80, "team_id": "team-a", "outcome": "goal"}})
        self.assertEqual(saved["saved_shot"]["location_source"], "ball")

    def test_merged_review_reads_a_uniform_historic_policy_until_full_regeneration(self) -> None:
        _write(self.matches / "source-two" / "shot_candidates.json", {
            "policy_version": V2_POLICY_VERSION,
            "candidates": [{"candidate_id": "candidate-2", "candidate_timestamp_sec": 20.0}],
        })
        _write(self.matches / "source-one" / "shot_candidates.json", {
            "policy_version": V2_POLICY_VERSION,
            "candidates": [{"candidate_id": "candidate-1", "candidate_timestamp_sec": 10.0}],
        })
        merged = {"source_kind": "merged", "public_report": _report()}
        members = [
            {"source_match_id": "source-one", "logical_start_sec": 0, "logical_end_sec": 50},
            {"source_match_id": "source-two", "logical_start_sec": 50, "logical_end_sec": 100},
        ]
        with patch("app.services.shot_review_editor.get_published_match", return_value=merged), patch("app.services.shot_review_editor.group_id_for_merged_published_id", return_value="group-one"), patch("app.services.shot_review_editor.get_match_group", return_value={"members": members, "aggregate_semantic_digest": "group"}):
            state = editor.editor_state("published-merged-one")
        self.assertEqual(state["suggestions"]["status"], "ready")
        self.assertEqual(state["suggestions"]["candidate_count"], 2)

    def test_missing_sidecar_after_operator_mutation_fails_closed(self) -> None:
        saved = editor.create_manual_shot("published-one", {"expected_revision": self._initial()["revision"], "shot": {"time_sec": 10, "team_id": "team-a", "outcome": "goal"}})
        (self.root / "editorial" / "published-one.json").unlink()
        with self.assertRaises(editor.ShotReviewError) as failure:
            editor.editor_state("published-one")
        self.assertEqual(saved["saved_shot"]["origin"], "manual")
        self.assertEqual(failure.exception.code, "shot_review_recovery_required")

    def test_runtime_does_not_read_evaluation_goldset(self) -> None:
        source = Path(editor.__file__).read_text(encoding="utf-8")
        self.assertNotIn("shot_goldset", source)

    def test_cluster_builder_is_source_bounded_deterministic_and_keeps_singletons(self) -> None:
        rows = [
            {"candidate_id": "a", "source_match_id": "one", "time_sec": 10.0, "confidence": .4},
            {"candidate_id": "b", "source_match_id": "one", "time_sec": 12.0, "confidence": .9},
            {"candidate_id": "c", "source_match_id": "one", "time_sec": 15.1, "confidence": .8},
            {"candidate_id": "d", "source_match_id": "two", "time_sec": 12.1, "confidence": .7},
        ]
        first = editor.build_review_clusters(rows, candidate_generation_digest="digest", timeline_span_sec=20)
        second = editor.build_review_clusters(list(reversed(rows)), candidate_generation_digest="digest", timeline_span_sec=20)
        self.assertEqual(first, second)
        self.assertEqual([["a", "b"], ["d"], ["c"]], [row["member_candidate_ids"] for row in first])
        self.assertEqual((first[0]["preferred_candidate_id"], first[0]["review_start_time_sec"], first[0]["review_end_time_sec"]), ("b", 9.5, 12.5))

    def test_cluster_rejection_is_atomic_and_durable_for_same_lineage(self) -> None:
        self._write_candidates([
            {"candidate_id": "candidate-1", "candidate_timestamp_sec": 10.0, "confidence": .4},
            {"candidate_id": "candidate-2", "candidate_timestamp_sec": 11.5, "confidence": .8},
        ])
        initial = self._initial()
        cluster = initial["unreviewed_suggestion_clusters"][0]
        rejected = editor.reject_cluster("published-one", {
            "expected_revision": initial["revision"], "cluster_id": cluster["cluster_id"],
            "candidate_generation_digest": initial["candidate_generation_digest"],
        })
        self.assertEqual((rejected["unreviewed_cluster_count"], rejected["rejected_count"]), (0, 2))
        document = editor.load_shot_review_document("published-one")
        self.assertEqual({row["review_status"] for row in document["suggested_candidate_reviews"]}, {"rejected"})
        self.assertEqual(editor.editor_state("published-one")["unreviewed_cluster_count"], 0)

    def test_cluster_accepts_once_resolves_siblings_and_delete_has_no_dangling_reviews(self) -> None:
        self._write_candidates([
            {"candidate_id": "candidate-1", "candidate_timestamp_sec": 10.0, "confidence": .4},
            {"candidate_id": "candidate-2", "candidate_timestamp_sec": 11.5, "confidence": .8},
        ])
        initial = self._initial()
        cluster = initial["unreviewed_suggestion_clusters"][0]
        accepted = editor.accept_cluster("published-one", {
            "expected_revision": initial["revision"], "cluster_id": cluster["cluster_id"],
            "candidate_generation_digest": initial["candidate_generation_digest"],
            "shot": {"time_sec": 11.2, "team_id": "team-a", "outcome": "blocked"},
        })
        self.assertEqual((len(accepted["canonical_shots"]), accepted["unreviewed_cluster_count"], accepted["accepted_count"]), (1, 0, 2))
        shot = accepted["accepted_shot"]
        document = editor.load_shot_review_document("published-one")
        self.assertEqual((shot["time_sec"], document["canonical_shots"][0]["suggested_cluster_member_candidate_ids"]), (11.2, ["candidate-1", "candidate-2"]))
        deleted = editor.delete_canonical_shot("published-one", shot["shot_id"], {"expected_revision": accepted["revision"]})
        self.assertEqual((deleted["accepted_count"], deleted["rejected_count"], deleted["canonical_shots"]), (0, 2, []))

    def test_cluster_accept_preserves_prior_rejection_and_delete_keeps_it_durable(self) -> None:
        self._write_candidates([
            {"candidate_id": "candidate-1", "candidate_timestamp_sec": 10.0, "confidence": .9},
            {"candidate_id": "candidate-2", "candidate_timestamp_sec": 11.5, "confidence": .6},
        ])
        initial = self._initial()
        rejected_one = editor.reject_suggestion("published-one", {
            "expected_revision": initial["revision"], "candidate_id": "candidate-1",
            "candidate_generation_digest": initial["candidate_generation_digest"],
        })
        partial = rejected_one["unreviewed_suggestion_clusters"][0]
        self.assertEqual(partial["status"], "partially_rejected")
        self.assertEqual((partial["preferred_candidate_id"], partial["preferred_candidate"]["time_sec"]), ("candidate-2", 11.5))
        before = editor.load_shot_review_document("published-one")
        prior_rejection = next(row for row in before["suggested_candidate_reviews"] if row["candidate_id"] == "candidate-1")

        accepted = editor.accept_cluster("published-one", {
            "expected_revision": rejected_one["revision"], "cluster_id": partial["cluster_id"],
            "candidate_generation_digest": rejected_one["candidate_generation_digest"],
            "shot": {"team_id": "team-a", "outcome": "on_target"},
        })
        after = editor.load_shot_review_document("published-one")
        candidate_one = next(row for row in after["suggested_candidate_reviews"] if row["candidate_id"] == "candidate-1")
        candidate_two = next(row for row in after["suggested_candidate_reviews"] if row["candidate_id"] == "candidate-2")
        self.assertEqual(candidate_one, prior_rejection)
        self.assertEqual((candidate_two["review_status"], candidate_two["canonical_shot_id"], len(accepted["canonical_shots"])), ("accepted", accepted["accepted_shot"]["shot_id"], 1))
        canonical = after["canonical_shots"][0]
        self.assertEqual((canonical["suggested_candidate_id"], canonical["time_sec"]), ("candidate-2", 11.5))
        self.assertEqual(accepted["unreviewed_cluster_count"], 0)

        deleted = editor.delete_canonical_shot("published-one", accepted["accepted_shot"]["shot_id"], {"expected_revision": accepted["revision"]})
        reloaded = editor.load_shot_review_document("published-one")
        self.assertEqual(next(row for row in reloaded["suggested_candidate_reviews"] if row["candidate_id"] == "candidate-1"), prior_rejection)
        self.assertEqual((deleted["accepted_count"], deleted["rejected_count"], deleted["unreviewed_cluster_count"]), (0, 2, 0))
        self.assertEqual(editor.editor_state("published-one"), deleted)

    def test_cluster_mutation_rejects_stale_revision_and_generation(self) -> None:
        self._write_candidates([
            {"candidate_id": "candidate-1", "candidate_timestamp_sec": 10.0},
            {"candidate_id": "candidate-2", "candidate_timestamp_sec": 11.0},
        ])
        state = self._initial()
        cluster = state["unreviewed_suggestion_clusters"][0]
        editor.create_manual_shot("published-one", {"expected_revision": state["revision"], "shot": {"time_sec": 30, "team_id": "team-a", "outcome": "goal"}})
        with self.assertRaises(editor.ShotReviewError) as stale_revision:
            editor.reject_cluster("published-one", {"expected_revision": state["revision"], "cluster_id": cluster["cluster_id"], "candidate_generation_digest": state["candidate_generation_digest"]})
        self.assertEqual(stale_revision.exception.code, "shot_review_revision_conflict")
        current = self._initial()
        self._write_candidates([{"candidate_id": "candidate-3", "candidate_timestamp_sec": 20.0}])
        with self.assertRaises(editor.ShotReviewError) as stale_generation:
            editor.reject_cluster("published-one", {"expected_revision": current["revision"], "cluster_id": cluster["cluster_id"], "candidate_generation_digest": current["candidate_generation_digest"]})
        self.assertEqual(stale_generation.exception.code, "shot_review_candidate_stale")

    def test_editor_endpoints_are_registered_without_frontend_routes(self) -> None:
        paths = {getattr(route, "path", "") for route in app.routes}
        base = "/api/published/matches/{published_match_id}/shot-review/editor"
        self.assertTrue({base, f"{base}/frame-location", f"{base}/frame-location/project", f"{base}/suggestions/accept", f"{base}/suggestions/reject", f"{base}/suggestion-clusters/accept", f"{base}/suggestion-clusters/reject", f"{base}/shots", f"{base}/shots/{{shot_id}}"}.issubset(paths))
