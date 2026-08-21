from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from copy import deepcopy

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_reviewed_hot_state import (
    FILENAME,
    ReviewedIdentityHotStateError,
    assert_hot_state_version,
    hot_context,
    load_or_rebuild_review_hot_state,
    update_hot_state_after_deferred_save,
)
from app.services.identity_reviewed_coverage import summarize_effective_observations
from app.services.identity_reviewed_progress import project_reviewed_identity_progress


def _progress() -> dict:
    unit = {
        "candidate_subject_id": "subject-1",
        "scope_kind": "whole_subject",
        "source_team_label": "A",
        "effective_team_label": "A",
        "tracklet_ids": ["t-1"],
        "detected_pairs": [("t-1", 10), ("t-1", 11)],
        "detected_observation_count": 2,
        "frame_start": 10,
        "frame_end": 11,
        "current_resolution_status": "pending_high_priority",
        "priority": "high",
        "operator_actionable": True,
        "has_operator_visual_evidence": True,
        "visual_evidence": {"kind": "identity_continuity", "anchor_crops": [{"artifact": "crop.jpg"}]},
    }
    return {
        "schema_version": "2.8.0",
        "status": "ready",
        "source_snapshot_digest": "snapshot",
        "next_cases": [dict(unit)],
        "optional_audit_cases": [],
        "summary": {"important_decisions_remaining": 1, "semantic_decisions_remaining": 1, "coverage_decisions_remaining": 0, "material_continuity_decisions_remaining": 0, "optional_audit_cases_remaining": 0},
        "_internal_review_units": [unit],
    }


class ReviewedIdentityHotStateTests(unittest.TestCase):
    def test_reuses_materialized_state_without_progress_rebuild_and_hides_pairs(self) -> None:
        with _workspace() as root, patch(
            "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
            return_value=_progress(),
        ) as build:
            state = load_or_rebuild_review_hot_state(root, _match())
            self.assertEqual(build.call_count, 1)
            reused = load_or_rebuild_review_hot_state(root, _match())
            self.assertEqual(build.call_count, 1)
            context = hot_context(reused, "subject-1")
            self.assertEqual(context["review_state_version"], 1)
            self.assertNotIn("detected_pairs", context)
            self.assertEqual(context["visual_evidence"]["anchor_crops"][0]["artifact"], "crop.jpg")
            self.assertEqual(reused["internal_review_units"][0]["detected_pairs"], [["t-1", 10], ["t-1", 11]])

    def test_corrupt_hot_state_recovers_with_one_cold_rebuild(self) -> None:
        with _workspace() as root:
            (root / FILENAME).write_text("not json", encoding="utf-8")
            with patch(
                "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
                return_value=_progress(),
            ) as build:
                state = load_or_rebuild_review_hot_state(root, _match())
            self.assertEqual(build.call_count, 1)
            self.assertEqual(state["state_version"], 1)

    def test_stale_state_version_rejects_before_any_write(self) -> None:
        with self.assertRaises(ReviewedIdentityHotStateError) as raised:
            assert_hot_state_version({"state_version": 2}, 1)
        self.assertEqual(raised.exception.code, "review_state_stale")

    def test_deferred_save_updates_derived_queue_without_progress_rebuild(self) -> None:
        with _workspace() as root, patch(
            "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
            return_value=_progress(),
        ):
            state = load_or_rebuild_review_hot_state(root, _match())
            unit = state["internal_review_units"][0]
            updated = update_hot_state_after_deferred_save(
                root,
                _match(),
                state,
                unit,
                {"action": "assign_roster_player", "player_id": "p1", "team_label": "A"},
                "after-save",
            )
            self.assertEqual(updated["state_version"], 2)
            self.assertEqual(updated["progress"]["next_cases"], [])
            self.assertEqual(updated["internal_review_units"][0]["current_resolution_status"], "reviewed_by_operator")

    def test_materialization_derives_exact_whole_source_digest_from_one_candidate_and_tracklet_read(self) -> None:
        with _workspace() as root:
            _write_json(root / "identity_candidate_shadow.json", {
                "subjects": [{"candidate_subject_id": "subject-1", "tracklet_ids": ["t-1"]}],
            })
            _write_json(root / "tracklets.json", {
                "tracklets": [{
                    "tracklet_id": "t-1",
                    "positions_m": [
                        {"frame": 11, "status": "detected", "source": "detected"},
                        {"frame": 10, "status": "detected", "source": "detected"},
                        {"frame": 12, "status": "predicted", "source": "predicted"},
                    ],
                }],
            })
            with patch(
                "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
                return_value=_progress(),
            ):
                state = load_or_rebuild_review_hot_state(root, _match())
            self.assertEqual(
                state["internal_review_units"][0]["source_ownership_digest"],
                canonical_digest({
                    "candidate_subject_id": "subject-1",
                    "tracklet_ids": ["t-1"],
                    "observations": [
                        {"tracklet_id": "t-1", "frame": 10},
                        {"tracklet_id": "t-1", "frame": 11},
                    ],
                }),
            )

    def test_materialization_keeps_existing_temporal_split_context_without_context_rebuild(self) -> None:
        with _workspace() as root:
            progress = _progress()
            progress["_internal_review_units"][0].update({
                "review_target_id": "target-1",
                "scope_kind": "canonical_segment",
                "source_ownership_digest": "target-digest",
            })
            progress["next_cases"][0].update({
                "review_target_id": "target-1",
                "scope_kind": "canonical_segment",
                "source_ownership_digest": "target-digest",
            })
            _write_json(root / "reviewed_identity_mixed_players.json", {
                "cases": [{
                    "original_issue": "inline_temporal_split",
                    "source": {
                        "scope_kind": "canonical_segment",
                        "candidate_subject_id": "subject-1",
                        "review_target_id": "target-1",
                        "continuity_group_id": None,
                        "source_ownership_digest": "target-digest",
                    },
                    "resolution_status": "resolved",
                    "split_after_frames": [10],
                    "segment_assignments": [{"action": "assign_team", "team_label": "A"}],
                    "split_semantic_digest": "split-digest",
                }],
            })
            with patch(
                "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
                return_value=progress,
            ):
                state = load_or_rebuild_review_hot_state(root, _match())
            context = hot_context(state, "subject-1", "target-1")
            self.assertEqual(context["temporal_split"]["split_after_frames"], [10])
            self.assertEqual(context["temporal_split"]["split_semantic_digest"], "split-digest")

    def test_materialization_normalizes_server_only_sets_before_writing_json(self) -> None:
        with _workspace() as root:
            progress = _progress()
            progress["_internal_review_units"][0]["detected_team_labels"] = {"A", "B"}
            with patch(
                "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
                return_value=progress,
            ):
                state = load_or_rebuild_review_hot_state(root, _match())
            self.assertEqual(state["internal_review_units"][0]["detected_team_labels"], ["A", "B"])
            persisted = json.loads((root / FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(persisted["internal_review_units"][0]["detected_team_labels"], ["A", "B"])

    def test_hot_projection_matches_canonical_policy_after_non_naming_decision(self) -> None:
        """Hot re-projection follows the existing coverage policy exactly."""
        with _workspace() as root:
            match = _complete_roster_match()
            units = [_coverage_unit("a"), _coverage_unit("b")]
            inputs = _projection_inputs(match, units)
            initial = project_reviewed_identity_progress(
                units, match, inputs, include_internal_units=True,
            )
            state = {
                "state_version": 4,
                "progress": {key: value for key, value in initial.items() if key != "_internal_review_units"},
                "internal_review_units": initial["_internal_review_units"],
                "unit_lookup": {"a\u001f": 0, "b\u001f": 1},
                "source_index": {},
                "projection_inputs": inputs,
                "roster_options": [{"player_id": "p1", "team_label": "A"}],
            }
            updated = update_hot_state_after_deferred_save(
                root,
                match,
                state,
                state["internal_review_units"][0],
                {"action": "unresolved"},
                "after-unresolved",
            )
            reference_units = [dict(unit) for unit in initial["_internal_review_units"]]
            reference_units[0]["current_decision"] = {"action": "unresolved"}
            reference_units[0]["current_resolution_status"] = "reviewed_by_operator"
            reference = project_reviewed_identity_progress(reference_units, match, inputs)
            semantic_fields = (
                "next_cases", "optional_audit_cases", "summary", "coverage_readiness",
                "coverage_residuals", "workload", "optional_audit", "observations",
            )
            self.assertEqual(
                {key: updated["progress"][key] for key in semantic_fields},
                {key: reference[key] for key in semantic_fields},
            )

    def test_hot_optional_max_projection_matches_reference_for_named_cross_team_and_unresolved(self) -> None:
        """Deferred MAX saves retain the canonical optional-queue semantics."""
        scenarios = (
            ("assign_roster_player", "p1", "A"),
            ("assign_roster_player", "p2", "B"),
            ("unresolved", None, "A"),
        )
        semantic_fields = (
            "optional_audit_cases",
            "optional_audit",
            "summary",
            "coverage_readiness",
            "coverage_residuals",
        )
        for action, player_id, effective_team in scenarios:
            with self.subTest(action=action, player_id=player_id), _workspace() as root:
                match, inputs, units = _optional_max_inputs()
                initial = project_reviewed_identity_progress(
                    units,
                    match,
                    inputs,
                    include_internal_units=True,
                )
                state = {
                    "state_version": 8,
                    "progress": {
                        key: value for key, value in initial.items()
                        if key not in {"_internal_review_units", "_projection_inputs"}
                    },
                    "internal_review_units": deepcopy(initial["_internal_review_units"]),
                    "unit_lookup": {"broad\u001f": 0, "narrow\u001f": 1},
                    "source_index": {},
                    "projection_inputs": inputs,
                    "roster_options": [
                        {"player_id": "p1", "team_label": "A"},
                        {"player_id": "p2", "team_label": "B"},
                    ],
                }
                saved_decision = {"action": action}
                if player_id:
                    saved_decision["player_id"] = player_id
                updated = update_hot_state_after_deferred_save(
                    root,
                    match,
                    state,
                    state["internal_review_units"][0],
                    saved_decision,
                    f"after-{action}-{player_id or 'none'}",
                )

                reference_units = deepcopy(initial["_internal_review_units"])
                reference_units[0]["current_decision"] = saved_decision
                reference_units[0]["current_resolution_status"] = "reviewed_by_operator"
                reference_units[0]["priority"] = None
                reference_units[0]["canonical_player_id"] = player_id
                reference_units[0]["effective_team_label"] = effective_team
                reference = project_reviewed_identity_progress(
                    reference_units,
                    match,
                    inputs,
                )

                self.assertEqual(
                    {key: updated["progress"][key] for key in semantic_fields},
                    {key: reference[key] for key in semantic_fields},
                )
                if player_id == "p2":
                    self.assertEqual(
                        updated["progress"]["optional_audit"]["pending_named_gain"],
                        0,
                    )

    def test_rebuild_revision_is_never_reused_after_hot_state_invalidation(self) -> None:
        with _workspace() as root, patch(
            "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
            return_value=_progress(),
        ):
            first = load_or_rebuild_review_hot_state(root, _match())
            (root / FILENAME).unlink()
            second = load_or_rebuild_review_hot_state(root, _match())
        self.assertGreater(second["state_version"], first["state_version"])

    def test_roster_change_invalidates_an_otherwise_fresh_materialization(self) -> None:
        with _workspace() as root, patch(
            "app.services.identity_reviewed_hot_state.build_reviewed_identity_progress",
            return_value=_progress(),
        ) as build:
            load_or_rebuild_review_hot_state(root, _match())
            changed = _match()
            changed["teams"][0]["players"].append({"id": "p2", "name": "Second"})
            load_or_rebuild_review_hot_state(root, changed)
        self.assertEqual(build.call_count, 2)


def _match() -> dict:
    return {"id": "hot-state", "teams": [{"team_label": "A", "players": [{"id": "p1", "name": "Player"}]}]}


def _complete_roster_match() -> dict:
    return {
        "id": "hot-coverage",
        "identity_review_scope": {"teams": {"A": "complete_roster", "B": "team_stats_only"}},
        "teams": [{"team_label": "A", "players": [{"id": "p1", "name": "Player"}]}],
    }


def _coverage_unit(subject_id: str) -> dict:
    return {
        "candidate_subject_id": subject_id,
        "scope_kind": "whole_subject",
        "source_team_label": "A",
        "effective_team_label": "A",
        "tracklet_ids": ["t-1"],
        "detected_pairs": [("t-1", 10), ("t-1", 11)],
        "detected_observation_count": 2,
        "detected_frame_count": 2,
        "detected_time_sec": 0.08,
        "current_resolution_status": "pending_optional",
        "operator_actionable": True,
        "has_operator_visual_evidence": True,
        "visual_evidence": {"anchor_crops": [{"artifact": "crop.jpg"}]},
        "reason_codes": ["long_unresolved_safe_anonymous"],
    }


def _projection_inputs(match: dict, units: list[dict]) -> dict:
    rows = [
        {"tracklet_id": "t-1", "frame": frame, "identity_status": "unresolved", "team_label": "A"}
        for frame in (10, 11)
    ]
    coverage, pair_index = summarize_effective_observations(rows, match)
    return {
        "match_id": match["id"],
        "coverage": coverage,
        "pair_index": [
            {"tracklet_id": tracklet_id, "frame": frame, "value": value}
            for (tracklet_id, frame), value in pair_index.items()
        ],
        "observed_pairs": [("t-1", 10), ("t-1", 11)],
        "technical_diagnostics": {},
        "mixed_players": {},
        "deferred_correction_context": {},
    }


def _optional_max_inputs() -> tuple[dict, dict, list[dict]]:
    match = {
        "id": "hot-optional-max",
        "identity_review_scope": {
            "teams": {"A": "complete_roster", "B": "team_stats_only"},
        },
        "teams": [
            {"team_label": "A", "players": [{"id": "p1", "name": "Player A"}]},
            {"team_label": "B", "players": [{"id": "p2", "name": "Player B"}]},
        ],
    }
    rows = [
        {
            "tracklet_id": "named",
            "frame": frame,
            "team_label": "A",
            "identity_status": "confirmed",
            "canonical_player_id": "p1",
            "play_area_status": "inside_play",
        }
        for frame in range(90)
    ] + [
        {
            "tracklet_id": "unresolved",
            "frame": frame,
            "team_label": "A",
            "identity_status": "unresolved",
            "canonical_player_id": None,
            "play_area_status": "inside_play",
        }
        for frame in range(90, 100)
    ]
    coverage, pair_index = summarize_effective_observations(rows, match)
    broad_pairs = [("unresolved", frame) for frame in range(90, 100)]
    narrow_pairs = [("unresolved", frame) for frame in range(95, 100)]

    def unit(subject_id: str, pairs: list[tuple[str, int]]) -> dict:
        return {
            "candidate_subject_id": subject_id,
            "scope_kind": "whole_subject",
            "source_team_label": "A",
            "effective_team_label": "A",
            "tracklet_ids": ["unresolved"],
            "detected_pairs": pairs,
            "detected_observation_count": len(pairs),
            "detected_frame_count": len(pairs),
            "detected_time_sec": len(pairs) / 25,
            "frame_start": pairs[0][1],
            "frame_end": pairs[-1][1],
            "current_decision": None,
            "current_resolution_status": "pending_optional",
            "priority": "optional",
            "operator_actionable": True,
            "has_operator_visual_evidence": True,
            "visual_evidence": {"kind": "identity_continuity", "anchor_crops": [{"artifact": "crop.jpg"}]},
            "reason_codes": ["long_unresolved_safe_anonymous"],
        }

    inputs = {
        "match_id": match["id"],
        "coverage": coverage,
        "pair_index": [
            {"tracklet_id": tracklet_id, "frame": frame, "value": value}
            for (tracklet_id, frame), value in pair_index.items()
        ],
        "observed_pairs": [
            (str(row["tracklet_id"]), int(row["frame"]))
            for row in rows
        ],
        "technical_diagnostics": {},
        "mixed_players": {},
        "deferred_correction_context": {},
    }
    return match, inputs, [unit("broad", broad_pairs), unit("narrow", narrow_pairs)]


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


class _workspace:
    def __enter__(self) -> Path:
        self.temporary = tempfile.TemporaryDirectory()
        return Path(self.temporary.name)

    def __exit__(self, *args: object) -> None:
        self.temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
