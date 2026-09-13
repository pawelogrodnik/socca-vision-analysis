from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import app
from app.services import shot_review_editor as editor


def _report(duration: float = 100.0) -> dict:
    return {
        "match": {"duration_sec": duration},
        "teams": [{"team_id": "team-a"}, {"team_id": "team-b"}],
        "players": [{"player_id": "a1", "team_id": "team-a"}, {"player_id": "b1", "team_id": "team-b"}],
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
        _write(self.matches / "source-one" / "shot_candidates.json", {"policy_version": "shot-candidate-shadow:v2", "candidates": [{"candidate_id": "candidate-1", "candidate_timestamp_sec": 10.0}]})
        self.patches = [
            patch("app.services.shot_review_editor.EDITORIAL_DIRECTORY", self.root / "editorial"),
            patch.object(editor.config, "MATCHES_DIR", self.matches),
            patch("app.services.shot_review_editor.get_published_match", return_value=_match()),
        ]
        for value in self.patches:
            value.start()

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

    def test_accept_creates_canonical_shot_hides_suggestion_and_changes_revision(self) -> None:
        initial = self._initial()
        accepted = self._accept(initial)
        self.assertNotEqual(initial["revision"], accepted["revision"])
        self.assertEqual(accepted["suggestions"]["unreviewed_count"], 0)
        self.assertEqual(accepted["suggestions"]["accepted_count"], 1)
        shot = accepted["canonical_shots"][0]
        self.assertEqual((shot["origin"], shot["time_sec"], shot["team_id"]), ("accepted_suggestion", 10.0, "team-a"))

    def test_rejection_is_durable_for_same_lineage(self) -> None:
        initial = self._initial()
        rejected = editor.reject_suggestion("published-one", {"expected_revision": initial["revision"], "candidate_id": "candidate-1", "candidate_generation_digest": initial["suggestions"]["candidate_generation_digest"]})
        reopened = self._initial()
        self.assertEqual(rejected["suggestions"]["unreviewed_count"], 0)
        self.assertEqual(reopened["suggestions"]["rejected_count"], 1)
        self.assertEqual(reopened["suggestions"]["unreviewed_count"], 0)

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
        for shot, code in (({"time_sec": 10, "team_id": "missing", "outcome": "goal"}, "shot_review_team_invalid"), ({"time_sec": 10, "team_id": "team-a", "player_id": "missing", "outcome": "goal"}, "shot_review_player_invalid"), ({"time_sec": 10, "team_id": "team-a", "player_id": "b1", "outcome": "goal"}, "shot_review_player_invalid")):
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
        _write(self.matches / "source-one" / "stable_players.json", {"players": [{"stable_player_id": "a1", "trajectory_m": [{"time_sec": 12, "source": "detected", "pitch_m": [6, 7]}]}]})
        player = editor.create_manual_shot("published-one", {"expected_revision": ball["revision"], "shot": {"time_sec": 12, "team_id": "team-a", "player_id": "a1", "outcome": "on_target"}})
        self.assertEqual(player["saved_shot"]["location_source"], "player")
        manual = editor.create_manual_shot("published-one", {"expected_revision": player["revision"], "shot": {"time_sec": 20, "team_id": "team-a", "outcome": "blocked", "location_m": {"x": 8, "y": 9}}})
        self.assertEqual(manual["saved_shot"]["location_source"], "manual")
        unavailable = editor.create_manual_shot("published-one", {"expected_revision": manual["revision"], "shot": {"time_sec": 30, "team_id": "team-b", "outcome": "off_target"}})
        self.assertEqual((unavailable["saved_shot"]["location_m"], unavailable["saved_shot"]["location_source"]), (None, "unavailable"))
        _write(self.matches / "source-one" / "ball_tracks.json", {"positions": [{"time_sec": 39.8, "source": "detected", "confidence": .9, "position_m": [2, 2]}, {"time_sec": 39.9, "source": "unknown", "confidence": 0, "position_m": None}]})
        blocked = editor.create_manual_shot("published-one", {"expected_revision": unavailable["revision"], "shot": {"time_sec": 40, "team_id": "team-b", "outcome": "off_target"}})
        self.assertEqual(blocked["saved_shot"]["location_source"], "unavailable")

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

    def test_editor_endpoints_are_registered_without_frontend_routes(self) -> None:
        paths = {getattr(route, "path", "") for route in app.routes}
        base = "/api/published/matches/{published_match_id}/shot-review/editor"
        self.assertTrue({base, f"{base}/suggestions/accept", f"{base}/suggestions/reject", f"{base}/shots", f"{base}/shots/{{shot_id}}"}.issubset(paths))
