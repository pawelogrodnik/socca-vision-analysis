from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.identity_reviewed_coverage import (
    apply_coverage_policy,
    paginate_progress,
    review_case_team_label,
    summarize_effective_observations,
    target_named_observations,
)
from app.services.identity_reviewed_progress import reviewed_snapshot_file_fingerprint


class ReviewedIdentityCoverageTests(unittest.TestCase):
    def test_complete_roster_selection_uses_named_target_shortfall(self) -> None:
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(880)
        ] + [
            _observation("unnamed", frame, "A", "unresolved", None)
            for frame in range(880, 1000)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        units = [
            _unit("a", [("unnamed", frame) for frame in range(880, 892)], visual=True),
            _unit("b", [("unnamed", frame) for frame in range(892, 899)], visual=True),
            _unit("c", [("unnamed", frame) for frame in range(899, 904)], visual=True),
        ]

        policy = apply_coverage_policy(units, coverage, pair_index, _scoped_match())
        residual = policy["residual_by_team"]["A"]

        self.assertEqual(target_named_observations(1_000, 0.90), 900)
        self.assertEqual(residual["required_named_gain"], 20)
        self.assertEqual(residual["selected_required_named_gain"], 24)
        self.assertEqual(
            [row["candidate_subject_id"] for row in policy["next_cases"]],
            ["a", "b", "c"],
        )

    def test_overlapping_candidate_gain_is_selected_by_unique_pairs(self) -> None:
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(886)
        ] + [
            _observation("unnamed", frame, "A", "unresolved", None)
            for frame in range(886, 1000)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        units = [
            _unit("a", [("unnamed", frame) for frame in range(886, 896)], visual=True),
            _unit("b", [("unnamed", frame) for frame in range(890, 900)], visual=True),
        ]

        policy = apply_coverage_policy(units, coverage, pair_index, _scoped_match())
        residual = policy["residual_by_team"]["A"]

        self.assertEqual(residual["required_named_gain"], 14)
        self.assertEqual(residual["available_actionable_named_gain"], 14)
        self.assertEqual(residual["selected_required_named_gain"], 14)
        self.assertEqual(len(policy["next_cases"]), 2)
        self.assertEqual(
            policy["next_cases"][1]["marginal_named_observation_gain"],
            4,
        )

    def test_golden_shaped_shortfall_selects_past_the_old_residual_budget_stop(
        self,
    ) -> None:
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(64_783)
        ] + [
            _observation("unnamed", frame, "A", "unresolved", None)
            for frame in range(64_783, 73_420)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        units = [
            _unit("high", [("unnamed", frame) for frame in range(64_783, 65_476)], visual=True),
            _unit("medium", [("unnamed", frame) for frame in range(65_476, 65_877)], visual=True),
            _unit("lower", [("unnamed", frame) for frame in range(65_877, 66_153)], visual=True),
        ]

        policy = apply_coverage_policy(units, coverage, pair_index, _scoped_match())
        residual = policy["residual_by_team"]["A"]

        self.assertEqual(target_named_observations(73_420, 0.90), 66_078)
        self.assertEqual(residual["required_named_gain"], 1_295)
        self.assertEqual(residual["available_actionable_named_gain"], 1_370)
        self.assertEqual(residual["selected_required_named_gain"], 1_370)
        self.assertEqual(residual["remaining_uncovered_named_gain"], 0)
        self.assertEqual(
            [row["candidate_subject_id"] for row in policy["next_cases"]],
            ["high", "medium", "lower"],
        )

    def test_insufficient_safe_evidence_surfaces_all_cases_and_quantifies_gap(
        self,
    ) -> None:
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(880)
        ] + [
            _observation("unnamed", frame, "A", "unresolved", None)
            for frame in range(880, 1_000)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        units = [
            _unit("safe-eight", [("unnamed", frame) for frame in range(880, 888)], visual=True),
            _unit("safe-five", [("unnamed", frame) for frame in range(888, 893)], visual=True),
            _unit("no-evidence", [("unnamed", frame) for frame in range(893, 943)], visual=False),
        ]

        policy = apply_coverage_policy(units, coverage, pair_index, _scoped_match())
        residual = policy["residual_by_team"]["A"]
        blocker = next(
            row
            for row in policy["readiness"]["blockers"]
            if row["code"] == "complete_roster_named_coverage_gap_unreachable"
        )

        self.assertEqual(residual["required_named_gain"], 20)
        self.assertEqual(residual["available_actionable_named_gain"], 13)
        self.assertEqual(residual["selected_required_named_gain"], 13)
        self.assertEqual(residual["remaining_uncovered_named_gain"], 7)
        self.assertEqual(residual["nonactionable_or_unavailable_gap"], 7)
        self.assertEqual(
            [row["candidate_subject_id"] for row in policy["next_cases"]],
            ["safe-eight", "safe-five"],
        )
        self.assertEqual(blocker["remaining_uncovered_named_gain"], 7)
        self.assertFalse(policy["readiness"]["allows_finalize"])

    def test_target_met_does_not_require_ordinary_named_coverage_cases(self) -> None:
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(905)
        ] + [
            _observation("unnamed", frame, "A", "unresolved", None)
            for frame in range(905, 1_000)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        case = _unit("optional-now", [("unnamed", frame) for frame in range(905, 1_000)], visual=True)

        policy = apply_coverage_policy([case], coverage, pair_index, _scoped_match())

        self.assertEqual(policy["residual_by_team"]["A"]["required_named_gain"], 0)
        self.assertEqual(policy["coverage_blockers"], 0)
        self.assertEqual(policy["next_cases"], [])

    def test_explicit_roster_decision_is_not_requeued_for_safety_debt(self) -> None:
        rows = [
            _observation("demoted", frame, "A", "conflicted", None)
            for frame in range(20)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        already_reviewed = _unit(
            "roman",
            [("demoted", frame) for frame in range(20)],
            visual=True,
        )
        already_reviewed["current_decision"] = {
            "action": "assign_roster_player",
            "player_id": "p1",
        }

        policy = apply_coverage_policy(
            [already_reviewed],
            coverage,
            pair_index,
            _scoped_match(),
        )

        self.assertEqual(policy["next_cases"], [])
        self.assertEqual(
            policy["residual_by_team"]["A"]["available_actionable_named_gain"],
            0,
        )
    def test_team_stats_only_moves_clean_named_debt_to_optional_audit(self) -> None:
        rows = [
            _observation("a", frame, "A", "confirmed" if frame < 95 else "unresolved", "p1" if frame < 95 else None)
            for frame in range(100)
        ] + [
            _observation("b", frame, "B", "unresolved", None)
            for frame in range(200)
        ]
        match = _scoped_match()
        coverage, pair_index = summarize_effective_observations(rows, match)
        b_unit = _unit("b-subject", [("b", frame) for frame in range(200)], visual=True)
        b_unit.update({"source_team_label": "B", "effective_team_label": "B"})

        policy = apply_coverage_policy([b_unit], coverage, pair_index, match)

        self.assertEqual(policy["coverage_blockers"], 0)
        self.assertEqual(policy["next_cases"], [])
        self.assertEqual(len(policy["optional_audit_cases"]), 1)
        self.assertTrue(policy["readiness"]["allows_finalize"])
        self.assertEqual(policy["workload"]["remaining_cases"], 0)
        self.assertEqual(
            coverage["per_team"]["B"]["named_coverage_status"],
            "not_required_by_scope",
        )

    def test_team_stats_only_does_not_hide_semantic_conflict(self) -> None:
        rows = [_observation("b", frame, "B", "unresolved", None) for frame in range(100)]
        match = _scoped_match()
        coverage, pair_index = summarize_effective_observations(rows, match)
        conflict = _unit("b-conflict", [("b", frame) for frame in range(100)], visual=True)
        conflict.update({
            "source_team_label": "A",
            "effective_team_label": "B",
            "current_resolution_status": "pending_high_priority",
            "priority": "high",
            "reason_codes": ["cross_team_conflict"],
        })

        policy = apply_coverage_policy([conflict], coverage, pair_index, match)

        self.assertEqual(policy["semantic_blockers"], 1)
        self.assertEqual(policy["next_cases"][0]["candidate_subject_id"], "b-conflict")
        self.assertFalse(policy["readiness"]["allows_finalize"])

    def test_team_stats_only_explains_non_actionable_team_uncertainty(self) -> None:
        rows = [_observation("u", frame, "B", "unresolved", None) for frame in range(100)]
        match = _scoped_match()
        coverage, pair_index = summarize_effective_observations(rows, match)
        uncertain = _unit("unknown-without-crops", [("u", frame) for frame in range(100)], visual=False)
        uncertain.update({
            "coverage_team_label": "B",
            "source_team_label": "U",
            "effective_team_label": "U",
            "operator_actionable": False,
            "non_actionable_reason": "missing_visual_evidence",
        })

        policy = apply_coverage_policy([uncertain], coverage, pair_index, match)

        self.assertEqual(policy["next_cases"], [])
        self.assertFalse(policy["readiness"]["allows_finalize"])
        cases = policy["residual_by_team"]["B"][
            "non_actionable_required_team_uncertainty_cases"
        ]
        self.assertEqual(cases[0]["candidate_subject_id"], "unknown-without-crops")
        self.assertIn("team_attribution_uncertain", cases[0]["reason_codes"])

    def test_unknown_case_becomes_sufficient_after_assigning_team_stats_only_team(self) -> None:
        rows = [_observation("u", frame, "U", "unresolved", None) for frame in range(100)]
        match = _scoped_match()
        coverage, pair_index = summarize_effective_observations(rows, match)
        unknown = _unit("unknown", [("u", frame) for frame in range(100)], visual=True)
        unknown.update({"source_team_label": "U", "effective_team_label": "U"})

        before = apply_coverage_policy([unknown], coverage, pair_index, match)
        reviewed = {
            **unknown,
            "effective_team_label": "B",
            "current_decision": {"action": "assign_team", "team_label": "B"},
            "current_resolution_status": "reviewed_by_operator",
        }
        after = apply_coverage_policy([reviewed], coverage, pair_index, match)

        self.assertEqual(len(before["next_cases"]), 1)
        self.assertEqual(after["next_cases"], [])
        self.assertEqual(after["optional_audit_cases"], [])

    def test_optional_audit_is_filtered_then_paginated_without_affecting_workload(self) -> None:
        optional = [_queue_unit(f"b-{index:03d}", "B", priority="optional") for index in range(75)]
        progress = {
            "next_cases": [_queue_unit("required-a", "A")],
            "optional_audit_cases": optional,
            "workload": {"remaining_cases": 1, "level": "normal"},
        }

        page = paginate_progress(
            progress,
            queue="optional_audit",
            team_label="B",
            offset=20,
            limit=20,
        )

        self.assertEqual(page["queue"], "optional_audit")
        self.assertEqual(len(page["next_cases"]), 20)
        self.assertEqual(page["pagination"]["total_remaining"], 75)
        self.assertEqual(page["next_cases"][0]["candidate_subject_id"], "b-020")
        self.assertEqual(page["workload"]["remaining_cases"], 1)

    def test_scope_switch_reclassifies_named_debt_without_losing_the_review_unit(self) -> None:
        rows = [
            _observation("b", frame, "B", "unresolved", None)
            for frame in range(200)
        ]
        review_unit = _unit(
            "b-subject",
            [("b", frame) for frame in range(200)],
            visual=True,
        )
        review_unit.update({"source_team_label": "B", "effective_team_label": "B"})
        team_only = _scoped_match()
        both_complete = _scoped_match()
        both_complete["identity_review_scope"]["teams"]["B"] = "complete_roster"

        optional_coverage, optional_pairs = summarize_effective_observations(
            rows,
            team_only,
        )
        required_coverage, required_pairs = summarize_effective_observations(
            rows,
            both_complete,
        )
        optional = apply_coverage_policy(
            [review_unit],
            optional_coverage,
            optional_pairs,
            team_only,
        )
        required = apply_coverage_policy(
            [review_unit],
            required_coverage,
            required_pairs,
            both_complete,
        )

        self.assertEqual(optional["next_cases"], [])
        self.assertEqual(
            optional["optional_audit_cases"][0]["candidate_subject_id"],
            "b-subject",
        )
        self.assertEqual(
            required["next_cases"][0]["candidate_subject_id"],
            "b-subject",
        )
        self.assertEqual(required["optional_audit_cases"], [])

    def test_snapshot_file_fingerprint_changes_without_rehashing_identity_sources(self) -> None:
        with TemporaryDirectory() as directory:
            match_path = Path(directory)
            snapshot_path = match_path / "reviewed_identity_snapshot.json"
            self.assertIsNone(reviewed_snapshot_file_fingerprint(match_path))
            snapshot_path.write_text('{"semantic_digest":"one"}', encoding="utf-8")
            first = reviewed_snapshot_file_fingerprint(match_path)
            snapshot_path.write_text('{"semantic_digest":"two","x":1}', encoding="utf-8")
            second = reviewed_snapshot_file_fingerprint(match_path)
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertNotEqual(first, second)

    def test_team_coverage_is_distinct_from_named_player_coverage(self) -> None:
        rows = [
            _observation("a", frame, "A", "confirmed" if frame < 2 else "unresolved", "p1" if frame < 2 else None)
            for frame in range(10)
        ] + [
            _observation("u", frame, "U", "unresolved", None)
            for frame in range(10)
        ]

        coverage, _ = summarize_effective_observations(rows, _match())

        self.assertEqual(coverage["reliable_observations"], 20)
        self.assertEqual(coverage["confirmed_named_observations"], 2)
        self.assertEqual(coverage["named_observation_coverage"], 0.1)
        self.assertEqual(coverage["team_known_observations"], 10)
        self.assertEqual(coverage["team_known_observation_coverage"], 0.5)
        self.assertEqual(
            coverage["per_team"]["A"]["team_known_observation_coverage"], 1.0
        )
        self.assertEqual(
            coverage["per_team"]["U"]["team_known_observation_coverage"], 0.0
        )

    def test_large_queue_is_ranked_by_observation_gain_and_never_capped(self) -> None:
        rows = []
        units = []
        for index in range(180):
            tracklet_id = f"t-{index:03d}"
            pairs = [(tracklet_id, frame) for frame in range(10)]
            rows.extend(
                _observation(tracklet_id, frame, "A", "unresolved", None)
                for _, frame in pairs
            )
            units.append(_unit(f"subject-{index:03d}", pairs, visual=True))
        coverage, pair_index = summarize_effective_observations(rows, _match())

        policy = apply_coverage_policy(units, coverage, pair_index, _match())

        self.assertEqual(policy["semantic_blockers"], 0)
        self.assertEqual(policy["coverage_blockers"], 162)
        self.assertEqual(len(policy["next_cases"]), 162)
        self.assertEqual(
            policy["residual_by_team"]["A"]["low_impact_reviewable_units"],
            18,
        )
        self.assertEqual(policy["workload"]["level"], "elevated")
        self.assertFalse(policy["workload"]["queue_truncated"])
        self.assertFalse(policy["readiness"]["allows_finalize"])
        self.assertLessEqual(
            policy["residual_by_team"]["A"]["residual_unreviewed_ratio"],
            0.1,
        )

        page = paginate_progress(
            {
                "next_cases": policy["next_cases"],
                "review_units": [{"detected_pairs": [["large", 1]]}],
                "deferred_correction_context": {"subjects": ["internal"]},
            },
            limit=20,
        )
        self.assertEqual(len(page["next_cases"]), 20)
        self.assertEqual(page["pagination"]["total_remaining"], 162)
        self.assertTrue(page["pagination"]["has_more"])
        self.assertNotIn("review_units", page)
        self.assertNotIn("deferred_correction_context", page)

    def test_unfiltered_pagination_remains_backward_compatible_and_reports_counts(self) -> None:
        cases = [
            _queue_unit("a-high", "A", priority="high"),
            _queue_unit("u-coverage", "U"),
            _queue_unit("b-coverage", "B"),
        ]
        progress = {
            "next_cases": cases,
            "summary": {"important_decisions_remaining": 3},
            "coverage_readiness": {"status": "incomplete"},
            "workload": {"remaining_cases": 3, "level": "normal"},
        }

        page = paginate_progress(progress, offset=0, limit=20)

        self.assertEqual(
            [row["candidate_subject_id"] for row in page["next_cases"]],
            ["a-high", "u-coverage", "b-coverage"],
        )
        self.assertIsNone(page["filters"]["active_team_label"])
        self.assertEqual(
            page["filters"]["counts"],
            {"all": 3, "A": 1, "B": 1, "U": 1},
        )
        self.assertEqual(page["pagination"]["total_remaining"], 3)
        self.assertEqual(page["pagination"]["global_total_remaining"], 3)
        self.assertEqual(page["summary"], progress["summary"])
        self.assertEqual(page["coverage_readiness"], progress["coverage_readiness"])
        self.assertEqual(page["workload"], progress["workload"])

    def test_team_filters_select_a_or_b_and_leave_unknown_only_in_all(self) -> None:
        cases = [
            _queue_unit("a", "A"),
            _queue_unit("b", "B"),
            _queue_unit("unknown", "U"),
        ]

        page_a = paginate_progress({"next_cases": cases}, team_label="A")
        page_b = paginate_progress({"next_cases": cases}, team_label="B")

        self.assertEqual(
            [row["candidate_subject_id"] for row in page_a["next_cases"]],
            ["a"],
        )
        self.assertEqual(
            [row["candidate_subject_id"] for row in page_b["next_cases"]],
            ["b"],
        )
        self.assertEqual(page_a["pagination"]["total_remaining"], 1)
        self.assertEqual(page_b["pagination"]["total_remaining"], 1)
        self.assertEqual(page_a["pagination"]["global_total_remaining"], 3)
        self.assertEqual(page_a["filters"]["counts"]["U"], 1)
        self.assertEqual(
            sum(page_a["filters"]["counts"][key] for key in ("A", "B", "U")),
            page_a["filters"]["counts"]["all"],
        )

    def test_filter_team_precedence_is_canonical_and_backend_owned(self) -> None:
        self.assertEqual(
            review_case_team_label({
                "coverage_team_label": "B",
                "effective_team_label": "A",
                "source_team_label": "A",
            }),
            "B",
        )
        self.assertEqual(
            review_case_team_label({
                "coverage_team_label": "U",
                "effective_team_label": "A",
                "source_team_label": "B",
            }),
            "A",
        )
        self.assertEqual(
            review_case_team_label({
                "effective_team_label": "U",
                "source_team_label": "B",
            }),
            "B",
        )
        self.assertEqual(review_case_team_label({}), "U")

    def test_filtering_happens_before_pagination_for_a_large_interleaved_queue(self) -> None:
        cases = []
        for index in range(531):
            team = "A" if index < 45 else "B" if index < 449 else "U"
            cases.append(_queue_unit(f"{team}-{index:03d}", team))
        # Put B rows into the first global page. A page 2 must still be A rows
        # 21-40, not the A subset of global rows 21-40.
        cases = [cases[45], *cases[:45], *cases[46:]]

        page = paginate_progress(
            {"next_cases": cases},
            team_label="A",
            offset=20,
            limit=20,
        )

        self.assertEqual(len(page["next_cases"]), 20)
        self.assertTrue(all(row["filter_team_label"] == "A" for row in page["next_cases"]))
        self.assertEqual(page["next_cases"][0]["candidate_subject_id"], "A-020")
        self.assertEqual(page["next_cases"][-1]["candidate_subject_id"], "A-039")
        self.assertEqual(page["pagination"]["total_remaining"], 45)
        self.assertEqual(page["pagination"]["global_total_remaining"], 531)
        self.assertTrue(page["pagination"]["has_more"])
        self.assertEqual(page["filters"]["counts"]["all"], 531)

    def test_filter_preserves_semantic_then_coverage_order_within_each_team(self) -> None:
        cases = [
            _queue_unit("a-semantic", "A", priority="high"),
            _queue_unit("b-semantic", "B", priority="high"),
            _queue_unit("a-large", "A"),
            _queue_unit("b-large", "B"),
            _queue_unit("a-small", "A"),
            _queue_unit("b-small", "B"),
        ]

        page_a = paginate_progress({"next_cases": cases}, team_label="A")
        page_b = paginate_progress({"next_cases": cases}, team_label="B")

        self.assertEqual(
            [row["candidate_subject_id"] for row in page_a["next_cases"]],
            ["a-semantic", "a-large", "a-small"],
        )
        self.assertEqual(
            [row["candidate_subject_id"] for row in page_b["next_cases"]],
            ["b-semantic", "b-large", "b-small"],
        )

    def test_invalid_filter_is_rejected_instead_of_returning_an_empty_page(self) -> None:
        with self.assertRaisesRegex(ValueError, "team_label must be A or B"):
            paginate_progress({"next_cases": []}, team_label="Corgi")

    def test_empty_team_filter_does_not_mutate_global_completion_state(self) -> None:
        progress = {
            "next_cases": [_queue_unit("b-only", "B")],
            "summary": {"important_decisions_remaining": 1},
            "coverage_readiness": {"status": "incomplete", "allows_finalize": False},
            "workload": {"remaining_cases": 1, "level": "normal"},
        }

        page = paginate_progress(progress, team_label="A")

        self.assertEqual(page["next_cases"], [])
        self.assertEqual(page["pagination"]["total_remaining"], 0)
        self.assertEqual(page["pagination"]["global_total_remaining"], 1)
        self.assertEqual(page["summary"]["important_decisions_remaining"], 1)
        self.assertFalse(page["coverage_readiness"]["allows_finalize"])
        self.assertEqual(page["workload"]["remaining_cases"], 1)

    def test_short_clean_match_with_tiny_residual_needs_no_extra_case(self) -> None:
        rows = [
            _observation(
                "clean",
                frame,
                "A",
                "confirmed" if frame < 91 else "unresolved",
                "p1" if frame < 91 else None,
            )
            for frame in range(100)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _match())
        residual = _unit(
            "tiny-residual",
            [("clean", frame) for frame in range(91, 100)],
            visual=True,
        )

        policy = apply_coverage_policy([residual], coverage, pair_index, _match())

        self.assertEqual(coverage["named_observation_coverage"], 0.91)
        self.assertEqual(policy["coverage_blockers"], 0)
        self.assertTrue(policy["readiness"]["allows_finalize"])

    def test_named_decision_improves_coverage_by_exact_owned_observations(self) -> None:
        before_rows = [
            _observation("subject", frame, "A", "unresolved", None)
            for frame in range(40)
        ]
        after_rows = [
            _observation("subject", frame, "A", "confirmed", "p1")
            for frame in range(40)
        ]

        before, _ = summarize_effective_observations(before_rows, _match())
        after, _ = summarize_effective_observations(after_rows, _match())

        self.assertEqual(before["confirmed_named_observations"], 0)
        self.assertEqual(after["confirmed_named_observations"], 40)
        self.assertEqual(after["named_observation_coverage"], 1.0)
        self.assertEqual(
            before["per_team"]["A"]["team_known_observations"],
            after["per_team"]["A"]["team_known_observations"],
        )

    def test_false_detection_and_referee_do_not_create_identity_debt(self) -> None:
        rows = [
            _observation("player", frame, "A", "confirmed", "p1")
            for frame in range(10)
        ] + [
            _observation("referee", frame, "U", "referee", None)
            for frame in range(7)
        ] + [
            _observation("false", frame, "U", "false_detection", None)
            for frame in range(5)
        ]

        coverage, _ = summarize_effective_observations(rows, _match())

        self.assertEqual(coverage["reliable_observations"], 10)
        self.assertEqual(coverage["confirmed_named_observations"], 10)
        self.assertEqual(coverage["named_observation_coverage"], 1.0)
        self.assertEqual(coverage["ignored_observations"], 12)

    def test_huge_anonymous_subject_ranks_before_smaller_coverage_debt(self) -> None:
        rows = [
            _observation("huge", frame, "A", "unresolved", None)
            for frame in range(100)
        ] + [
            _observation("small", frame, "A", "unresolved", None)
            for frame in range(20)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _match())
        huge = _unit("huge", [("huge", frame) for frame in range(100)], visual=True)
        small = _unit("small", [("small", frame) for frame in range(20)], visual=True)

        policy = apply_coverage_policy([small, huge], coverage, pair_index, _match())

        self.assertEqual(policy["next_cases"][0]["candidate_subject_id"], "huge")
        self.assertEqual(policy["next_cases"][0]["potential_named_observation_gain"], 100)

    def test_semantic_conflicts_are_ranked_before_coverage_debt(self) -> None:
        rows = [
            _observation("semantic", frame, "A", "conflicted", None)
            for frame in range(5)
        ] + [
            _observation("large", frame, "A", "unresolved", None)
            for frame in range(100)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _match())
        semantic = _unit(
            "semantic-subject",
            [("semantic", frame) for frame in range(5)],
            visual=True,
        )
        semantic["current_resolution_status"] = "pending_high_priority"
        semantic["priority"] = "high"
        semantic["reason_codes"] = ["semantic_identity_conflict"]
        large = _unit(
            "large-subject",
            [("large", frame) for frame in range(100)],
            visual=True,
        )

        policy = apply_coverage_policy(
            [large, semantic], coverage, pair_index, _match()
        )

        self.assertEqual(policy["next_cases"][0]["candidate_subject_id"], "semantic-subject")
        self.assertEqual(policy["next_cases"][0]["priority"], "high")

    def test_team_only_decision_acknowledges_partial_roster_but_not_complete_roster(self) -> None:
        rows = [
            _observation("a", frame, "A", "unresolved", None)
            for frame in range(100)
        ]
        unit = _unit("subject", [("a", frame) for frame in range(100)], visual=True)
        unit["current_decision"] = {"action": "assign_team", "team_label": "A"}
        coverage, pair_index = summarize_effective_observations(rows, _match())

        partial = apply_coverage_policy([unit], coverage, pair_index, _match())
        complete = apply_coverage_policy(
            [unit],
            coverage,
            pair_index,
            _match(scope_a="complete_roster"),
        )

        self.assertEqual(partial["coverage_blockers"], 0)
        self.assertTrue(partial["readiness"]["allows_finalize"])
        self.assertEqual(complete["coverage_blockers"], 1)
        self.assertFalse(complete["readiness"]["allows_finalize"])

    def test_missing_visual_evidence_is_explicitly_not_assessable(self) -> None:
        rows = [
            _observation("a", frame, "A", "unresolved", None)
            for frame in range(100)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _match())
        policy = apply_coverage_policy(
            [_unit("subject", [("a", frame) for frame in range(100)], visual=False)],
            coverage,
            pair_index,
            _match(),
        )

        self.assertEqual(policy["coverage_blockers"], 0)
        self.assertFalse(policy["readiness"]["allows_finalize"])
        self.assertEqual(
            policy["readiness"]["blockers"][0]["code"],
            "coverage_evidence_unavailable",
        )
        self.assertEqual(
            policy["residual_by_team"]["A"]["unreviewable_reason_counts"],
            {"no_visual_evidence": 1},
        )

    def test_structurally_ambiguous_visual_unit_is_not_promoted_and_debt_is_retained(self) -> None:
        rows = [
            _observation("ambiguous", frame, "A", "unresolved", None)
            for frame in range(100)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _match())
        unit = _unit(
            "ambiguous-subject",
            [("ambiguous", frame) for frame in range(100)],
            visual=True,
        )
        unit.update({
            "current_resolution_status": "structurally_blocked",
            "operator_actionable": False,
            "non_actionable_reason": "ambiguous_candidate_subject_membership",
        })

        policy = apply_coverage_policy([unit], coverage, pair_index, _match())

        self.assertEqual(policy["next_cases"], [])
        self.assertEqual(policy["coverage_blockers"], 0)
        self.assertEqual(policy["residual_by_team"]["A"]["unreviewable_units"], 1)
        self.assertEqual(
            policy["residual_by_team"]["A"]["low_impact_reviewable_units"],
            0,
        )
        self.assertEqual(
            policy["residual_by_team"]["A"]["unreviewable_observations"],
            100,
        )
        self.assertEqual(
            policy["residual_by_team"]["A"]["unreviewable_reason_counts"],
            {"ambiguous_candidate_subject_membership": 1},
        )
        self.assertFalse(policy["readiness"]["allows_finalize"])
        blocker = next(
            row
            for row in policy["readiness"]["blockers"]
            if row["code"] == "coverage_evidence_unavailable"
        )
        self.assertEqual(
            blocker["observations_by_reason"],
            {"ambiguous_candidate_subject_membership": 100},
        )

    def test_reviewable_whole_subject_and_canonical_segment_remain_eligible(self) -> None:
        rows = [
            _observation("whole", frame, "A", "unresolved", None)
            for frame in range(100)
        ] + [
            _observation("segment", frame, "A", "unresolved", None)
            for frame in range(100, 200)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _match())
        whole = _unit("whole", [("whole", frame) for frame in range(100)], visual=True)
        whole.update({"operator_actionable": True, "correction_scope": "whole_subject"})
        segment = _unit(
            "segment",
            [("segment", frame) for frame in range(100, 200)],
            visual=True,
        )
        segment.update({
            "operator_actionable": True,
            "correction_scope": "canonical_segment",
            "scope_kind": "canonical_segment",
            "review_target_id": "segment-target",
        })

        policy = apply_coverage_policy([whole, segment], coverage, pair_index, _match())

        self.assertEqual(policy["coverage_blockers"], 2)
        self.assertEqual(
            {row["candidate_subject_id"] for row in policy["next_cases"]},
            {"whole", "segment"},
        )
        self.assertTrue(all(row["priority"] == "coverage" for row in policy["next_cases"]))


def _match(scope_a: str | None = None) -> dict:
    team_a = {"team_label": "A", "players": [{"id": "p1", "name": "One"}]}
    if scope_a:
        team_a["identity_coverage_scope"] = scope_a
    return {
        "id": "match",
        "teams": [team_a, {"team_label": "B", "players": []}],
    }


def _scoped_match() -> dict:
    match = _match()
    match["identity_review_scope"] = {
        "schema_version": "1.0.0",
        "teams": {"A": "complete_roster", "B": "team_stats_only"},
    }
    return match


def _observation(
    tracklet_id: str,
    frame: int,
    team: str,
    status: str,
    player_id: str | None,
) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "frame": frame,
        "team_label": team,
        "identity_status": status,
        "canonical_player_id": player_id,
        "play_area_status": "inside_play",
        "source": "detected",
    }


def _unit(subject_id: str, pairs: list[tuple[str, int]], *, visual: bool) -> dict:
    return {
        "candidate_subject_id": subject_id,
        "tracklet_ids": sorted({tracklet_id for tracklet_id, _ in pairs}),
        "tracklet_count": len({tracklet_id for tracklet_id, _ in pairs}),
        "effective_team_label": "A",
        "detected_observation_count": len(pairs),
        "detected_time_sec": len(pairs) / 25,
        "current_decision": None,
        "current_resolution_status": "pending_optional",
        "priority": "optional",
        "reason_codes": ["long_unresolved_safe_anonymous"],
        "has_operator_visual_evidence": visual,
        "detected_pairs": pairs,
    }


def _queue_unit(
    subject_id: str,
    team_label: str,
    *,
    priority: str = "coverage",
) -> dict:
    return {
        "candidate_subject_id": subject_id,
        "tracklet_ids": [f"tracklet-{subject_id}"],
        "tracklet_count": 1,
        "source_team_label": team_label,
        "effective_team_label": team_label,
        "coverage_team_label": team_label,
        "detected_observation_count": 10,
        "detected_time_sec": 0.4,
        "current_resolution_status": (
            "pending_high_priority"
            if priority == "high"
            else "pending_coverage_review"
        ),
        "priority": priority,
        "reason_codes": [],
    }


if __name__ == "__main__":
    unittest.main()
