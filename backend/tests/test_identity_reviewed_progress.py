from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.services.identity_reviewed_coverage import paginate_progress, summarize_effective_observations
from app.services.identity_reviewed_progress import (
    _public_unit,
    build_reviewed_identity_progress,
    project_reviewed_identity_progress,
)


class ReviewedIdentityProgressTests(unittest.TestCase):
    def test_progress_uses_candidate_subjects_and_real_detected_positions(self) -> None:
        with _workspace() as root:
            _fixture(root)
            before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
            progress = build_reviewed_identity_progress(root, _match())
            self.assertEqual(progress["summary"]["review_units_total"], 6)
            self.assertEqual(progress["summary"]["important_decisions_remaining"], 0)
            self.assertEqual(progress["summary"]["optional_cases_remaining"], 2)
            self.assertEqual(progress["summary"]["safe_anonymous_units"], 1)
            self.assertEqual(progress["summary"]["structural_blockers"], 2)
            self.assertEqual(progress["summary"]["non_actionable_review_units"], 3)
            self.assertEqual(
                progress["summary"]["non_actionable_reason_counts"],
                {
                    "ambiguous_candidate_subject_membership": 2,
                    "mixed_team_subject": 1,
                },
            )
            long = _unit(progress, "long")
            self.assertEqual(long["tracklet_count"], 2)
            self.assertEqual(long["detected_observation_count"], 120)
            self.assertEqual(long["detected_frame_count"], 60)
            self.assertEqual(long["current_resolution_status"], "pending_optional")
            self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()})

    def test_operator_decision_marks_one_whole_subject_complete(self) -> None:
        with _workspace() as root:
            _fixture(root)
            _write(root / "reviewed_identity_slot_assignments.json", {
                "decisions": [{"candidate_subject_id": "long", "action": "assign_team", "team_label": "B"}],
                "reviewed_slots": [],
            })
            progress = build_reviewed_identity_progress(root, _match())
            long = _unit(progress, "long")
            self.assertEqual(long["current_resolution_status"], "reviewed_by_operator")
            self.assertEqual(progress["summary"]["completed_by_operator"], 1)
            self.assertEqual(progress["observations"]["operator_reviewed_observations"], 120)
            self.assertEqual(progress["summary"]["important_decisions_remaining"], 0)

    def test_many_long_unnamed_subjects_are_optional_not_blocking(self) -> None:
        with _workspace() as root:
            subjects = []
            tracklets = []
            for index in range(100):
                tracklet_id = f"long-{index}"
                tracklets.append(_tracklet(
                    tracklet_id,
                    "A" if index % 2 == 0 else "B",
                    range(1, 101),
                ))
                subjects.append({"candidate_subject_id": f"subject-{index}", "tracklet_ids": [tracklet_id]})
            _write(root / "tracklets.json", {"tracklets": tracklets})
            _write(root / "identity_candidate_shadow.json", {"subjects": subjects})

            progress = build_reviewed_identity_progress(root, _match())

            self.assertEqual(progress["summary"]["important_decisions_remaining"], 0)
            self.assertEqual(progress["summary"]["optional_cases_remaining"], 100)
            self.assertEqual(progress["next_cases"], [])

    def test_subject_with_only_non_inside_observations_creates_no_review_work(self) -> None:
        with _workspace() as root:
            outside = _tracklet("outside", "A", range(1, 31))
            for row in outside["positions_m"]:
                row["play_area_status"] = "outside_play"
            boundary = _tracklet("boundary", "A", range(31, 61))
            for row in boundary["positions_m"]:
                row["play_area_status"] = "boundary_transient"
            _write(root / "tracklets.json", {"tracklets": [outside, boundary]})
            _write(root / "identity_candidate_shadow.json", {"subjects": [
                {"candidate_subject_id": "outside", "tracklet_ids": ["outside"]},
                {"candidate_subject_id": "boundary", "tracklet_ids": ["boundary"]},
            ]})
            _write(root / "identity_roster_subject_review_shadow.json", {"cards": [
                {
                    "candidate_subject_id": "outside",
                    "review_status": "blocked_conflict",
                    "requires_operator_review": True,
                    "visual_evidence": {"anchor_crops": [{"anchor_crop_id": "outside"}]},
                },
                {
                    "candidate_subject_id": "boundary",
                    "review_status": "blocked_conflict",
                    "requires_operator_review": True,
                    "visual_evidence": {"anchor_crops": [{"anchor_crop_id": "boundary"}]},
                },
            ]})

            progress = build_reviewed_identity_progress(root, _match())

            self.assertEqual(progress["summary"]["important_decisions_remaining"], 0)
            self.assertEqual(progress["summary"]["optional_cases_remaining"], 0)
            self.assertEqual(progress["summary"]["ignored_low_impact"], 2)
            self.assertEqual(progress["observations"]["total_detected_observations"], 0)
            self.assertEqual(progress["next_cases"], [])

    def test_legacy_requires_operator_review_without_conflict_is_not_blocking(self) -> None:
        with _workspace() as root:
            _single_subject(root, team="A", frames=range(1, 101), card={
                "review_status": "ready_for_operator_review",
                "requires_operator_review": True,
            })

            progress = build_reviewed_identity_progress(root, _match())

            self.assertEqual(progress["summary"]["important_decisions_remaining"], 0)
            self.assertEqual(_unit(progress, "subject")["current_resolution_status"], "pending_optional")

    def test_legacy_blocked_conflict_for_missing_roster_name_is_not_blocking(self) -> None:
        with _workspace() as root:
            _single_subject(root, team="B", frames=range(1, 101), card={
                "review_status": "blocked_conflict",
                "requires_operator_review": True,
                "reason_codes": ["no_roster_identity_evidence"],
            })

            progress = build_reviewed_identity_progress(root, _match())

            self.assertEqual(progress["summary"]["important_decisions_remaining"], 0)
            self.assertEqual(_unit(progress, "subject")["current_resolution_status"], "pending_optional")

    def test_jersey_conflict_in_blockers_is_blocking(self) -> None:
        with _workspace() as root:
            _single_subject(root, team="A", frames=range(1, 101), card={
                "review_status": "blocked_conflict",
                "requires_operator_review": True,
                "reason_codes": ["no_roster_identity_evidence"],
                "blockers": ["jersey_number_roster_conflict"],
                "visual_evidence": {"anchor_crops": [{"anchor_crop_id": "crop-1"}]},
            })

            progress = build_reviewed_identity_progress(root, _match())

            self.assertEqual(progress["summary"]["important_decisions_remaining"], 1)
            self.assertEqual(_unit(progress, "subject")["current_resolution_status"], "pending_high_priority")

    def test_jersey_conflict_without_visual_evidence_is_optional(self) -> None:
        with _workspace() as root:
            _single_subject(root, team="A", frames=range(1, 101), card={
                "review_status": "blocked_conflict",
                "requires_operator_review": True,
                "reason_codes": ["no_roster_identity_evidence"],
                "blockers": ["jersey_number_roster_conflict"],
                "visual_evidence": {"anchor_crops": []},
            })

            progress = build_reviewed_identity_progress(root, _match())
            subject = _unit(progress, "subject")

            self.assertEqual(progress["summary"]["important_decisions_remaining"], 0)
            self.assertEqual(progress["summary"]["optional_cases_remaining"], 1)
            self.assertEqual(subject["current_resolution_status"], "pending_optional")
            self.assertIn("review_card_conflict", subject["reason_codes"])
            self.assertIn(
                "semantic_conflict_without_visual_evidence",
                subject["reason_codes"],
            )
            self.assertEqual(progress["next_cases"], [])

    def test_non_semantic_blocker_remains_optional(self) -> None:
        with _workspace() as root:
            _single_subject(root, team="B", frames=range(1, 101), card={
                "review_status": "blocked_conflict",
                "requires_operator_review": True,
                "reason_codes": ["no_roster_identity_evidence"],
                "blockers": ["insufficient_visual_evidence"],
            })

            progress = build_reviewed_identity_progress(root, _match())

            self.assertEqual(progress["summary"]["important_decisions_remaining"], 0)
            self.assertEqual(_unit(progress, "subject")["current_resolution_status"], "pending_optional")

    def test_semantic_quality_flag_is_blocking(self) -> None:
        with _workspace() as root:
            _single_subject(root, team="A", frames=range(1, 101), card={
                "review_status": "blocked_conflict",
                "requires_operator_review": True,
                "reason_codes": ["no_roster_identity_evidence"],
                "quality_flags": ["production_anchor_team_mismatch"],
                "visual_evidence": {"anchor_crops": [{"anchor_crop_id": "crop-1"}]},
            })

            progress = build_reviewed_identity_progress(root, _match())

            self.assertEqual(progress["summary"]["important_decisions_remaining"], 1)
            self.assertEqual(_unit(progress, "subject")["current_resolution_status"], "pending_high_priority")

    def test_semantic_review_card_conflict_is_blocking_until_operator_resolves_it(self) -> None:
        with _workspace() as root:
            _single_subject(root, team="A", frames=range(1, 101), card={
                "review_status": "blocked_conflict",
                "requires_operator_review": True,
                "reason_codes": ["parallel_roster_candidate_conflict"],
                "visual_evidence": {"anchor_crops": [{"anchor_crop_id": "crop-1"}]},
            })

            before = build_reviewed_identity_progress(root, _match())
            self.assertEqual(before["summary"]["important_decisions_remaining"], 1)
            self.assertEqual(_unit(before, "subject")["current_resolution_status"], "pending_high_priority")

            _write(root / "reviewed_identity_slot_assignments.json", {
                "decisions": [{"candidate_subject_id": "subject", "action": "assign_team", "team_label": "A"}],
                "reviewed_slots": [],
            })
            after = build_reviewed_identity_progress(root, _match())
            self.assertEqual(after["summary"]["important_decisions_remaining"], 0)
            self.assertEqual(_unit(after, "subject")["current_resolution_status"], "reviewed_by_operator")

    def test_legacy_conflict_metadata_without_reasons_remains_blocking_with_crop(self) -> None:
        with _workspace() as root:
            _single_subject(root, team="A", frames=range(1, 101), card={
                "review_status": "blocked_conflict",
                "requires_operator_review": True,
                "reason_codes": [],
                "blockers": [],
                "visual_evidence": {"anchor_crops": [{"anchor_crop_id": "crop-1"}]},
            })

            progress = build_reviewed_identity_progress(root, _match())

            self.assertEqual(progress["summary"]["important_decisions_remaining"], 1)
            self.assertEqual(_unit(progress, "subject")["current_resolution_status"], "pending_high_priority")

    def test_team_conflict_without_visual_evidence_is_optional(self) -> None:
        with _workspace() as root:
            _team_conflict_subject(root, anchor_crops=[])

            progress = build_reviewed_identity_progress(root, _match())
            subject = _unit(progress, "subject")

            self.assertEqual(progress["summary"]["important_decisions_remaining"], 0)
            self.assertEqual(subject["current_resolution_status"], "pending_optional")
            self.assertIn("conflicting_detected_team_labels", subject["reason_codes"])
            self.assertIn(
                "semantic_conflict_without_visual_evidence",
                subject["reason_codes"],
            )

    def test_team_conflict_with_visual_evidence_is_not_a_whole_subject_task(self) -> None:
        with _workspace() as root:
            _team_conflict_subject(
                root,
                anchor_crops=[{"anchor_crop_id": "crop-1"}],
            )

            progress = build_reviewed_identity_progress(root, _match())

            subject = _unit(progress, "subject")
            self.assertEqual(progress["summary"]["important_decisions_remaining"], 0)
            self.assertEqual(subject["current_resolution_status"], "pending_high_priority")
            self.assertFalse(subject["operator_actionable"])
            self.assertEqual(subject["non_actionable_reason"], "mixed_team_subject")
            self.assertEqual(progress["next_cases"], [])
            self.assertFalse(progress["coverage_readiness"]["allows_finalize"])

    def test_progress_materializes_exact_detected_team_evidence(self) -> None:
        with _workspace() as root:
            _write(
                root / "tracklets.json",
                {
                    "tracklets": [
                        _tracklet("only-a", "A", [1]),
                        _tracklet("only-b", "B", [2]),
                        _tracklet("unknown", "U", [3]),
                        _tracklet("mixed-a", "A", [4]),
                        _tracklet("mixed-b", "B", [5]),
                    ]
                },
            )
            _write(
                root / "identity_candidate_shadow.json",
                {
                    "subjects": [
                        {
                            "candidate_subject_id": "only-a",
                            "tracklet_ids": ["only-a"],
                        },
                        {
                            "candidate_subject_id": "only-b",
                            "tracklet_ids": ["only-b"],
                        },
                        {
                            "candidate_subject_id": "unknown",
                            "tracklet_ids": ["unknown"],
                        },
                        {
                            "candidate_subject_id": "mixed",
                            "tracklet_ids": ["mixed-a", "mixed-b"],
                        },
                    ]
                },
            )

            progress = build_reviewed_identity_progress(root, _match())

            self.assertEqual(_unit(progress, "mixed")["source_team_label"], "U")
            context = progress["deferred_correction_context"]
            self.assertEqual(context["detected_team_evidence_status"], "ready")
            labels_by_subject = {
                row["candidate_subject_id"]: row["detected_team_labels"]
                for row in context["subjects"]
            }
            self.assertEqual(
                labels_by_subject,
                {
                    "mixed": ["A", "B"],
                    "only-a": ["A"],
                    "only-b": ["B"],
                    "unknown": [],
                },
            )

    def test_projected_queue_retains_conflict_filter_after_exact_team_evidence_is_stripped(self) -> None:
        match = {
            "id": "team-filter-projection",
            "identity_review_scope": {
                "teams": {"A": "complete_roster", "B": "team_stats_only"},
            },
            "teams": [{"team_label": "A"}, {"team_label": "B"}],
        }
        rows = [{
            "tracklet_id": "cross-team-tracklet",
            "frame": 10,
            "team_label": "B",
            "identity_status": "unresolved",
            "canonical_player_id": None,
            "play_area_status": "inside_play",
        }]
        coverage, pair_index = summarize_effective_observations(rows, match)
        full_required_unit = {
            "candidate_subject_id": "b-labelled-cross-team",
            "scope_kind": "whole_subject",
            "source_team_label": "B",
            "effective_team_label": "B",
            "coverage_team_label": "B",
            "detected_team_labels": ["A", "B"],
            "tracklet_ids": ["cross-team-tracklet"],
            "detected_pairs": [("cross-team-tracklet", 10)],
            "detected_observation_count": 1,
            "detected_frame_count": 1,
            "detected_time_sec": 0.04,
            "frame_start": 10,
            "frame_end": 10,
            "current_resolution_status": "pending_high_priority",
            "priority": "high",
            "operator_actionable": True,
            "has_operator_visual_evidence": True,
            "visual_evidence": {"anchor_crops": [{"artifact": "crop.jpg"}]},
            "reason_codes": ["identity_conflict"],
        }
        inputs = {
            "match_id": match["id"],
            "coverage": coverage,
            "pair_index": [
                {"tracklet_id": tracklet_id, "frame": frame, "value": value}
                for (tracklet_id, frame), value in pair_index.items()
            ],
            "observed_pairs": [("cross-team-tracklet", 10)],
            "mixed_players": {},
            "technical_diagnostics": {},
            "deferred_correction_context": {},
        }

        projected = project_reviewed_identity_progress(
            [full_required_unit], match, inputs,
        )
        compact_case = projected["next_cases"][0]
        self.assertNotIn("detected_team_labels", compact_case)
        self.assertEqual(compact_case["filter_team_label"], "U")

        conflict_page = paginate_progress(projected, team_label="U")
        b_page = paginate_progress(projected, team_label="B")
        self.assertEqual(conflict_page["filters"]["counts"], {"all": 1, "A": 0, "B": 0, "U": 1})
        self.assertEqual([row["candidate_subject_id"] for row in conflict_page["next_cases"]], ["b-labelled-cross-team"])
        self.assertEqual(conflict_page["next_cases"][0]["filter_team_label"], "U")
        self.assertEqual(b_page["next_cases"], [])

    def test_public_unit_preserves_certain_team_b_navigation_label(self) -> None:
        full_unit = {
            "candidate_subject_id": "certain-b",
            "source_team_label": "B",
            "effective_team_label": "B",
            "coverage_team_label": "B",
            "detected_team_labels": ["B"],
            "operator_impact_kind": "named_coverage",
            "operator_impact_observation_gain": 120,
            "operator_impact_pp": 2.4,
        }

        public = _public_unit(full_unit)

        self.assertNotIn("detected_team_labels", public)
        self.assertEqual(public["filter_team_label"], "B")
        self.assertEqual(public["operator_impact_kind"], "named_coverage")
        self.assertEqual(public["operator_impact_observation_gain"], 120)
        self.assertEqual(public["operator_impact_pp"], 2.4)

    def test_short_unnamed_team_a_and_team_b_subjects_are_safe_anonymous(self) -> None:
        with _workspace() as root:
            _write(root / "tracklets.json", {"tracklets": [
                _tracklet("team-a", "A", [1]),
                _tracklet("team-b", "B", [2]),
            ]})
            _write(root / "identity_candidate_shadow.json", {"subjects": [
                {"candidate_subject_id": "team-a", "tracklet_ids": ["team-a"]},
                {"candidate_subject_id": "team-b", "tracklet_ids": ["team-b"]},
            ]})

            progress = build_reviewed_identity_progress(root, _match())

            self.assertEqual(progress["summary"]["important_decisions_remaining"], 0)
            self.assertEqual(progress["summary"]["safe_anonymous_units"], 2)
            self.assertEqual(_unit(progress, "team-a")["current_resolution_status"], "safe_anonymous")
            self.assertEqual(_unit(progress, "team-b")["current_resolution_status"], "safe_anonymous")

    def test_all_real_conflicts_remain_visible_without_an_arbitrary_cap(self) -> None:
        with _workspace() as root:
            tracklets = []
            subjects = []
            cards = []
            for index in range(7):
                tracklet_id = f"conflict-{index}"
                subject_id = f"subject-{index}"
                tracklets.append(_tracklet(tracklet_id, "A", range(1, 101)))
                subjects.append({"candidate_subject_id": subject_id, "tracklet_ids": [tracklet_id]})
                cards.append({
                    "candidate_subject_id": subject_id,
                    "review_status": "blocked_conflict",
                    "requires_operator_review": True,
                    "visual_evidence": {
                        "anchor_crops": [{"anchor_crop_id": f"crop-{index}"}],
                    },
                })
            _write(root / "tracklets.json", {"tracklets": tracklets})
            _write(root / "identity_candidate_shadow.json", {"subjects": subjects})
            _write(root / "identity_roster_subject_review_shadow.json", {"cards": cards})

            progress = build_reviewed_identity_progress(root, _match())

            self.assertEqual(progress["summary"]["important_decisions_remaining"], 7)
            self.assertEqual(len(progress["next_cases"]), 7)

    def test_mixed_player_subject_is_excluded_from_normal_queue(self) -> None:
        with _workspace() as root:
            _single_subject(
                root,
                team="A",
                frames=range(1, 101),
                card={
                    "visual_evidence": {
                        "anchor_crops": [{
                            "anchor_crop_id": "mixed-crop",
                            "tracklet_id": "tracklet",
                            "frame": 50,
                            "artifact": "crop.jpg",
                        }],
                    },
                },
            )
            _write(root / "reviewed_identity_mixed_players.json", {
                "schema_version": "1.0.0",
                "mode": "reviewed_identity_mixed_players",
                "cases": [{
                    "candidate_subject_id": "subject",
                    "resolution_status": "unresolved",
                    "source_tracklet_ids": ["tracklet"],
                    "observation_count": 100,
                    "frame_start": 1,
                    "frame_end": 100,
                }],
            })

            progress = build_reviewed_identity_progress(root, _match())

            self.assertEqual(progress["next_cases"], [])
            self.assertEqual(progress["review_units"], [])
            self.assertEqual(progress["mixed_players"]["summary"]["unresolved"], 1)


def _fixture(root: Path) -> None:
    tracklets = [
        _tracklet("l1", "U", range(1, 61)),
        _tracklet("l2", "U", range(1, 61)),
        _tracklet("optional", "A", range(70, 85)),
        _tracklet("noise", "A", [100]),
        _tracklet("mixed-a", "A", [110]),
        _tracklet("mixed-b", "B", [111]),
        _tracklet("ambiguous", "A", [120]),
    ]
    tracklets[0]["positions_m"].append({"frame": 61, "status": "predicted", "source": "predicted"})
    _write(root / "tracklets.json", {"tracklets": tracklets})
    _write(root / "identity_candidate_shadow.json", {"subjects": [
        {"candidate_subject_id": "long", "tracklet_ids": ["l1", "l2"]},
        {"candidate_subject_id": "optional", "tracklet_ids": ["optional"]},
        {"candidate_subject_id": "noise", "tracklet_ids": ["noise"]},
        {"candidate_subject_id": "mixed", "tracklet_ids": ["mixed-a", "mixed-b"]},
        {"candidate_subject_id": "ambiguous", "tracklet_ids": ["ambiguous"]},
        {"candidate_subject_id": "ambiguous-other", "tracklet_ids": ["ambiguous"]},
    ]})
    _write(root / "identity_roster_subject_review_shadow.json", {"cards": [{
        "candidate_subject_id": "mixed",
        "visual_evidence": {"anchor_crops": [{"anchor_crop_id": "mixed-crop"}]},
    }]})


def _single_subject(
    root: Path,
    *,
    team: str,
    frames: range | list[int],
    card: dict | None = None,
) -> None:
    _write(root / "tracklets.json", {"tracklets": [_tracklet("tracklet", team, frames)]})
    _write(root / "identity_candidate_shadow.json", {"subjects": [
        {"candidate_subject_id": "subject", "tracklet_ids": ["tracklet"]},
    ]})
    if card is not None:
        _write(root / "identity_roster_subject_review_shadow.json", {
            "cards": [{"candidate_subject_id": "subject", **card}],
        })


def _team_conflict_subject(root: Path, *, anchor_crops: list[dict]) -> None:
    _write(root / "tracklets.json", {"tracklets": [
        _tracklet("team-a", "A", range(1, 51)),
        _tracklet("team-b", "B", range(51, 101)),
    ]})
    _write(root / "identity_candidate_shadow.json", {"subjects": [{
        "candidate_subject_id": "subject",
        "tracklet_ids": ["team-a", "team-b"],
    }]})
    _write(root / "identity_roster_subject_review_shadow.json", {"cards": [{
        "candidate_subject_id": "subject",
        "visual_evidence": {"anchor_crops": anchor_crops},
    }]})


def _tracklet(tracklet_id: str, team: str, frames: range | list[int]) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "team_label": team,
        "positions_m": [
            {
                "frame": frame,
                "status": "detected",
                "source": "detected",
                "play_area_status": "inside_play",
            }
            for frame in frames
        ],
    }


def _match() -> dict:
    return {"id": "progress", "teams": []}


def _unit(progress: dict, subject_id: str) -> dict:
    return next(row for row in progress["review_units"] if row["candidate_subject_id"] == subject_id)


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


class _workspace:
    def __enter__(self) -> Path:
        self.temporary = tempfile.TemporaryDirectory()
        return Path(self.temporary.name)

    def __exit__(self, *args: object) -> None:
        self.temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
