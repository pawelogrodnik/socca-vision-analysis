from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_reviewed_hot_state import (
    FILENAME,
    ReviewedIdentityHotStateError,
    assert_hot_state_version,
    hot_context,
    load_or_rebuild_review_hot_state,
    update_hot_state_after_deferred_save,
)


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


def _match() -> dict:
    return {"id": "hot-state", "teams": [{"team_label": "A", "players": [{"id": "p1", "name": "Player"}]}]}


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
