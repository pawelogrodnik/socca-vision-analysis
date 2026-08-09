from __future__ import annotations

import json
import hashlib
from pathlib import Path
import tempfile
import unittest

from app.services.identity_roster_subject_review_store import (
    REVIEW_ARTIFACT_FILENAME,
    REVIEW_DECISIONS_FILENAME,
    VISUAL_PRE_AUDIT_FILENAME,
    load_identity_roster_subject_review,
    save_identity_roster_subject_review,
)
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_initial_audit_store import SEEDS_FILENAME
from app.services.identity_seeded_candidate_assignments import OUTPUT_FILENAME
from app.services.identity_seeded_review_reduction import REPORT_FILENAME


def artifact() -> dict:
    return {
        "schema_version": "0.1.0",
        "generated_at": "ignored-for-digest",
        "mode": "shadow_read_only",
        "summary": {"cards": 2},
        "cards": [
            {
                "review_card_key": "card-1",
                "candidate_subject_id": "subject-1",
                "team_label": "A",
                "start_frame": 10,
                "end_frame": 100,
                "review_status": "ready_for_operator_review",
                "recommended_player": {"player_id": "p1", "player_name": "One"},
                "roster_candidates": [{"player_id": "p1"}, {"player_id": "p2"}],
                "allowed_actions": [
                    "confirm_recommended_player",
                    "assign_roster_player",
                    "mark_unresolved",
                    "open_debug_context",
                ],
                "visual_evidence": {
                    "anchor_crops": [{"anchor_crop_id": "crop-1", "artifact": "crop.jpg"}],
                },
            },
            {
                "review_card_key": "card-2",
                "candidate_subject_id": "subject-2",
                "team_label": "A",
                "start_frame": 110,
                "end_frame": 180,
                "review_status": "blocked_conflict",
                "recommended_player": {"player_id": "p2"},
                "roster_candidates": [{"player_id": "p2"}],
                "allowed_actions": ["mark_unresolved", "open_debug_context"],
            },
        ],
    }


def match_doc() -> dict:
    return {
        "teams": [
            {
                "id": "team-a",
                "name": "Alpha",
                "players": [
                    {"id": "p1", "name": "One"},
                    {"id": "p2", "name": "Two"},
                    {"id": "p9", "name": "Nine"},
                ],
            },
            {
                "id": "team-b",
                "name": "Beta",
                "players": [{"id": "b1", "name": "Opponent"}],
            },
        ]
    }


class IdentityRosterSubjectReviewStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        (self.path / REVIEW_ARTIFACT_FILENAME).write_text(json.dumps(artifact()), encoding="utf-8")
        (self.path / "crop.jpg").write_bytes(b"crop")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_seeded_assignment(
        self,
        *,
        conflicts: list[dict] | None = None,
    ) -> None:
        seeds = {"schema_version": "0.1.0", "decisions": []}
        seeded = {
            "schema_version": "0.1.0",
            "source": {
                "operator_seeds_digest": canonical_digest(seeds),
                "candidate_identity_digest": canonical_digest({}),
                "timeline_digest": canonical_digest({}),
                "tracklets_digest": canonical_digest({}),
                "whole_subject_review_decisions_digest": canonical_digest({}),
            },
            "accepted_assignments": [
                {
                    "candidate_subject_id": "subject-1",
                    "start_frame": 10,
                    "end_frame": 100,
                    "tracklet_ids": ["tracklet-1"],
                    "assigned_player": {
                        "player_id": "p1",
                        "name": "One",
                        "team_label": "A",
                    },
                    "seed_observations": ["observation-1"],
                }
            ],
            "conflicts": conflicts or [],
            "summary": {
                "subjects_resolved_after_seeding": 1,
                "tracklets_resolved_after_seeding": 1,
                "frames_resolved_after_seeding": 91,
                "conflicts_created": len(conflicts or []),
            },
            "safety": {"production_identity_untouched": True},
        }
        (self.path / SEEDS_FILENAME).write_text(json.dumps(seeds), encoding="utf-8")
        (self.path / OUTPUT_FILENAME).write_text(json.dumps(seeded), encoding="utf-8")

    def test_saves_whole_subject_decision_without_touching_production_artifacts(self) -> None:
        production = self.path / "player_identity_assignments.json"
        production.write_text('{"keep": true}', encoding="utf-8")

        state = save_identity_roster_subject_review(
            self.path,
            [{"review_card_key": "card-1", "decision": "assign_roster_player", "player_id": "p2"}],
            updated_at="fixed",
        )

        self.assertEqual(state["summary"]["reviewed_cards"], 1)
        self.assertEqual(state["cards"][0]["operator_decision"]["player_id"], "p2")
        self.assertEqual(production.read_text(encoding="utf-8"), '{"keep": true}')
        self.assertTrue((self.path / REVIEW_DECISIONS_FILENAME).exists())
        self.assertFalse(state["safety"]["writes_player_identity_assignments"])

    def test_initial_audit_seed_completes_card_and_reduces_pending_queue(self) -> None:
        self.write_seeded_assignment()

        state = load_identity_roster_subject_review(self.path, match_doc=match_doc())

        card = next(
            row for row in state["cards"]
            if row["candidate_subject_id"] == "subject-1"
        )
        self.assertEqual(card["review_status"], "completed_by_initial_audit")
        self.assertFalse(card["requires_operator_review"])
        self.assertEqual(state["summary"]["initial_audit_completed_cards"], 1)
        self.assertEqual(state["summary"]["pending_cards"], 1)
        self.assertEqual(
            state["initial_audit_integration"]["metrics"]["review_cards_reduced"],
            1,
        )
        self.assertTrue((self.path / REPORT_FILENAME).exists())

    def test_initial_audit_completed_card_cannot_be_reassigned(self) -> None:
        self.write_seeded_assignment()

        with self.assertRaisesRegex(ValueError, "blocked"):
            save_identity_roster_subject_review(
                self.path,
                [{
                    "review_card_key": "card-1",
                    "decision": "assign_roster_player",
                    "player_id": "p2",
                }],
                match_doc=match_doc(),
            )

    def test_initial_audit_conflict_stays_assignable(self) -> None:
        self.write_seeded_assignment(
            conflicts=[{
                "candidate_subject_ids": ["subject-1"],
                "reason_codes": ["seed_observations_disagree"],
            }]
        )

        state = load_identity_roster_subject_review(self.path, match_doc=match_doc())
        card = next(
            row for row in state["cards"]
            if row["candidate_subject_id"] == "subject-1"
        )

        self.assertEqual(card["review_status"], "blocked_seed_conflict")
        self.assertTrue(card["requires_operator_review"])
        self.assertIn("assign_roster_player", card["allowed_actions"])

    def test_blocked_action_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "blocked"):
            save_identity_roster_subject_review(
                self.path,
                [{"review_card_key": "card-2", "decision": "confirm_recommended_player"}],
            )

    def test_conflict_allows_explicit_roster_assignment_for_legacy_contract(self) -> None:
        state = save_identity_roster_subject_review(
            self.path,
            [{"review_card_key": "card-2", "decision": "assign_roster_player", "player_id": "p2"}],
            updated_at="fixed",
        )

        card = state["cards"][1]
        self.assertIn("assign_roster_player", card["allowed_actions"])
        self.assertNotIn("confirm_recommended_player", card["allowed_actions"])
        self.assertEqual(card["operator_decision"]["player_id"], "p2")

    def test_assignment_requires_card_roster_candidate(self) -> None:
        with self.assertRaisesRegex(ValueError, "operator roster options"):
            save_identity_roster_subject_review(
                self.path,
                [{"review_card_key": "card-1", "decision": "assign_roster_player", "player_id": "p9"}],
            )

    def test_full_same_team_roster_is_available_without_expanding_ranked_candidates(self) -> None:
        state = load_identity_roster_subject_review(self.path, match_doc=match_doc())

        card = state["cards"][0]
        self.assertEqual([row["player_id"] for row in card["roster_candidates"]], ["p1", "p2"])
        self.assertEqual(
            [row["player_id"] for row in card["operator_roster_options"]],
            ["p9", "p1", "p2"],
        )
        self.assertEqual(card["decision_contract"]["decision_schema"]["player_id"], ["p9", "p1", "p2"])

    def test_explicit_assignment_accepts_full_same_team_roster(self) -> None:
        state = save_identity_roster_subject_review(
            self.path,
            [{"review_card_key": "card-1", "decision": "assign_roster_player", "player_id": "p9"}],
            match_doc=match_doc(),
            updated_at="fixed",
        )

        self.assertEqual(state["cards"][0]["operator_decision"]["player_id"], "p9")

    def test_seeded_override_accepts_same_team_player_hidden_from_normal_options(
        self,
    ) -> None:
        source = artifact()
        source["cards"][1]["start_frame"] = 50
        source["cards"][1]["end_frame"] = 80
        source["cards"][1]["roster_candidates"] = [{"player_id": "p1"}]
        (self.path / REVIEW_ARTIFACT_FILENAME).write_text(
            json.dumps(source),
            encoding="utf-8",
        )
        self.write_seeded_assignment()

        reduced = load_identity_roster_subject_review(self.path, match_doc=match_doc())
        card = next(
            row for row in reduced["cards"] if row["review_card_key"] == "card-2"
        )
        self.assertNotIn(
            "p1",
            [row["player_id"] for row in card["operator_roster_options"]],
        )

        state = save_identity_roster_subject_review(
            self.path,
            [
                {
                    "review_card_key": "card-2",
                    "decision": "assign_roster_player",
                    "player_id": "p1",
                }
            ],
            match_doc=match_doc(),
            allow_seeded_override=True,
            updated_at="fixed",
        )

        saved = next(
            row for row in state["cards"] if row["review_card_key"] == "card-2"
        )
        self.assertEqual(saved["operator_decision"]["player_id"], "p1")

    def test_explicit_assignment_rejects_other_team_player(self) -> None:
        with self.assertRaisesRegex(ValueError, "operator roster options"):
            save_identity_roster_subject_review(
                self.path,
                [{"review_card_key": "card-1", "decision": "assign_roster_player", "player_id": "b1"}],
                match_doc=match_doc(),
            )

    def test_changed_contract_marks_previous_decisions_stale(self) -> None:
        save_identity_roster_subject_review(
            self.path,
            [{"review_card_key": "card-1", "decision": "mark_unresolved"}],
            updated_at="fixed",
        )
        changed = artifact()
        changed["cards"][0]["allowed_actions"] = ["mark_unresolved"]
        (self.path / REVIEW_ARTIFACT_FILENAME).write_text(json.dumps(changed), encoding="utf-8")

        state = load_identity_roster_subject_review(self.path)

        self.assertFalse(state["decisions_fresh"])
        self.assertEqual(state["summary"]["reviewed_cards"], 0)
        self.assertEqual(state["summary"]["stale_decisions"], 1)

    def test_clear_decision_is_idempotent(self) -> None:
        save_identity_roster_subject_review(
            self.path,
            [{"review_card_key": "card-1", "decision": "mark_unresolved"}],
            updated_at="first",
        )
        state = save_identity_roster_subject_review(
            self.path,
            [{"review_card_key": "card-1", "decision": "clear_decision"}],
            updated_at="second",
        )

        self.assertEqual(state["summary"]["reviewed_cards"], 0)
        self.assertEqual(state["summary"]["pending_cards"], 2)

    def test_operator_telemetry_is_deduplicated_and_caps_idle_time(self) -> None:
        event = {
            "event_id": "event-1",
            "session_id": "session-1",
            "event_type": "card_opened",
            "occurred_at": "2026-07-21T10:00:00+00:00",
            "active_delta_seconds": 120,
            "review_card_key": "card-1",
        }
        save_identity_roster_subject_review(self.path, [], telemetry_events=[event])
        state = save_identity_roster_subject_review(
            self.path,
            [{
                "update_id": "update-1",
                "review_card_key": "card-1",
                "decision": "assign_roster_player",
                "player_id": "p2",
            }],
            telemetry_events=[event],
        )

        telemetry = state["operator_telemetry"]
        self.assertEqual(telemetry["active_review_seconds"], 30.0)
        self.assertEqual(telemetry["cards_opened"], 1)
        self.assertEqual(telemetry["cards_decided"], 1)
        self.assertEqual(telemetry["manual_assignment_count"], 1)

    def test_decision_changes_count_once_for_retried_update(self) -> None:
        save_identity_roster_subject_review(
            self.path,
            [{"update_id": "first", "review_card_key": "card-1", "decision": "mark_unresolved"}],
        )
        changed = {
            "update_id": "changed",
            "review_card_key": "card-1",
            "decision": "assign_roster_player",
            "player_id": "p2",
        }
        save_identity_roster_subject_review(self.path, [changed])
        state = save_identity_roster_subject_review(self.path, [changed])

        self.assertEqual(state["operator_telemetry"]["decisions_changed"], 1)

    def test_crop_annotation_saves_and_reloads_by_anchor_crop_id(self) -> None:
        annotation = {
            "jersey_number": "10",
            "number_panel_bbox_normalized": [0.2, 0.18, 0.81, 0.72],
            "number_panel_artifact": "panels/crop-1.jpg",
        }
        save_identity_roster_subject_review(
            self.path,
            [
                {
                    "review_card_key": "card-1",
                    "anchor_crop_id": "crop-1",
                    "jersey_number_annotation": annotation,
                }
            ],
            updated_at="fixed",
        )

        state = load_identity_roster_subject_review(self.path)

        crop = state["cards"][0]["visual_evidence"]["anchor_crops"][0]
        self.assertEqual(
            crop["jersey_number_annotation"],
            {
                **annotation,
                "jersey_number_state": "number_confirmed",
            },
        )

    def test_operator_number_panel_annotation_persists_separately(self) -> None:
        source_sha = hashlib.sha256((self.path / "crop.jpg").read_bytes()).hexdigest()
        panel = {
            "number_panel_source_artifact": "crop.jpg",
            "number_panel_source_sha256": source_sha,
            "coordinate_space_version": "crop-normalized-v1",
            "number_panel_bbox_normalized": [0.1, 0.2, 0.8, 0.9],
            "glyph_height_px": 12,
            "annotation_source": "operator",
        }
        save_identity_roster_subject_review(
            self.path,
            [{"review_card_key": "card-1", "anchor_crop_id": "crop-1", "number_panel_annotation": panel}],
            updated_at="fixed",
        )

        crop = load_identity_roster_subject_review(self.path)["cards"][0]["visual_evidence"]["anchor_crops"][0]
        self.assertEqual(crop["number_panel_annotation"]["number_panel_source_sha256"], source_sha)
        self.assertIsNone(crop["jersey_number_annotation"])

    def test_invalid_or_machine_number_panel_annotation_is_rejected(self) -> None:
        for panel in (
            {"annotation_source": "assistant"},
            {
                "number_panel_source_artifact": "wrong.jpg",
                "coordinate_space_version": "v1",
                "number_panel_bbox_normalized": [0, 0, 1, 1],
            },
            {
                "number_panel_source_artifact": "crop.jpg",
                "coordinate_space_version": "v1",
                "number_panel_bbox_normalized": [0.5, 0.2, 0.4, 0.9],
            },
        ):
            with self.assertRaises(ValueError):
                save_identity_roster_subject_review(
                    self.path,
                    [{"review_card_key": "card-1", "anchor_crop_id": "crop-1", "number_panel_annotation": panel}],
                )

    def test_invalid_crop_annotation_enum_or_ratio_is_rejected(self) -> None:
        for annotation in (
            {"jersey_number": "abc"},
            {"digit_visibility": "visible"},
            {"panel_height_ratio": 1.1},
            {"number_panel_bbox_normalized": [0.2, 0.3, 0.2, 0.7]},
            {"number_panel_artifact": "../escape.jpg"},
        ):
            with self.assertRaises(ValueError):
                save_identity_roster_subject_review(
                    self.path,
                    [
                        {
                            "review_card_key": "card-1",
                            "anchor_crop_id": "crop-1",
                            "jersey_number_annotation": annotation,
                        }
                    ],
                )

    def test_unknown_crop_annotation_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown anchor_crop_id"):
            save_identity_roster_subject_review(
                self.path,
                [
                    {
                        "review_card_key": "card-1",
                        "anchor_crop_id": "unknown-crop",
                        "jersey_number_annotation": {},
                    }
                ],
            )

    def test_annotation_only_update_preserves_decision_and_identity_safety(self) -> None:
        save_identity_roster_subject_review(
            self.path,
            [{"review_card_key": "card-1", "decision": "mark_unresolved"}],
            updated_at="first",
        )
        state = save_identity_roster_subject_review(
            self.path,
            [
                {
                    "review_card_key": "card-1",
                    "anchor_crop_id": "crop-1",
                    "jersey_number_annotation": {},
                }
            ],
            updated_at="second",
        )

        self.assertEqual(state["cards"][0]["operator_decision"]["decision"], "mark_unresolved")
        self.assertFalse(state["safety"]["writes_player_identity_assignments"])
        self.assertFalse(state["safety"]["mutates_production_identity"])

    def test_fresh_pre_audit_attaches_to_matching_crop(self) -> None:
        self._write_pre_audit()

        crop = load_identity_roster_subject_review(self.path)["cards"][0]["visual_evidence"]["anchor_crops"][0]

        self.assertEqual(crop["jersey_number_pre_audit"]["anchor_crop_id"], "crop-1")
        self.assertEqual(
            crop["jersey_number_pre_audit"]["jersey_number_visual_diagnostics"]["digit_signal"],
            "likely_partial",
        )
        self.assertIsNone(crop["jersey_number_annotation"])

    def test_stale_or_mismatched_pre_audit_is_ignored(self) -> None:
        for mutate in (
            lambda document: document["source"].update({"subject_review_digest": "stale"}),
            lambda document: document["suggestions"][0].update({"crop_sha256": "mismatch"}),
            lambda document: document.update({"schema_version": "0.1.0"}),
            lambda document: document["suggestions"][0].update({"digit_visibility": "full"}),
        ):
            document = self._pre_audit_document()
            mutate(document)
            (self.path / VISUAL_PRE_AUDIT_FILENAME).write_text(json.dumps(document), encoding="utf-8")

            crop = load_identity_roster_subject_review(self.path)["cards"][0]["visual_evidence"]["anchor_crops"][0]

            self.assertIsNone(crop["jersey_number_pre_audit"])

    def test_pre_audit_remains_separate_from_manual_annotation(self) -> None:
        save_identity_roster_subject_review(
            self.path,
            [{
                "review_card_key": "card-1",
                "anchor_crop_id": "crop-1",
                "jersey_number_annotation": {"kit_profile": "manual-kit"},
            }],
        )
        self._write_pre_audit()

        crop = load_identity_roster_subject_review(self.path)["cards"][0]["visual_evidence"]["anchor_crops"][0]

        self.assertEqual(crop["jersey_number_annotation"]["kit_profile"], "manual-kit")
        self.assertEqual(
            crop["jersey_number_pre_audit"]["jersey_number_visual_diagnostics"]["digit_signal"],
            "likely_partial",
        )

    def _write_pre_audit(self) -> None:
        (self.path / VISUAL_PRE_AUDIT_FILENAME).write_text(
            json.dumps(self._pre_audit_document()), encoding="utf-8"
        )

    def _pre_audit_document(self) -> dict:
        source = artifact()
        crop_row = {
            "review_card_key": "card-1",
            "candidate_subject_id": "subject-1",
            "anchor_crop_id": "crop-1",
            "artifact": "crop.jpg",
        }
        suggestion = {
            **crop_row,
            "source_review_digest": canonical_digest(source),
            "source_crop_digest": canonical_digest(crop_row),
            "crop_sha256": hashlib.sha256((self.path / "crop.jpg").read_bytes()).hexdigest(),
            "status": "audited",
            "jersey_number_visual_diagnostics": {"digit_signal": "likely_partial"},
        }
        suggestion["row_digest"] = canonical_digest(suggestion)
        return {
            "schema_version": "0.2.0",
            "mode": "shadow_visual_pre_audit",
            "algorithm": {
                "name": "identity_jersey_number_visual_pre_audit",
                "version": "1.1.0",
                "parameters": {},
            },
            "source": {
                "subject_review_digest": canonical_digest(source),
                "review_crop_entries_digest": canonical_digest([crop_row]),
            },
            "safety": {
                "eligible_for_training": False,
                "mutates_candidate_identity": False,
                "mutates_production_identity": False,
            },
            "suggestions": [suggestion],
        }


if __name__ == "__main__":
    unittest.main()
