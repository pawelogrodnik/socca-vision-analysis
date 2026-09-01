from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.identity_reviewed_coverage import (
    _coverage_impact,
    apply_coverage_policy,
    build_coverage_debt,
    paginate_progress,
    review_case_team_label,
    summarize_effective_observations,
    target_named_observations,
)
from app.services.identity_ownership_compact import CompactPairIndexView
from app.services.identity_reviewed_material_continuity import (
    coalesce_material_continuity_units,
)
from app.services.identity_reviewed_scope_eligibility import team_attribution_state
from app.services.identity_reviewed_progress import reviewed_snapshot_file_fingerprint


class ReviewedIdentityCoverageTests(unittest.TestCase):
    def test_team_stats_only_filters_certain_identity_semantic_and_continuity_but_keeps_attribution_safety(self) -> None:
        pair_index = {
            ("a", frame): {"team_label": "A", "identity_status": "unresolved", "canonical_player_id": None}
            for frame in range(3)
        } | {
            ("b", frame): {"team_label": "B", "identity_status": "unresolved", "canonical_player_id": None}
            for frame in range(4)
        }
        a_semantic = _unit("a-semantic", [("a", 0)], visual=True)
        a_semantic.update({"current_resolution_status": "pending_high_priority", "priority": "high"})
        a_coverage = _unit("a-coverage", [("a", 1)], visual=True)
        a_material = _material_case("a-material", range(2, 3))
        b_semantic = _unit("b-semantic", [("b", 0)], visual=True)
        b_semantic.update({"effective_team_label": "B", "source_team_label": "B", "current_resolution_status": "pending_high_priority", "priority": "high", "reason_codes": ["identity_conflict"]})
        b_material = _material_case("b-material", range(1, 2))
        b_material.update({"effective_team_label": "B", "source_team_label": "B", "detected_pairs": [("b", 1)]})
        b_coverage = _unit("b-coverage", [("b", 2)], visual=True)
        b_coverage.update({"effective_team_label": "B", "source_team_label": "B"})
        b_uncertain = _unit("b-uncertain", [("b", 3)], visual=True)
        b_uncertain.update({"effective_team_label": "B", "source_team_label": "B", "detected_team_labels": ["A", "B"], "current_resolution_status": "pending_high_priority", "priority": "high", "reason_codes": ["team_attribution_uncertain"]})

        policy = apply_coverage_policy(
            [a_semantic, a_coverage, a_material, b_semantic, b_material, b_coverage, b_uncertain],
            {"per_team": {
                "A": {"reliable_observations": 3, "confirmed_named_observations": 0},
                "B": {"reliable_observations": 4, "confirmed_named_observations": 0},
            }},
            pair_index,
            _scoped_match(),
        )

        self.assertEqual(
            {unit["candidate_subject_id"] for unit in policy["next_cases"]},
            {"a-semantic", "a-coverage", "a-material"},
        )
        self.assertEqual(policy["material_continuity_blockers"], 1)
        self.assertEqual(policy["semantic_blockers"], 1)

    def test_certain_team_b_anonymous_keeps_team_stats_without_named_player_debt(self) -> None:
        rows = [
            _observation("b-anonymous", frame, "B", "unresolved", None)
            for frame in range(5)
        ]
        match = _scoped_match()
        coverage, pair_index = summarize_effective_observations(rows, match)
        unit = _unit("b-anonymous", [("b-anonymous", frame) for frame in range(5)], visual=True)
        unit.update({
            "source_team_label": "B",
            "effective_team_label": "B",
            "detected_team_labels": ["B"],
            "current_resolution_status": "pending_high_priority",
            "priority": "high",
            "reason_codes": ["identity_conflict"],
            "canonical_player_id": None,
        })

        policy = apply_coverage_policy([unit], coverage, pair_index, match)

        self.assertEqual(policy["next_cases"], [])
        self.assertEqual(coverage["per_team"]["B"]["team_known_observations"], 5)
        self.assertEqual(coverage["per_team"]["B"]["confirmed_named_observations"], 0)
        self.assertEqual(coverage["per_team"]["B"]["named_coverage_status"], "not_required_by_scope")

    def test_coverage_team_authority_ignores_a_raw_u_majority_in_pair_and_compact_paths(self) -> None:
        coverage = {
            "reliable_observations": 208,
            "per_team": {
                "A": {"reliable_observations": 100, "confirmed_named_observations": 0},
                "B": {"reliable_observations": 100, "confirmed_named_observations": 0},
                "U": {"reliable_observations": 8, "confirmed_named_observations": 0},
            },
        }

        def impact_variants(
            subject_id: str,
            labels: list[str],
            **team_evidence: object,
        ) -> tuple[dict, dict, dict, dict]:
            pair_index = {
                (subject_id, frame): {
                    "team_label": team,
                    "identity_status": "unresolved",
                    "canonical_player_id": None,
                }
                for frame, team in enumerate(labels)
            }
            unit = _unit(
                subject_id,
                [(subject_id, frame) for frame in range(len(labels))],
                visual=True,
            )
            unit.update({
                **team_evidence,
                "current_resolution_status": "pending_high_priority",
                "priority": "high",
            })
            compact = CompactPairIndexView({
                subject_id: [
                    [frame, frame, pair_index[(subject_id, frame)]]
                    for frame in range(len(labels))
                ],
            })
            compact_unit = {
                **unit,
                "detected_pair_runs": {subject_id: [[0, len(labels) - 1]]},
            }
            return (
                unit,
                pair_index,
                _coverage_impact(unit, pair_index, coverage),
                _coverage_impact(compact_unit, compact, coverage),
            )

        a_unit, a_index, a_pair, a_compact = impact_variants(
            "a-u-majority",
            ["A", "A", *(["U"] * 8)],
            source_team_label="A",
            effective_team_label="A",
            coverage_team_label="A",
            detected_team_labels=["A"],
        )
        self.assertEqual(team_attribution_state(a_unit), "certain_A")
        self.assertEqual(a_pair["coverage_team_label"], "A")
        self.assertEqual(a_pair["potential_named_observation_gain"], 2)
        self.assertEqual(a_pair["operator_impact_pp"], 2.0)
        self.assertEqual(
            {key: a_pair[key] for key in ("coverage_team_label", "potential_named_observation_gain", "operator_impact_pp")},
            {key: a_compact[key] for key in ("coverage_team_label", "potential_named_observation_gain", "operator_impact_pp")},
        )
        a_policy = apply_coverage_policy([a_unit], coverage, a_index, _scoped_match())
        self.assertEqual(a_policy["next_cases"][0]["coverage_team_label"], "A")
        self.assertEqual(a_policy["next_cases"][0]["potential_named_observation_gain"], 2)

        b_unit, b_index, b_pair, b_compact = impact_variants(
            "b-u-majority",
            ["B", "B", *(["U"] * 8)],
            source_team_label="B",
            effective_team_label="B",
            coverage_team_label="B",
            detected_team_labels=["B"],
        )
        self.assertEqual(team_attribution_state(b_unit), "certain_B")
        self.assertEqual(b_pair["coverage_team_label"], "B")
        self.assertEqual(b_pair["potential_named_observation_gain"], 2)
        self.assertEqual(
            {key: b_pair[key] for key in ("coverage_team_label", "potential_named_observation_gain", "operator_impact_pp")},
            {key: b_compact[key] for key in ("coverage_team_label", "potential_named_observation_gain", "operator_impact_pp")},
        )
        self.assertEqual(apply_coverage_policy([b_unit], coverage, b_index, _scoped_match())["next_cases"], [])

        cross_unit, cross_index, cross_pair, cross_compact = impact_variants(
            "a-b-u-conflict",
            ["A", "B", *(["U"] * 8)],
            source_team_label="B",
            effective_team_label="B",
            coverage_team_label="B",
            detected_team_labels=["A", "B"],
        )
        self.assertEqual(team_attribution_state(cross_unit), "cross_team")
        self.assertEqual(cross_pair["coverage_team_label"], "U")
        self.assertEqual(cross_compact["coverage_team_label"], "U")
        cross_policy = apply_coverage_policy([cross_unit], coverage, cross_index, _scoped_match())
        self.assertEqual(cross_policy["next_cases"], [])

        u_unit, u_index, u_pair, u_compact = impact_variants(
            "u-only",
            ["U"] * 4,
            source_team_label="U",
            effective_team_label="U",
            coverage_team_label="U",
            detected_team_labels=[],
        )
        self.assertEqual(team_attribution_state(u_unit), "unknown")
        self.assertEqual(u_pair["coverage_team_label"], "U")
        self.assertEqual(u_compact["coverage_team_label"], "U")
        u_policy = apply_coverage_policy([u_unit], coverage, u_index, _scoped_match())
        self.assertEqual(u_policy["next_cases"], [])
        self.assertFalse(u_policy["readiness"]["allows_finalize"])

    def test_real_shape_scope_regression_removes_stale_diagnostic_team_b_cases(self) -> None:
        pair_index = {
            ("a", frame): {"team_label": "A", "identity_status": "unresolved", "canonical_player_id": None}
            for frame in range(6)
        } | {
            ("b", frame): {"team_label": "B", "identity_status": "unresolved", "canonical_player_id": None}
            for frame in range(43)
        }
        corgi = []
        for frame in range(6):
            unit = _unit(f"a-{frame}", [("a", frame)], visual=True)
            unit.update({"current_resolution_status": "pending_high_priority", "priority": "high"})
            corgi.append(unit)
        safety = []
        for frame in range(2):
            unit = _unit(f"b-safety-{frame}", [("b", frame)], visual=True)
            unit.update({"effective_team_label": "B", "source_team_label": "B", "detected_team_labels": ["A", "B"], "current_resolution_status": "pending_high_priority", "priority": "high", "reason_codes": ["team_attribution_uncertain"]})
            safety.append(unit)
        opponent_identity = []
        for frame in range(2, 23):
            unit = _unit(f"b-identity-{frame}", [("b", frame)], visual=True)
            unit.update({"effective_team_label": "B", "source_team_label": "B", "current_resolution_status": "pending_high_priority", "priority": "high"})
            opponent_identity.append(unit)
        stale_diagnostic_b = []
        for frame in range(2, 21):
            unit = _unit(f"b-stale-diagnostic-{frame}", [("b", frame)], visual=True)
            unit.update({
                "source_team_label": "B",
                "effective_team_label": "B",
                "detected_team_labels": ["B"],
                "current_resolution_status": "pending_high_priority",
                "priority": "high",
                "reason_codes": ["team_mismatch", "identity_conflict"],
            })
            stale_diagnostic_b.append(unit)
        opponent_continuity = []
        for frame in range(23, 43):
            unit = _material_case(f"b-continuity-{frame}", range(frame, frame + 1))
            unit.update({"effective_team_label": "B", "source_team_label": "B", "detected_pairs": [("b", frame)]})
            opponent_continuity.append(unit)
        coverage = {"per_team": {
            "A": {"reliable_observations": 6, "confirmed_named_observations": 0},
            "B": {"reliable_observations": 43, "confirmed_named_observations": 0},
        }}
        policy = apply_coverage_policy(
            [*corgi, *safety, *opponent_identity, *stale_diagnostic_b, *opponent_continuity],
            coverage,
            pair_index,
            _scoped_match(),
        )
        debt = build_coverage_debt(
            [*corgi, *safety, *opponent_identity, *stale_diagnostic_b, *opponent_continuity],
            coverage,
            pair_index,
            _scoped_match(),
            policy,
            {"cases": []},
        )

        self.assertEqual(len(policy["next_cases"]), 6)
        self.assertEqual(debt["actual_required_queue"]["total_cases"], 6)
        self.assertEqual(debt["actual_required_queue"]["per_team"]["A"]["total_cases"], 6)
        self.assertEqual(debt["actual_required_queue"]["per_team"]["B"]["total_cases"], 0)
        self.assertEqual(debt["actual_required_queue"]["per_team"]["B"]["unexpected_by_scope"], 0)

    def test_cross_team_case_is_exposed_through_the_uncertain_team_filter(self) -> None:
        cross_team = _queue_unit("cross-team", "B")
        cross_team.update({
            "source_team_label": "B",
            "effective_team_label": "B",
            "coverage_team_label": "B",
            "detected_team_labels": ["A", "B"],
        })

        page = paginate_progress({"next_cases": [cross_team]}, team_label="U")

        self.assertEqual(page["filters"]["counts"], {"all": 1, "A": 0, "B": 0, "U": 1})
        self.assertEqual(page["next_cases"][0]["filter_team_label"], "U")

    def test_coverage_debt_partitions_unnamed_pairs_with_exact_mixed_ownership(self) -> None:
        pair_index = {
            ("t", frame): {
                "team_label": "A",
                "identity_status": "confirmed" if frame < 8 else "unresolved",
                "canonical_player_id": "p1" if frame < 8 else None,
            }
            for frame in range(10)
        }
        coverage = {
            "per_team": {
                "A": {
                    "reliable_observations": 10,
                    "confirmed_named_observations": 8,
                },
            },
        }
        saved = _unit("saved", [("t", 8)], visual=True)
        saved["current_decision"] = {"action": "assign_roster_player", "player_id": "p-a"}
        required = _unit("required", [("t", 9)], visual=True)
        required["_potential_named_observation_pairs"] = {("t", 9)}
        match = _scoped_match()
        match["teams"][0]["players"] = [{"id": "p-a"}]
        debt = build_coverage_debt(
            [saved, required],
            coverage,
            pair_index,
            match,
            {
                "next_cases": [required],
                "optional_audit_cases": [required],
                "optional_audit": {"unavailable_reason_counts": {}},
            },
            {
                "cases": [{
                    "case_id": "mixed-overlap",
                    "resolution_status": "unresolved",
                    "observation_count": 1,
                    "source": {"owned_observations": [{"tracklet_id": "t", "frame": 9}]},
                }],
            },
        )

        row = debt["per_team"]["A"]
        self.assertEqual(row["unnamed_observations"], 2)
        self.assertEqual(row["buckets"]["committed_pending"]["unique_observations"], 1)
        self.assertEqual(row["buckets"]["required"]["unique_observations"], 1)
        self.assertEqual(row["buckets"]["mixed"]["unique_observations"], 0)
        self.assertEqual(row["accounted_unnamed_observations"], 2)
        self.assertEqual(row["unaccounted_unnamed_observations"], 0)

    def test_coverage_debt_moves_only_exact_required_source_to_mixed(self) -> None:
        pair_index = {
            ("t", frame): {"team_label": "A", "identity_status": "unresolved", "canonical_player_id": None}
            for frame in range(6)
        }
        coverage = {"per_team": {"A": {"reliable_observations": 6, "confirmed_named_observations": 0}}}
        exact = _unit("subject", [("t", 2), ("t", 3)], visual=True)
        exact["_potential_named_observation_pairs"] = {("t", 2), ("t", 3)}
        sibling = _unit("subject", [("t", 0), ("t", 1), ("t", 4), ("t", 5)], visual=True)
        before = build_coverage_debt([exact, sibling], coverage, pair_index, _scoped_match(), {
            "next_cases": [exact], "optional_audit_cases": [], "optional_audit": {},
        }, {"cases": []})
        after = build_coverage_debt([sibling], coverage, pair_index, _scoped_match(), {
            "next_cases": [], "optional_audit_cases": [], "optional_audit": {},
        }, {"cases": [{
            "case_id": "exact", "resolution_status": "unresolved", "observation_count": 2,
            "source": {"owned_observations": [{"tracklet_id": "t", "frame": 2}, {"tracklet_id": "t", "frame": 3}]},
        }]})

        self.assertEqual(before["per_team"]["A"]["buckets"]["required"]["unique_observations"], 2)
        self.assertEqual(after["per_team"]["A"]["buckets"]["required"]["unique_observations"], 0)
        self.assertEqual(after["per_team"]["A"]["buckets"]["mixed"]["unique_observations"], 2)
        self.assertEqual(after["per_team"]["A"]["accounted_unnamed_observations"], 6)

    def test_cross_team_mixed_is_unattributed_in_coverage_debt(self) -> None:
        pair_index = {
            **{
                ("a", frame): {"team_label": "A", "identity_status": "unresolved", "canonical_player_id": None}
                for frame in range(60)
            },
            **{
                ("b", frame): {"team_label": "B", "identity_status": "unresolved", "canonical_player_id": None}
                for frame in range(40)
            },
        }
        debt = build_coverage_debt(
            [],
            {"per_team": {
                "A": {"reliable_observations": 60, "confirmed_named_observations": 0},
                "B": {"reliable_observations": 40, "confirmed_named_observations": 0},
            }},
            pair_index,
            _scoped_match(),
            {"next_cases": [], "optional_audit_cases": [], "optional_audit": {}},
            {"cases": [{
                "case_id": "cross", "mixed_hint": "cross_team", "resolution_status": "unresolved", "observation_count": 100,
                "source": {"owned_observations": [
                    *[{"tracklet_id": "a", "frame": frame} for frame in range(60)],
                    *[{"tracklet_id": "b", "frame": frame} for frame in range(40)],
                ]},
            }]},
        )

        self.assertEqual(debt["per_team"]["A"]["buckets"]["mixed"]["unique_observations"], 0)
        self.assertEqual(debt["per_team"]["B"]["buckets"]["mixed"]["unique_observations"], 0)
        self.assertEqual(debt["ambiguous"]["mixed_case_count"], 1)
        self.assertEqual(debt["ambiguous"]["unique_current_reliable_observations"], 100)
        self.assertEqual(debt["ambiguous"]["currently_labeled"], {"A": 60, "B": 40})
        self.assertEqual(debt["per_team"]["A"]["buckets"]["unavailable"]["unique_observations"], 0)
        self.assertEqual(debt["per_team"]["B"]["not_required_by_scope"]["unique_observations"], 0)
        self.assertEqual(debt["per_team"]["A"]["accounted_unnamed_observations"], 60)
        self.assertEqual(debt["per_team"]["B"]["accounted_unnamed_observations"], 40)

    def test_team_stats_only_unnamed_observations_are_not_unavailable_identity_debt(self) -> None:
        pair_index = {
            ("b", frame): {"team_label": "B", "identity_status": "unresolved", "canonical_player_id": None}
            for frame in range(1_000)
        }
        debt = build_coverage_debt(
            [],
            {"per_team": {"B": {"reliable_observations": 1_000, "confirmed_named_observations": 142}}},
            pair_index,
            _scoped_match(),
            {"next_cases": [], "optional_audit_cases": [], "optional_audit": {}},
            {"cases": []},
        )

        row = debt["per_team"]["B"]
        self.assertIsNone(row["target_named_coverage"])
        self.assertEqual(row["buckets"]["unavailable"]["unique_observations"], 0)
        self.assertEqual(row["not_required_by_scope"]["unique_observations"], 1_000)
        self.assertEqual(row["accounted_unnamed_observations"], 1_000)

    def test_team_stats_only_retains_required_team_attribution_safety_breakdown(self) -> None:
        pair_index = {
            ("b", frame): {"team_label": "B", "identity_status": "unresolved", "canonical_player_id": None}
            for frame in range(5)
        }
        safety = _unit("uncertain", [("b", frame) for frame in range(5)], visual=True)
        safety.update({
            "source_team_label": "B",
            "effective_team_label": "B",
            "detected_team_labels": ["A", "B"],
            "priority": "high",
            "reason_codes": ["team_attribution_uncertain"],
            "_potential_named_observation_pairs": {("b", frame) for frame in range(5)},
        })
        debt = build_coverage_debt(
            [safety],
            {"per_team": {"B": {"reliable_observations": 5, "confirmed_named_observations": 0}}},
            pair_index,
            _scoped_match(),
            {"next_cases": [safety], "optional_audit_cases": [], "optional_audit": {}},
            {"cases": []},
        )

        required = debt["per_team"]["B"]["buckets"]["required"]
        self.assertEqual(required["unique_observations"], 5)
        self.assertEqual(required["breakdown"]["semantic"]["case_count"], 1)
        self.assertEqual(debt["per_team"]["B"]["not_required_by_scope"]["unique_observations"], 0)

    def test_required_debt_breakdown_uses_queue_categories_without_overlap(self) -> None:
        pair_index = {
            ("a", frame): {"team_label": "A", "identity_status": "unresolved", "canonical_player_id": None}
            for frame in range(6)
        }
        semantic = _unit("semantic", [("a", 0), ("a", 1)], visual=True)
        semantic.update({"priority": "high", "_potential_named_observation_pairs": {("a", 0), ("a", 1)}})
        continuity = _unit("continuity", [("a", 2), ("a", 3)], visual=True)
        continuity.update({"priority": "continuity", "_potential_named_observation_pairs": {("a", 2), ("a", 3)}})
        coverage = _unit("coverage", [("a", 4), ("a", 5)], visual=True)
        coverage.update({"priority": "coverage", "_potential_named_observation_pairs": {("a", 4), ("a", 5)}})
        debt = build_coverage_debt(
            [semantic, continuity, coverage],
            {"per_team": {"A": {"reliable_observations": 6, "confirmed_named_observations": 0}}},
            pair_index,
            _scoped_match(),
            {"next_cases": [semantic, continuity, coverage], "optional_audit_cases": [coverage], "optional_audit": {}},
            {"cases": []},
        )

        breakdown = debt["per_team"]["A"]["buckets"]["required"]["breakdown"]
        self.assertEqual({key: row["unique_observations"] for key, row in breakdown.items()}, {
            "semantic": 2, "continuity": 2, "coverage": 2,
        })
        self.assertEqual(debt["per_team"]["A"]["buckets"]["optional_max"]["unique_observations"], 0)

    def test_team_stats_only_actual_required_queue_exposes_scope_mismatch(self) -> None:
        pair_index = {
            ("b", frame): {"team_label": "B", "identity_status": "unresolved", "canonical_player_id": None}
            for frame in range(59)
        }
        def required(subject: str, frame: int, priority: str, *, uncertain: bool = False) -> dict:
            pairs = [("b", frame)]
            unit = _unit(subject, pairs, visual=True)
            unit.update({
                "source_team_label": "B",
                "effective_team_label": "B",
                **({"detected_team_labels": ["A", "B"]} if uncertain else {}),
                "priority": priority,
                "reason_codes": ["team_attribution_uncertain"] if uncertain else ["test"],
                "_potential_named_observation_pairs": set(pairs),
            })
            return unit
        semantic = [required(f"semantic-{frame}", frame, "high", uncertain=True) for frame in range(4)]
        continuity = [required(f"continuity-{frame}", frame, "continuity") for frame in range(4, 41)]
        coverage = [required(f"coverage-{frame}", frame, "coverage") for frame in range(41, 59)]
        required_cases = [*semantic, *continuity, *coverage]
        debt = build_coverage_debt(
            required_cases,
            {"per_team": {"B": {"reliable_observations": 59, "confirmed_named_observations": 0}}},
            pair_index,
            _scoped_match(),
            {"next_cases": required_cases, "optional_audit_cases": [], "optional_audit": {}},
            {"cases": []},
        )

        actual = debt["actual_required_queue"]
        self.assertEqual(actual["total_cases"], 59)
        self.assertEqual(actual["normal_blocking_case_count"], 59)
        self.assertEqual(actual["per_team"]["B"]["breakdown"], {
            "semantic": {"case_count": 4}, "continuity": {"case_count": 37}, "coverage": {"case_count": 18},
        })
        self.assertEqual(actual["per_team"]["B"]["expected_by_scope"], 4)
        self.assertEqual(actual["per_team"]["B"]["unexpected_by_scope"], 55)
        self.assertEqual(debt["per_team"]["B"]["buckets"]["required"]["case_count"], 4)
        self.assertEqual(debt["per_team"]["B"]["buckets"]["required"]["breakdown"]["semantic"]["case_count"], 4)

    def test_actual_required_queue_counts_overlapping_sources_independently(self) -> None:
        pair_index = {
            ("a", frame): {"team_label": "A", "identity_status": "unresolved", "canonical_player_id": None}
            for frame in range(3)
        }
        first = _unit("same-subject", [("a", frame) for frame in range(3)], visual=True)
        second = _unit("same-subject", [("a", frame) for frame in range(3)], visual=True)
        for target, unit in (("first", first), ("second", second)):
            unit.update({"review_target_id": target, "priority": "coverage", "_potential_named_observation_pairs": {("a", frame) for frame in range(3)}})
        debt = build_coverage_debt(
            [first, second],
            {"per_team": {"A": {"reliable_observations": 3, "confirmed_named_observations": 0}}},
            pair_index,
            _scoped_match(),
            {"next_cases": [first, second], "optional_audit_cases": [], "optional_audit": {}},
            {"cases": []},
        )

        self.assertEqual(debt["actual_required_queue"]["total_cases"], 2)
        self.assertEqual(debt["per_team"]["A"]["buckets"]["required"]["case_count"], 2)
        self.assertEqual(debt["per_team"]["A"]["buckets"]["required"]["unique_observations"], 3)

    def test_overlapping_cross_team_mixed_markers_keep_unique_current_labels(self) -> None:
        pair_index = {
            ("a", frame): {"team_label": "A", "identity_status": "unresolved", "canonical_player_id": None}
            for frame in range(2)
        } | {
            ("b", frame): {"team_label": "B", "identity_status": "unresolved", "canonical_player_id": None}
            for frame in range(2)
        }
        owned = [
            *[{"tracklet_id": "a", "frame": frame} for frame in range(2)],
            *[{"tracklet_id": "b", "frame": frame} for frame in range(2)],
        ]
        debt = build_coverage_debt(
            [],
            {"per_team": {
                "A": {"reliable_observations": 2, "confirmed_named_observations": 0},
                "B": {"reliable_observations": 2, "confirmed_named_observations": 0},
            }},
            pair_index,
            _scoped_match(),
            {"next_cases": [], "optional_audit_cases": [], "optional_audit": {}},
            {"cases": [
                {"case_id": "one", "mixed_hint": "cross_team", "resolution_status": "unresolved", "observation_count": 4, "source": {"owned_observations": owned}},
                {"case_id": "two", "mixed_hint": "cross_team", "resolution_status": "unresolved", "observation_count": 4, "source": {"owned_observations": owned}},
            ]},
        )

        self.assertEqual(debt["ambiguous"]["mixed_case_count"], 2)
        self.assertEqual(debt["ambiguous"]["unique_current_reliable_observations"], 4)
        self.assertEqual(debt["ambiguous"]["currently_labeled"], {"A": 2, "B": 2})
        self.assertEqual(debt["ambiguous"]["raw_marker_observations"], 8)
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

    def test_continue_to_max_offers_all_safe_team_a_residuals_after_required_gate(self) -> None:
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(95)
        ] + [
            _observation("short", frame, "A", "unresolved", None)
            for frame in range(95, 100)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        policy = apply_coverage_policy(
            [_unit("short", [("short", frame) for frame in range(95, 100)], visual=True)],
            coverage,
            pair_index,
            _scoped_match(),
        )

        self.assertTrue(policy["readiness"]["allows_finalize"])
        self.assertEqual(policy["next_cases"], [])
        self.assertEqual(len(policy["optional_audit_cases"]), 1)
        optional = policy["optional_audit"]
        self.assertEqual(optional["status"], "available")
        self.assertFalse(optional["blocking"])
        self.assertEqual(optional["current_named_coverage"], 0.95)
        self.assertEqual(optional["safe_max_named_coverage"], 1.0)
        self.assertEqual(optional["remaining_actionable_named_gain"], 5)
        self.assertEqual(policy["optional_audit_cases"][0]["optional_max_marginal_coverage_gain_pp"], 5.0)

    def test_optional_max_split_children_are_recomputed_as_server_owned_named_gain(self) -> None:
        """A completed optional split must leave required readiness untouched.

        The split editor sends only child decisions.  Coverage therefore has to
        be recalculated from those exact child ownership pairs rather than from
        any client-side projected coverage value.
        """
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(90)
        ] + [
            _observation("optional-raw", frame, "A", "unresolved", None)
            for frame in range(90, 100)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        children = [
            _unit("optional-split:a", [("optional-raw", frame) for frame in range(90, 95)], visual=True),
            _unit("optional-split:b", [("optional-raw", frame) for frame in range(95, 100)], visual=True),
        ]
        for child in children:
            child.update({
                "current_decision": {"action": "assign_roster_player", "player_id": "p1"},
                "current_resolution_status": "reviewed_by_operator",
            })

        policy = apply_coverage_policy(children, coverage, pair_index, _scoped_match())

        self.assertTrue(policy["readiness"]["allows_finalize"])
        self.assertEqual(policy["next_cases"], [])
        self.assertEqual(policy["optional_audit_cases"], [])
        self.assertEqual(policy["optional_audit"]["pending_named_gain"], 10)
        self.assertEqual(policy["optional_audit"]["projected_named_observations"], 100)

    def test_optional_max_team_b_split_child_never_increases_team_a_named_gain(self) -> None:
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(90)
        ] + [
            _observation("optional-raw", frame, "A", "unresolved", None)
            for frame in range(90, 100)
        ]
        match_doc = _scoped_match()
        match_doc["teams"][1]["players"] = [{"id": "b1", "name": "Opponent"}]
        coverage, pair_index = summarize_effective_observations(rows, match_doc)
        child_a = _unit("optional-split:a", [("optional-raw", frame) for frame in range(90, 95)], visual=True)
        child_a.update({
            "current_decision": {"action": "assign_roster_player", "player_id": "p1"},
            "current_resolution_status": "reviewed_by_operator",
        })
        child_b = _unit("optional-split:b", [("optional-raw", frame) for frame in range(95, 100)], visual=True)
        child_b.update({
            "current_decision": {
                "action": "assign_roster_player",
                "player_id": "b1",
                # Payload fields cannot override the authoritative roster team.
                "team_label": "A",
            },
            "current_resolution_status": "reviewed_by_operator",
        })

        optional = apply_coverage_policy([child_a, child_b], coverage, pair_index, match_doc)["optional_audit"]

        self.assertEqual(optional["pending_named_gain"], 5)
        self.assertEqual(optional["projected_named_observations"], 95)

    def test_safe_maximum_at_one_hundred_percent_has_no_negative_residual(self) -> None:
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(100)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())

        optional = apply_coverage_policy([], coverage, pair_index, _scoped_match())["optional_audit"]

        self.assertEqual(optional["status"], "safe_max_reached")
        self.assertEqual(optional["safe_max_named_coverage"], 1.0)
        self.assertEqual(optional["unavailable_residual_observations"], 0)
        self.assertEqual(optional["unavailable_residual_ratio"], 0.0)

    def test_continue_to_max_ranks_overlapping_cases_by_marginal_unique_gain(self) -> None:
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(90)
        ] + [
            _observation("overlap", frame, "A", "unresolved", None)
            for frame in range(90, 100)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        broad = _unit("broad", [("overlap", frame) for frame in range(90, 100)], visual=True)
        narrow = _unit("narrow", [("overlap", frame) for frame in range(95, 100)], visual=True)
        policy = apply_coverage_policy([narrow, broad], coverage, pair_index, _scoped_match())

        self.assertEqual([row["candidate_subject_id"] for row in policy["optional_audit_cases"]], ["broad"])
        self.assertEqual(policy["optional_audit_cases"][0]["marginal_named_observation_gain"], 10)
        self.assertEqual(policy["optional_audit"]["safe_max_named_coverage"], 1.0)

    def test_deferring_broad_optional_case_exposes_overlapping_narrow_case(self) -> None:
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(90)
        ] + [
            _observation("overlap", frame, "A", "unresolved", None)
            for frame in range(90, 100)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        broad = _unit("broad", [("overlap", frame) for frame in range(90, 100)], visual=True)
        narrow = _unit("narrow", [("overlap", frame) for frame in range(95, 100)], visual=True)

        initial = apply_coverage_policy([broad, narrow], coverage, pair_index, _scoped_match())
        self.assertEqual([row["candidate_subject_id"] for row in initial["optional_audit_cases"]], ["broad"])
        self.assertNotIn("narrow", [row["candidate_subject_id"] for row in initial["optional_audit_cases"]])

        broad["current_decision"] = {"action": "unresolved"}
        broad["current_resolution_status"] = "reviewed_by_operator"
        refreshed = apply_coverage_policy([broad, narrow], coverage, pair_index, _scoped_match())

        self.assertEqual([row["candidate_subject_id"] for row in refreshed["optional_audit_cases"]], ["narrow"])
        self.assertEqual(refreshed["optional_audit_cases"][0]["marginal_named_observation_gain"], 5)

    def test_explicit_unresolved_removes_optional_max_case_and_lowers_safe_maximum(self) -> None:
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(95)
        ] + [
            _observation("unknown", frame, "A", "unresolved", None)
            for frame in range(95, 100)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        unit = _unit("unknown", [("unknown", frame) for frame in range(95, 100)], visual=True)
        unit["current_decision"] = {"action": "unresolved"}
        unit["current_resolution_status"] = "reviewed_by_operator"
        policy = apply_coverage_policy([unit], coverage, pair_index, _scoped_match())

        self.assertEqual(policy["optional_audit_cases"], [])
        self.assertEqual(policy["optional_audit"]["status"], "safe_max_reached")
        self.assertEqual(policy["optional_audit"]["safe_max_named_coverage"], 0.95)
        self.assertEqual(policy["optional_audit"]["unavailable_reason_counts"], {"explicit_unresolved": 5})
        self.assertEqual(policy["optional_audit"]["unavailable_residual_observations"], 5)
        self.assertEqual(
            sum(policy["optional_audit"]["unavailable_reason_counts"].values()),
            policy["optional_audit"]["unavailable_residual_observations"],
        )

    def test_explicit_non_naming_disposition_is_residual_not_a_max_case(self) -> None:
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(95)
        ] + [
            _observation("false", frame, "A", "unresolved", None)
            for frame in range(95, 100)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        unit = _unit("false", [("false", frame) for frame in range(95, 100)], visual=True)
        unit["current_decision"] = {"action": "false_detection"}
        unit["current_resolution_status"] = "reviewed_by_operator"

        optional = apply_coverage_policy([unit], coverage, pair_index, _scoped_match())["optional_audit"]

        self.assertEqual(optional["remaining_cases"], 0)
        self.assertEqual(optional["safe_max_named_coverage"], 0.95)
        self.assertEqual(
            optional["unavailable_reason_counts"],
            {"explicit_non_naming_disposition": 5},
        )

    def test_deferred_roster_assignment_is_projected_without_double_counting_max_queue(self) -> None:
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(90)
        ] + [
            _observation("saved", frame, "A", "unresolved", None)
            for frame in range(90, 95)
        ] + [
            _observation("next", frame, "A", "unresolved", None)
            for frame in range(95, 100)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        saved = _unit("saved", [("saved", frame) for frame in range(90, 95)], visual=True)
        saved.update({"current_decision": {"action": "assign_roster_player", "player_id": "p1"}, "current_resolution_status": "reviewed_by_operator"})
        next_case = _unit("next", [("next", frame) for frame in range(95, 100)], visual=True)
        policy = apply_coverage_policy([saved, next_case], coverage, pair_index, _scoped_match())

        optional = policy["optional_audit"]
        self.assertEqual(optional["current_named_coverage"], 0.9)
        self.assertEqual(optional["pending_named_gain"], 5)
        self.assertEqual(optional["projected_named_coverage"], 0.95)
        self.assertEqual(optional["safe_max_named_coverage"], 1.0)
        self.assertEqual([row["candidate_subject_id"] for row in policy["optional_audit_cases"]], ["next"])
        self.assertEqual(
            optional["projected_named_observations"]
            + optional["remaining_actionable_named_gain"]
            + optional["unavailable_residual_observations"],
            optional["reliable_observations"],
        )

    def test_cross_team_deferred_roster_assignment_has_no_team_a_projected_gain(self) -> None:
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(90)
        ] + [
            _observation("saved", frame, "A", "unresolved", None)
            for frame in range(90, 95)
        ]
        match_doc = _scoped_match()
        match_doc["teams"][1]["players"] = [{"id": "b1", "name": "Opponent"}]
        coverage, pair_index = summarize_effective_observations(rows, match_doc)
        saved = _unit("saved", [("saved", frame) for frame in range(90, 95)], visual=True)
        saved.update({
            "current_decision": {
                "action": "assign_roster_player",
                "player_id": "b1",
                # This deliberately misleading field must not override the roster.
                "team_label": "A",
            },
            "current_resolution_status": "reviewed_by_operator",
        })

        optional = apply_coverage_policy([saved], coverage, pair_index, match_doc)["optional_audit"]

        self.assertEqual(optional["pending_named_gain"], 0)
        self.assertEqual(optional["current_named_observations"], 90)
        self.assertEqual(optional["projected_named_observations"], 90)
        self.assertEqual(
            optional["projected_named_observations"]
            + optional["remaining_actionable_named_gain"]
            + optional["unavailable_residual_observations"],
            optional["reliable_observations"],
        )

    def test_cross_team_deferred_assignment_does_not_double_count_overlapping_max_case(self) -> None:
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(90)
        ] + [
            _observation("overlap", frame, "A", "unresolved", None)
            for frame in range(90, 100)
        ]
        match_doc = _scoped_match()
        match_doc["teams"][1]["players"] = [{"id": "b1", "name": "Opponent"}]
        coverage, pair_index = summarize_effective_observations(rows, match_doc)
        cross_team = _unit("cross-team", [("overlap", frame) for frame in range(90, 95)], visual=True)
        cross_team.update({
            "current_decision": {"action": "assign_roster_player", "player_id": "b1"},
            "current_resolution_status": "reviewed_by_operator",
        })
        remaining = _unit("remaining", [("overlap", frame) for frame in range(90, 100)], visual=True)

        optional = apply_coverage_policy([cross_team, remaining], coverage, pair_index, match_doc)["optional_audit"]

        self.assertEqual(optional["pending_named_gain"], 0)
        self.assertEqual(optional["remaining_actionable_named_gain"], 10)
        self.assertEqual(
            optional["projected_named_observations"]
            + optional["remaining_actionable_named_gain"]
            + optional["unavailable_residual_observations"],
            optional["reliable_observations"],
        )

    def test_numeric_minimum_is_distinct_from_required_readiness(self) -> None:
        members = _continuity_members(team="A")
        material = next(
            row
            for row in coalesce_material_continuity_units(members, fps=10.0)
            if row.get("scope_kind") == "material_continuity"
        )
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(3_000)
        ] + [
            _observation(tracklet_id, frame, "A", "unresolved", None)
            for member in members
            for tracklet_id, frame in member["detected_pairs"]
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())

        optional = apply_coverage_policy([material], coverage, pair_index, _scoped_match())["optional_audit"]

        self.assertTrue(optional["current_minimum_target_met"])
        self.assertTrue(optional["projected_minimum_target_met"])
        self.assertFalse(optional["required_readiness_met"])
        self.assertEqual(optional["status"], "not_ready")

    def test_optional_max_queue_is_uncapped_and_its_global_summary_is_not_page_scoped(self) -> None:
        named = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(9_000)
        ]
        unnamed = []
        units = []
        for index in range(250):
            tracklet_id = f"optional-{index:03d}"
            pairs = [(tracklet_id, frame) for frame in range(4)]
            unnamed.extend(_observation(tracklet_id, frame, "A", "unresolved", None) for _, frame in pairs)
            units.append(_unit(f"optional-{index:03d}", pairs, visual=True))
        coverage, pair_index = summarize_effective_observations([*named, *unnamed], _scoped_match())

        policy = apply_coverage_policy(units, coverage, pair_index, _scoped_match())
        page = paginate_progress(policy, queue="optional_audit", limit=20)

        self.assertTrue(policy["readiness"]["allows_finalize"])
        self.assertEqual(len(policy["optional_audit_cases"]), 250)
        self.assertFalse(policy["workload"]["queue_truncated"])
        self.assertEqual(policy["optional_audit"]["remaining_cases"], 250)
        self.assertEqual(policy["optional_audit"]["actionable_unique_observations_remaining"], 1_000)
        self.assertEqual(len(page["next_cases"]), 20)
        self.assertEqual(page["pagination"]["global_total_remaining"], 250)

    def test_continue_to_max_excludes_team_b_material_and_semantic_units(self) -> None:
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(95)
        ] + [
            _observation("a", frame, "A", "unresolved", None)
            for frame in range(95, 100)
        ] + [
            _observation("b", frame, "B", "unresolved", None)
            for frame in range(10)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        semantic = _unit("semantic", [("a", frame) for frame in range(95, 98)], visual=True)
        semantic.update({"current_resolution_status": "pending_high_priority", "reason_codes": ["cross_team_conflict"]})
        material = _material_case("material", range(98, 100))
        b_unit = _unit("b", [("b", frame) for frame in range(10)], visual=True)
        b_unit.update({"source_team_label": "B", "effective_team_label": "B"})
        policy = apply_coverage_policy([semantic, material, b_unit], coverage, pair_index, _scoped_match())

        self.assertEqual(policy["optional_audit_cases"], [])
        self.assertEqual(policy["optional_audit"]["status"], "not_ready")
        self.assertGreater(policy["semantic_blockers"], 0)
        self.assertGreater(policy["material_continuity_blockers"], 0)

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
    def test_team_stats_only_does_not_enter_team_a_optional_max_audit(self) -> None:
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
        self.assertEqual(policy["optional_audit_cases"], [])
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
            "source_team_label": "B",
            "effective_team_label": "B",
            "detected_team_labels": ["A", "B"],
            "operator_actionable": False,
            "non_actionable_reason": "missing_visual_evidence",
        })

        policy = apply_coverage_policy([uncertain], coverage, pair_index, match)

        self.assertEqual(policy["next_cases"], [])
        self.assertTrue(policy["readiness"]["allows_finalize"])
        self.assertEqual(policy["readiness"]["team_attribution_residual"]["ordinary_optional_units"], 1)

    def test_bounded_genuinely_unavailable_team_u_residual_does_not_block_finalize(self) -> None:
        # Mirrors the observed terminal state: complete-roster A is already
        # above 90% named, B is team-stats-only, and a tiny Team-U remainder
        # has been evaluated but has no safe crop for an operator decision.
        rows = [
            _observation(
                "a",
                frame,
                "A",
                "confirmed" if frame < 950 else "unresolved",
                "p1" if frame < 950 else None,
            )
            for frame in range(1_000)
        ] + [
            _observation("b", frame, "B", "unresolved", None)
            for frame in range(47)
        ] + [
            _observation("u", frame, "U", "team_unknown", None)
            for frame in range(3)
        ]
        match = _scoped_match()
        coverage, pair_index = summarize_effective_observations(rows, match)
        uncertain = _unit("unknown-without-crops", [("u", frame) for frame in range(3)], visual=False)
        uncertain.update({
            "source_team_label": "U",
            "effective_team_label": "U",
            "operator_actionable": False,
            "non_actionable_reason": "missing_visual_evidence",
            "team_attribution_evidence_status": "no_team_attribution_evidence",
        })

        policy = apply_coverage_policy([uncertain], coverage, pair_index, match)

        self.assertEqual(policy["next_cases"], [])
        self.assertTrue(policy["readiness"]["allows_finalize"])
        residual = policy["readiness"]["team_attribution_residual"]
        self.assertEqual(residual["status"], "accepted_within_tolerance")
        self.assertEqual(residual["observations"], 3)
        self.assertEqual(residual["residual_budget_observations"], 105)
        self.assertEqual(coverage["per_team"]["A"]["named_observation_coverage"], 0.95)
        self.assertEqual(coverage["per_team"]["B"]["named_coverage_status"], "not_required_by_scope")
        self.assertEqual(coverage["per_team"]["U"]["team_known_observation_coverage"], 0.0)
        self.assertEqual(coverage["team_known_observation_coverage"], 0.9971)
        self.assertEqual(pair_index[("u", 0)]["team_label"], "U")
        self.assertIsNone(pair_index[("u", 0)]["canonical_player_id"])

    def test_unmaterialized_team_u_evidence_is_a_remediable_not_terminal_residual(self) -> None:
        rows = [_observation("u", frame, "U", "team_unknown", None) for frame in range(3)]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        uncertain = _unit("not-materialized", [("u", frame) for frame in range(3)], visual=False)
        uncertain.update({
            "source_team_label": "U",
            "effective_team_label": "U",
            "operator_actionable": False,
            "team_attribution_evidence_status": "team_attribution_evidence_not_materialized",
        })

        policy = apply_coverage_policy([uncertain], coverage, pair_index, _scoped_match())

        self.assertFalse(policy["readiness"]["allows_finalize"])
        self.assertEqual(
            policy["readiness"]["team_attribution_residual"]["status"],
            "materialization_required",
        )
        self.assertEqual(
            policy["readiness"]["blockers"][0]["code"],
            "team_attribution_evidence_not_materialized",
        )

    def test_missing_or_unknown_team_u_evidence_status_fails_closed_within_budget(self) -> None:
        # The global 90% allowance is exactly three observations here.  These
        # cases prove that an absent or future status does not become terminal
        # merely because it fits inside that otherwise valid residual budget.
        rows = [
            _observation("a", frame, "A", "confirmed", "p1")
            for frame in range(27)
        ] + [
            _observation("u", frame, "U", "team_unknown", None)
            for frame in range(3)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        for name, evidence_status in (("missing", None), ("unknown", "future_status")):
            with self.subTest(name=name):
                uncertain = _unit(
                    f"{name}-status",
                    [("u", frame) for frame in range(3)],
                    visual=False,
                )
                uncertain.update({
                    "source_team_label": "U",
                    "effective_team_label": "U",
                    "operator_actionable": False,
                })
                if evidence_status is not None:
                    uncertain["team_attribution_evidence_status"] = evidence_status

                policy = apply_coverage_policy(
                    [uncertain], coverage, pair_index, _scoped_match()
                )

                self.assertTrue(policy["readiness"]["allows_finalize"])
                residual = policy["readiness"]["team_attribution_residual"]
                self.assertEqual(residual["status"], "accepted_within_tolerance")
                self.assertTrue(residual["within_tolerance"])
                self.assertEqual(
                    residual["evidence_status_counts"],
                    {},
                )

    def test_technical_team_evidence_failures_do_not_consume_residual_tolerance(self) -> None:
        rows = [_observation("u", frame, "U", "team_unknown", None) for frame in range(3)]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        for evidence_status in ("source_video_unavailable", "team_attribution_crops_unavailable"):
            with self.subTest(evidence_status=evidence_status):
                uncertain = _unit("technical", [("u", frame) for frame in range(3)], visual=False)
                uncertain.update({
                    "source_team_label": "U",
                    "effective_team_label": "U",
                    "operator_actionable": False,
                    "team_attribution_evidence_status": evidence_status,
                })
                policy = apply_coverage_policy([uncertain], coverage, pair_index, _scoped_match())

                self.assertFalse(policy["readiness"]["allows_finalize"])
                residual = policy["readiness"]["team_attribution_residual"]
                self.assertEqual(residual["status"], "technical_evidence_failure")
                self.assertEqual(policy["readiness"]["blockers"][0]["code"], "team_attribution_evidence_technical_failure")

    def test_unknown_case_is_not_an_operator_card_and_assignment_can_clear_its_residual(self) -> None:
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

        self.assertEqual(before["next_cases"], [])
        self.assertFalse(before["readiness"]["allows_finalize"])
        self.assertEqual(after["next_cases"], [])
        self.assertEqual(after["optional_audit_cases"], [])

    def test_team_u_with_evidence_is_a_terminal_residual_not_a_required_card(self) -> None:
        # ``has_operator_visual_evidence`` alone is not proof. The anchor
        # crops still have to belong to this exact Team-U ownership scope.
        rows = [_observation("u", frame, "U", "unresolved", None) for frame in range(12)]
        match = _scoped_match()
        coverage, pair_index = summarize_effective_observations(rows, match)
        unknown = _unit("unknown", [("u", frame) for frame in range(12)], visual=True)
        unknown.update({"source_team_label": "U", "effective_team_label": "U"})

        policy = apply_coverage_policy([unknown], coverage, pair_index, match)

        self.assertEqual(policy["semantic_blockers"], 0)
        self.assertEqual(policy["coverage_blockers"], 0)
        self.assertEqual(policy["next_cases"], [])
        self.assertEqual(
            policy["readiness"]["team_attribution_residual"]["status"],
            "materialization_required",
        )
        self.assertFalse(policy["readiness"]["allows_finalize"])

    def test_exact_rendered_team_attribution_evidence_becomes_required(self) -> None:
        pairs = [("u", frame) for frame in range(12)]
        rows = [_observation("u", frame, "U", "unresolved", None) for _, frame in pairs]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        unit = _unit("team-evidence", pairs, visual=True)
        unit.update({
            "source_team_label": "U",
            "effective_team_label": "U",
            "team_attribution_evidence_source_digest": "current-source",
            "visual_evidence": _safe_team_attribution_evidence(
                pairs,
                source_digest="current-source",
            ),
        })

        policy = apply_coverage_policy([unit], coverage, pair_index, _scoped_match())

        self.assertEqual(policy["semantic_blockers"], 0)
        self.assertEqual(policy["coverage_blockers"], 1)
        self.assertEqual(
            [case["candidate_subject_id"] for case in policy["next_cases"]],
            ["team-evidence"],
        )
        self.assertEqual(
            policy["readiness"]["team_attribution_residual"]["status"],
            "none",
        )

    def test_exact_ordinary_operator_evidence_for_team_u_becomes_required(self) -> None:
        pairs = [("u", frame) for frame in range(12)]
        rows = [_observation("u", frame, "U", "unresolved", None) for _, frame in pairs]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        unit = _unit("ordinary-evidence", pairs, visual=True)
        unit.update({
            "source_team_label": "U",
            "effective_team_label": "U",
            "team_attribution_evidence_source_digest": "current-source",
            "visual_evidence": _safe_ordinary_operator_evidence(pairs),
            "team_attribution_evidence_status": "focused_source_not_reviewable",
        })

        policy = apply_coverage_policy([unit], coverage, pair_index, _scoped_match())

        self.assertEqual(policy["semantic_blockers"], 0)
        self.assertEqual(policy["coverage_blockers"], 1)
        self.assertEqual(
            [case["candidate_subject_id"] for case in policy["next_cases"]],
            ["ordinary-evidence"],
        )
        self.assertEqual(
            policy["readiness"]["team_attribution_residual"]["status"],
            "none",
        )

    def test_stale_team_attribution_evidence_stays_a_technical_failure(self) -> None:
        pairs = [("u", frame) for frame in range(12)]
        rows = [_observation("u", frame, "U", "unresolved", None) for _, frame in pairs]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        unit = _unit("stale-evidence", pairs, visual=True)
        unit.update({
            "source_team_label": "U",
            "effective_team_label": "U",
            "team_attribution_evidence_source_digest": "current-source",
            "visual_evidence": _safe_team_attribution_evidence(
                pairs,
                source_digest="stale-source",
            ),
        })

        policy = apply_coverage_policy([unit], coverage, pair_index, _scoped_match())

        self.assertEqual(policy["next_cases"], [])
        self.assertEqual(
            policy["readiness"]["team_attribution_residual"]["status"],
            "technical_evidence_failure",
        )
        self.assertEqual(
            policy["readiness"]["team_attribution_residual"]["evidence_status_counts"],
            {"team_attribution_evidence_source_digest_mismatch": 1},
        )

    def test_team_u_team_assignment_or_non_player_disposition_leaves_safety_queue(self) -> None:
        match = _scoped_match()
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(100)
        ] + [
            _observation("u", frame, "A", "unresolved", None)
            for frame in range(100, 110)
        ]
        coverage, pair_index = summarize_effective_observations(rows, match)
        assigned_a = _unit("u", [("u", frame) for frame in range(100, 110)], visual=True)
        assigned_a.update({
            "source_team_label": "U",
            "effective_team_label": "A",
            "current_decision": {"action": "assign_team", "team_label": "A"},
            "current_resolution_status": "reviewed_by_operator",
        })
        assigned_b = {
            **assigned_a,
            "effective_team_label": "B",
            "current_decision": {"action": "assign_team", "team_label": "B"},
        }
        false_detection = {
            **assigned_a,
            "effective_team_label": "U",
            "current_decision": {"action": "false_detection"},
        }
        referee = {
            **assigned_a,
            "effective_team_label": "U",
            "current_decision": {"action": "referee"},
        }

        self.assertEqual(
            apply_coverage_policy([assigned_a], coverage, pair_index, match)["next_cases"],
            [],
        )
        assigned_b_policy = apply_coverage_policy([assigned_b], coverage, pair_index, match)
        self.assertEqual(assigned_b_policy["next_cases"], [])
        self.assertEqual(
            assigned_b_policy["residual_by_team"]["B"]["scope"],
            "team_stats_only",
        )
        self.assertEqual(
            apply_coverage_policy([false_detection], coverage, pair_index, match)["next_cases"],
            [],
        )
        self.assertEqual(
            apply_coverage_policy([referee], coverage, pair_index, match)["next_cases"],
            [],
        )

    def test_optional_audit_is_filtered_then_paginated_without_affecting_workload(self) -> None:
        optional = [_queue_unit(f"a-{index:03d}", "A", priority="optional") for index in range(75)]
        progress = {
            "next_cases": [_queue_unit("required-a", "A")],
            "optional_audit_cases": optional,
            "workload": {"remaining_cases": 1, "level": "normal"},
        }

        page = paginate_progress(
            progress,
            queue="optional_audit",
            team_label="A",
            offset=20,
            limit=20,
        )

        self.assertEqual(page["queue"], "optional_audit")
        self.assertEqual(len(page["next_cases"]), 20)
        self.assertEqual(page["pagination"]["total_remaining"], 75)
        self.assertEqual(page["next_cases"][0]["candidate_subject_id"], "a-020")
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
        self.assertEqual(optional["optional_audit_cases"], [])
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

    def test_ordinary_team_u_is_optional_when_global_team_known_target_is_met(self) -> None:
        match = _scoped_match()
        match["identity_review_scope"]["teams"] = {"A": "team_stats_only", "B": "team_stats_only"}
        rows = [
            _observation("known", frame, "B", "unresolved", None)
            for frame in range(900)
        ] + [
            _observation("u", frame, "U", "unresolved", None)
            for frame in range(100)
        ]
        coverage, pair_index = summarize_effective_observations(rows, match)
        units = []
        for index in range(50):
            pairs = [("u", index * 2), ("u", index * 2 + 1)]
            unit = _unit(f"u-{index}", pairs, visual=True)
            unit.update({"source_team_label": "U", "effective_team_label": "U", "detected_team_labels": ["A", "B"], "current_resolution_status": "pending_high_priority", "team_attribution_evidence_source_digest": f"u-{index}", "visual_evidence": _safe_team_attribution_evidence(pairs, source_digest=f"u-{index}")})
            units.append(unit)
        policy = apply_coverage_policy(units, coverage, pair_index, match)
        self.assertEqual(policy["next_cases"], [])
        self.assertTrue(policy["readiness"]["allows_finalize"])
        self.assertEqual(policy["readiness"]["team_attribution_residual"]["ordinary_optional_units"], 50)
        # Accepted residual is not silently converted into team-owned stats.
        self.assertEqual(pair_index[("u", 0)]["team_label"], "U")

    def test_team_u_selects_minimum_cases_by_marginal_unique_gain(self) -> None:
        match = _scoped_match()
        match["identity_review_scope"]["teams"] = {"A": "team_stats_only", "B": "team_stats_only"}
        rows = [
            _observation("known", frame, "B", "unresolved", None)
            for frame in range(850)
        ] + [
            _observation("u", frame, "U", "unresolved", None)
            for frame in range(150)
        ]
        coverage, pair_index = summarize_effective_observations(rows, match)
        def uncertain(name: str, frames: list[int]) -> dict:
            pairs = [("u", frame) for frame in frames]
            value = _unit(name, pairs, visual=True)
            value.update({"source_team_label": "U", "effective_team_label": "U", "detected_team_labels": ["A", "B"], "current_resolution_status": "pending_high_priority", "team_attribution_evidence_source_digest": name, "visual_evidence": _safe_team_attribution_evidence(pairs, source_digest=name)})
            return value
        # The second source overlaps the first heavily. A raw-size sort would
        # choose it next, whereas marginal selection must prefer c (30 new).
        policy = apply_coverage_policy(
            [uncertain("a", list(range(0, 40))), uncertain("b", list(range(10, 60))), uncertain("c", list(range(60, 110)))],
            coverage, pair_index, match,
        )
        self.assertEqual([row["candidate_subject_id"] for row in policy["next_cases"]], ["c"])
        self.assertEqual(policy["team_attribution_selection"]["selected_required_team_known_gain"], 50)

    def test_structural_team_conflict_remains_required_above_target(self) -> None:
        match = _scoped_match()
        rows = [_observation("known", frame, "A", "unresolved", None) for frame in range(95)] + [_observation("u", frame, "U", "unresolved", None) for frame in range(5)]
        coverage, pair_index = summarize_effective_observations(rows, match)
        unit = _unit("structural", [("u", frame) for frame in range(5)], visual=True)
        unit.update({"source_team_label": "U", "effective_team_label": "U", "detected_team_labels": ["A", "B"], "current_resolution_status": "pending_high_priority", "reason_codes": ["duplicate_physical_ownership"]})
        policy = apply_coverage_policy([unit], coverage, pair_index, match)
        self.assertEqual([row["candidate_subject_id"] for row in policy["next_cases"]], ["structural"])

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

    def test_team_filters_select_a_b_or_uncertain_team(self) -> None:
        cases = [
            _queue_unit("a", "A"),
            _queue_unit("b", "B"),
            _queue_unit("unknown", "U"),
        ]

        page_a = paginate_progress({"next_cases": cases}, team_label="A")
        page_b = paginate_progress({"next_cases": cases}, team_label="B")
        page_u = paginate_progress({"next_cases": cases}, team_label="U")

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
        self.assertEqual([row["candidate_subject_id"] for row in page_u["next_cases"]], ["unknown"])
        self.assertEqual(page_a["pagination"]["global_total_remaining"], 3)
        self.assertEqual(page_a["filters"]["counts"]["U"], 1)
        self.assertEqual(
            sum(page_a["filters"]["counts"][key] for key in ("A", "B", "U")),
            page_a["filters"]["counts"]["all"],
        )

    def test_filter_keeps_conflicting_or_unknown_team_evidence_out_of_named_team_tabs(self) -> None:
        self.assertEqual(
            review_case_team_label({
                "coverage_team_label": "B",
                "effective_team_label": "A",
                "source_team_label": "A",
            }),
            "U",
        )
        self.assertEqual(
            review_case_team_label({
                "coverage_team_label": "U",
                "effective_team_label": "A",
                "source_team_label": "B",
            }),
            "U",
        )
        self.assertEqual(
            review_case_team_label({
                "effective_team_label": "U",
                "source_team_label": "B",
                "current_decision": {"action": "team_unknown"},
            }),
            "U",
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
        with self.assertRaisesRegex(ValueError, "team_label must be A, B or U"):
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

    def test_material_continuity_stays_required_above_complete_roster_target(self) -> None:
        members = _continuity_members(team="A")
        grouped = coalesce_material_continuity_units(members, fps=10.0)
        material = next(
            row for row in grouped if row.get("scope_kind") == "material_continuity"
        )
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(3_000)
        ] + [
            _observation(tracklet_id, frame, "A", "unresolved", None)
            for member in members
            for tracklet_id, frame in member["detected_pairs"]
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())

        policy = apply_coverage_policy(grouped, coverage, pair_index, _scoped_match())

        self.assertGreater(coverage["per_team"]["A"]["named_observation_coverage"], 0.90)
        self.assertEqual(policy["residual_by_team"]["A"]["required_named_gain"], 0)
        self.assertEqual(policy["material_continuity_blockers"], 1)
        self.assertEqual(policy["coverage_blockers"], 0)
        self.assertEqual(policy["next_cases"][0]["candidate_subject_id"], material["candidate_subject_id"])
        self.assertEqual(policy["next_cases"][0]["priority"], "continuity")
        self.assertIn("material_identity_continuity_gap", policy["next_cases"][0]["reason_codes"])

    def test_unresolved_material_case_is_complete_above_named_coverage_target(self) -> None:
        members = _continuity_members(team="A")
        material = next(
            row
            for row in coalesce_material_continuity_units(members, fps=10.0)
            if row.get("scope_kind") == "material_continuity"
        )
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(3_000)
        ] + [
            _observation(tracklet_id, frame, "A", "unresolved", None)
            for member in members
            for tracklet_id, frame in member["detected_pairs"]
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        named_coverage_before = coverage["per_team"]["A"]["named_observation_coverage"]
        material.update(
            current_decision={"action": "unresolved"},
            current_resolution_status="reviewed_by_operator",
        )

        policy = apply_coverage_policy([material], coverage, pair_index, _scoped_match())

        self.assertGreater(named_coverage_before, 0.90)
        self.assertEqual(
            coverage["per_team"]["A"]["named_observation_coverage"],
            named_coverage_before,
        )
        self.assertEqual(policy["material_continuity_blockers"], 0)
        self.assertEqual(policy["coverage_blockers"], 0)
        self.assertEqual(policy["next_cases"], [])
        self.assertTrue(policy["readiness"]["allows_finalize"])

    def test_unresolved_material_case_leaves_normal_coverage_debt_below_target(self) -> None:
        material = _material_case("material", range(880, 1_000))
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(880)
        ] + [
            _observation("material", frame, "A", "unresolved", None)
            for frame in range(880, 1_000)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        named_coverage_before = coverage["per_team"]["A"]["named_observation_coverage"]
        material.update(
            current_decision={"action": "unresolved"},
            current_resolution_status="reviewed_by_operator",
        )

        policy = apply_coverage_policy([material], coverage, pair_index, _scoped_match())

        self.assertEqual(named_coverage_before, 0.88)
        self.assertEqual(
            coverage["per_team"]["A"]["named_observation_coverage"],
            named_coverage_before,
        )
        self.assertEqual(policy["material_continuity_blockers"], 0)
        self.assertEqual(policy["coverage_blockers"], 0)
        self.assertEqual(policy["next_cases"], [])
        self.assertFalse(policy["readiness"]["allows_finalize"])
        self.assertIn(
            "complete_roster_named_coverage_gap_unreachable",
            {row["code"] for row in policy["readiness"]["blockers"]},
        )

    def test_explicit_unresolved_coverage_case_is_not_requeued_to_force_a_name(self) -> None:
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(880)
        ] + [
            _observation("ambiguous", frame, "A", "unresolved", None)
            for frame in range(880, 1_000)
        ]
        coverage, pair_index = summarize_effective_observations(rows, _scoped_match())
        ambiguous = _unit(
            "ambiguous",
            [("ambiguous", frame) for frame in range(880, 1_000)],
            visual=True,
        )
        ambiguous.update(
            current_decision={"action": "unresolved"},
            current_resolution_status="reviewed_by_operator",
        )

        policy = apply_coverage_policy([ambiguous], coverage, pair_index, _scoped_match())

        self.assertEqual(policy["coverage_blockers"], 0)
        self.assertEqual(policy["next_cases"], [])
        self.assertFalse(policy["readiness"]["allows_finalize"])
        self.assertIn(
            "complete_roster_named_coverage_gap_unreachable",
            {row["code"] for row in policy["readiness"]["blockers"]},
        )

    def test_stale_unresolved_material_decision_does_not_hide_changed_case(self) -> None:
        members = _continuity_members(team="A")
        original = next(
            row
            for row in coalesce_material_continuity_units(members, fps=10.0)
            if row.get("scope_kind") == "material_continuity"
        )
        decisions = {
            "decisions": [
                {
                    "continuity_group_id": original["continuity_group_id"],
                    "source_ownership_digest": original["source_ownership_digest"],
                    "action": "unresolved",
                }
            ]
        }
        changed_members = [dict(member) for member in members]
        changed_members[0] = {
            **changed_members[0],
            "detected_pairs": [
                *changed_members[0]["detected_pairs"],
                ["continuity-tracklet-1", 4_060],
            ],
        }

        changed = next(
            row
            for row in coalesce_material_continuity_units(
                changed_members,
                fps=10.0,
                decisions=decisions,
            )
            if row.get("scope_kind") == "material_continuity"
        )

        self.assertNotEqual(
            changed["source_ownership_digest"],
            original["source_ownership_digest"],
        )
        self.assertIsNone(changed["current_decision"])
        self.assertEqual(
            changed["current_resolution_status"],
            "pending_material_continuity_review",
        )

    def test_material_gain_alone_satisfies_sub_target_coverage_without_ordinary_cards(self) -> None:
        material = _material_case("material", range(870, 1320))
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(870)
        ] + [
            _observation("material", frame, "A", "unresolved", None)
            for frame in range(870, 1320)
        ]
        coverage, pairs = summarize_effective_observations(rows, _scoped_match())

        policy = apply_coverage_policy([material], coverage, pairs, _scoped_match())

        residual = policy["residual_by_team"]["A"]
        self.assertLess(coverage["per_team"]["A"]["named_observation_coverage"], 0.90)
        self.assertEqual(policy["material_continuity_blockers"], 1)
        self.assertEqual(policy["coverage_blockers"], 0)
        self.assertGreater(residual["required_named_gain"], 0)
        self.assertGreaterEqual(
            residual["independently_required_named_gain"],
            residual["required_named_gain"],
        )
        self.assertEqual(residual["remaining_uncovered_named_gain"], 0)
        self.assertNotIn(
            "complete_roster_named_coverage_gap_unreachable",
            {row["code"] for row in policy["readiness"]["blockers"]},
        )

    def test_material_gain_leaves_only_residual_ordinary_coverage_selection(self) -> None:
        material = _material_case("material", range(870, 990))
        ordinary = _continuity_unit(
            "ordinary",
            "ordinary",
            range(990, 1290),
            stable_slot_id="A11",
            team="A",
        )
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(870)
        ] + [
            _observation("material", frame, "A", "unresolved", None)
            for frame in range(870, 990)
        ] + [
            _observation("ordinary", frame, "A", "unresolved", None)
            for frame in range(990, 1290)
        ]
        coverage, pairs = summarize_effective_observations(rows, _scoped_match())

        policy = apply_coverage_policy([material, ordinary], coverage, pairs, _scoped_match())

        residual = policy["residual_by_team"]["A"]
        self.assertGreater(residual["required_named_gain"], residual["independently_required_named_gain"])
        self.assertEqual(policy["coverage_blockers"], 1)
        self.assertEqual(policy["next_cases"][0]["candidate_subject_id"], "ordinary")
        self.assertEqual(residual["remaining_uncovered_named_gain"], 0)

    def test_material_and_ordinary_overlap_use_unique_pair_gain(self) -> None:
        material = _material_case("material", range(870, 970))
        ordinary = _continuity_unit(
            "ordinary",
            "ordinary",
            range(920, 1070),
            stable_slot_id="A11",
            team="A",
        )
        rows = [
            _observation("named", frame, "A", "confirmed", "p1")
            for frame in range(870)
        ] + [
            _observation("material", frame, "A", "unresolved", None)
            for frame in range(870, 970)
        ] + [
            _observation("ordinary", frame, "A", "unresolved", None)
            for frame in range(920, 1070)
        ]
        coverage, pairs = summarize_effective_observations(rows, _scoped_match())

        policy = apply_coverage_policy([material, ordinary], coverage, pairs, _scoped_match())

        # Different tracklets at the same frame are separate observations;
        # make the ordinary case reuse the same exact pairs to exercise union.
        ordinary["detected_pairs"] = list(material["detected_pairs"]) + ordinary["detected_pairs"][:50]
        policy = apply_coverage_policy([material, ordinary], coverage, pairs, _scoped_match())
        self.assertEqual(policy["residual_by_team"]["A"]["available_actionable_named_gain"], 150)

    def test_single_safe_25_second_subject_is_material_without_fragment_gate(self) -> None:
        single = _continuity_unit(
            "single",
            "single-tracklet",
            range(1_000, 1_250),
            stable_slot_id="A17",
            team="A",
        )
        grouped = coalesce_material_continuity_units([single], fps=10.0)
        material = next(row for row in grouped if row.get("scope_kind") == "material_continuity")
        self.assertEqual(material["continuity_fragment_count"], 1)
        self.assertTrue(material["material_continuity_required"])

    def test_just_under_twenty_second_subject_is_not_material(self) -> None:
        single = _continuity_unit(
            "short-single",
            "short-tracklet",
            range(1_000, 1_199),
            stable_slot_id="A17",
            team="A",
        )
        grouped = coalesce_material_continuity_units([single], fps=10.0)
        self.assertFalse(any(row.get("scope_kind") == "material_continuity" for row in grouped))

    def test_team_u_subject_is_never_promoted_to_material_continuity(self) -> None:
        unknown_team = _continuity_unit(
            "unknown-team",
            "unknown-tracklet",
            range(0, 250),
            stable_slot_id="U12",
            team="U",
        )

        grouped = coalesce_material_continuity_units([unknown_team], fps=10.0)

        self.assertFalse(
            any(row.get("scope_kind") == "material_continuity" for row in grouped)
        )

    def test_short_safe_residual_is_not_promoted_to_material_continuity(self) -> None:
        short_members = _continuity_members(team="A", frames_per_member=10)

        grouped = coalesce_material_continuity_units(short_members, fps=10.0)

        self.assertFalse(any(row.get("scope_kind") == "material_continuity" for row in grouped))
        self.assertEqual(
            {row["candidate_subject_id"] for row in grouped},
            {row["candidate_subject_id"] for row in short_members},
        )

    def test_material_case_groups_exact_multi_fragment_observations_only(self) -> None:
        members = _continuity_members(team="A")
        unrelated = _continuity_unit(
            "later-subject",
            "later-tracklet",
            range(1_000, 1_050),
            stable_slot_id="A12",
            team="A",
        )

        grouped = coalesce_material_continuity_units([*members, unrelated], fps=10.0)
        material = next(
            row for row in grouped if row.get("scope_kind") == "material_continuity"
        )

        expected_pairs = {
            pair for member in members for pair in member["detected_pairs"]
        }
        self.assertEqual(set(map(tuple, material["detected_pairs"])), expected_pairs)
        self.assertEqual(material["continuity_fragment_count"], 4)
        self.assertEqual(
            set(material["continuity_subject_ids"]),
            {member["candidate_subject_id"] for member in members},
        )
        self.assertNotIn("later-subject", material["continuity_subject_ids"])
        self.assertIn("later-subject", {row["candidate_subject_id"] for row in grouped})

    def test_team_b_fragments_never_gain_required_naming_workload(self) -> None:
        members = _continuity_members(team="B")

        grouped = coalesce_material_continuity_units(members, fps=10.0)

        self.assertFalse(any(row.get("scope_kind") == "material_continuity" for row in grouped))

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

    def test_larger_coverage_impact_ranks_before_a_smaller_semantic_conflict(self) -> None:
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

        self.assertEqual(policy["next_cases"][0]["candidate_subject_id"], "large-subject")
        self.assertEqual(policy["next_cases"][0]["priority"], "coverage")

    def test_required_queue_globally_ranks_selected_categories_by_operator_impact(self) -> None:
        match = _scoped_match()
        match["identity_review_scope"]["teams"]["B"] = "complete_roster"
        pair_index: dict[tuple[str, int], dict] = {}
        next_frame = 0

        def pairs_for(subject_id: str, count: int, team: str) -> list[tuple[str, int]]:
            nonlocal next_frame
            pairs = [(subject_id, frame) for frame in range(next_frame, next_frame + count)]
            next_frame += count
            pair_index.update({
                pair: {
                    "team_label": team,
                    "identity_status": "unresolved",
                    "canonical_player_id": None,
                }
                for pair in pairs
            })
            return pairs

        def semantic(subject_id: str, count: int, team: str) -> dict:
            unit = _unit(subject_id, pairs_for(subject_id, count, team), visual=True)
            unit.update({
                "source_team_label": team,
                "effective_team_label": team,
                "current_resolution_status": "pending_high_priority",
                "priority": "high",
                "reason_codes": ["identity_conflict"],
            })
            return unit

        def material(subject_id: str, count: int, team: str) -> dict:
            pairs = pairs_for(subject_id, count, team)
            unit = _material_case(subject_id, range(pairs[0][1], pairs[-1][1] + 1))
            unit.update({"source_team_label": team, "effective_team_label": team})
            return unit

        def coverage(subject_id: str, count: int, team: str) -> dict:
            unit = _unit(subject_id, pairs_for(subject_id, count, team), visual=True)
            unit.update({"source_team_label": team, "effective_team_label": team})
            return unit

        units = [
            semantic("semantic-3", 60, "A"),
            semantic("semantic-1", 20, "A"),
            semantic("semantic-0.1", 2, "A"),
            material("material-2", 40, "B"),
            material("material-0.5", 10, "A"),
            coverage("coverage-1.5", 30, "A"),
            coverage("coverage-0.75", 15, "B"),
            coverage("coverage-0.05", 1, "B"),
        ]
        coverage_state = {
            "reliable_observations": 4_000,
            "per_team": {
                "A": {"reliable_observations": 2_000, "confirmed_named_observations": 1_600},
                "B": {"reliable_observations": 2_000, "confirmed_named_observations": 1_600},
            },
        }

        policy = apply_coverage_policy(units, coverage_state, pair_index, match)

        self.assertEqual(policy["semantic_blockers"], 3)
        self.assertEqual(policy["material_continuity_blockers"], 2)
        self.assertEqual(policy["coverage_blockers"], 3)
        self.assertEqual(len(policy["next_cases"]), 8)
        self.assertEqual(
            [row["candidate_subject_id"] for row in policy["next_cases"]],
            [
                "semantic-3",
                "material-2",
                "coverage-1.5",
                "semantic-1",
                "coverage-0.75",
                "material-0.5",
                "semantic-0.1",
                "coverage-0.05",
            ],
        )
        self.assertEqual(
            [row["operator_impact_pp"] for row in policy["next_cases"]],
            [3.0, 2.0, 1.5, 1.0, 0.75, 0.5, 0.1, 0.05],
        )

        first_page = paginate_progress(policy, limit=3)
        second_page = paginate_progress(policy, offset=3, limit=3)
        self.assertEqual(
            [row["candidate_subject_id"] for row in first_page["next_cases"]],
            ["semantic-3", "material-2", "coverage-1.5"],
        )
        self.assertEqual(
            [row["candidate_subject_id"] for row in second_page["next_cases"]],
            ["semantic-1", "coverage-0.75", "material-0.5"],
        )
        self.assertEqual(
            [row["candidate_subject_id"] for row in paginate_progress(policy, team_label="A")["next_cases"]],
            ["semantic-3", "coverage-1.5", "semantic-1", "material-0.5", "semantic-0.1"],
        )
        self.assertEqual(
            [row["candidate_subject_id"] for row in paginate_progress(policy, team_label="B")["next_cases"]],
            ["material-2", "coverage-0.75", "coverage-0.05"],
        )

    def test_smaller_raw_case_with_higher_percentage_point_impact_ranks_first(self) -> None:
        match = _scoped_match()
        match["identity_review_scope"]["teams"]["B"] = "complete_roster"
        pair_index = {
            **{
                ("a-larger-raw-lower-pp", frame): {
                    "team_label": "A",
                    "identity_status": "unresolved",
                    "canonical_player_id": None,
                }
                for frame in range(800)
            },
            **{
                ("b-smaller-raw-higher-pp", frame): {
                    "team_label": "B",
                    "identity_status": "unresolved",
                    "canonical_player_id": None,
                }
                for frame in range(500)
            },
        }
        a_larger_raw_lower_pp = _unit(
            "a-larger-raw-lower-pp",
            [("a-larger-raw-lower-pp", frame) for frame in range(800)],
            visual=True,
        )
        a_larger_raw_lower_pp.update({
            "current_resolution_status": "pending_high_priority",
            "priority": "high",
            "reason_codes": ["identity_conflict"],
        })
        b_smaller_raw_higher_pp = _unit(
            "b-smaller-raw-higher-pp",
            [("b-smaller-raw-higher-pp", frame) for frame in range(500)],
            visual=True,
        )
        b_smaller_raw_higher_pp.update({
            "source_team_label": "B",
            "effective_team_label": "B",
            "current_resolution_status": "pending_high_priority",
            "priority": "high",
            "reason_codes": ["identity_conflict"],
        })

        policy = apply_coverage_policy(
            [a_larger_raw_lower_pp, b_smaller_raw_higher_pp],
            {
                "reliable_observations": 50_000,
                "per_team": {
                    "A": {"reliable_observations": 40_000, "confirmed_named_observations": 0},
                    "B": {"reliable_observations": 10_000, "confirmed_named_observations": 0},
                },
            },
            pair_index,
            match,
        )

        self.assertEqual(
            [row["candidate_subject_id"] for row in policy["next_cases"]],
            ["b-smaller-raw-higher-pp", "a-larger-raw-lower-pp"],
        )
        self.assertEqual(
            [row["operator_impact_pp"] for row in policy["next_cases"]],
            [5.0, 2.0],
        )
        self.assertEqual(
            [row["detected_observation_count"] for row in policy["next_cases"]],
            [500, 800],
        )

    def test_team_u_does_not_create_a_fake_team_attribution_card(self) -> None:
        pair_index = {
            ("unknown-team", frame): {
                "team_label": "U",
                "identity_status": "unresolved",
                "canonical_player_id": None,
            }
            for frame in range(5)
        }
        team_unknown = _unit(
            "unknown-team",
            [("unknown-team", frame) for frame in range(5)],
            visual=True,
        )
        team_unknown.update({
            "source_team_label": "U",
            "effective_team_label": "U",
            "current_resolution_status": "pending_high_priority",
            "priority": "high",
            "reason_codes": ["team_attribution_uncertain"],
        })

        policy = apply_coverage_policy(
            [team_unknown],
            {
                "reliable_observations": 100,
                "per_team": {"U": {"reliable_observations": 5, "confirmed_named_observations": 0}},
            },
            pair_index,
            _scoped_match(),
        )

        self.assertEqual(policy["next_cases"], [])
        self.assertFalse(policy["readiness"]["allows_finalize"])
        self.assertEqual(
            policy["readiness"]["team_attribution_residual"]["status"],
            "materialization_required",
        )

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


def _safe_team_attribution_evidence(
    pairs: list[tuple[str, int]],
    *,
    source_digest: str,
) -> dict:
    return {
        "kind": "team_attribution",
        "status": "ready_for_team_attribution",
        "source_ownership_digest": source_digest,
        "anchor_crops": _exact_anchor_crops(pairs),
    }


def _safe_ordinary_operator_evidence(pairs: list[tuple[str, int]]) -> dict:
    return {
        "status": "ready_for_visual_audit",
        "anchor_crops": _exact_anchor_crops(pairs),
    }


def _exact_anchor_crops(pairs: list[tuple[str, int]]) -> list[dict]:
    return [
        {
            "tracklet_id": tracklet_id,
            "frame": frame,
            "artifact": f"anchor_crops/{tracklet_id}/{frame}.jpg",
            "selection_eligible": True,
        }
        for tracklet_id, frame in pairs[:3]
    ]


def _continuity_members(
    *,
    team: str,
    frames_per_member: int = 60,
) -> list[dict]:
    members: list[dict] = []
    frame = 4_000
    for index in range(4):
        end = frame + frames_per_member
        members.append(
            _continuity_unit(
                f"continuity-subject-{index + 1}",
                f"continuity-tracklet-{index + 1}",
                range(frame, end),
                stable_slot_id=f"{team}12",
                team=team,
            )
        )
        frame = end
    return members


def _continuity_unit(
    subject_id: str,
    tracklet_id: str,
    frames: range,
    *,
    stable_slot_id: str,
    team: str,
) -> dict:
    pairs = [(tracklet_id, frame) for frame in frames]
    sample_frames = [pairs[0][1], pairs[-1][1]]
    return {
        **_unit(subject_id, pairs, visual=True),
        "tracklet_ids": [tracklet_id],
        "tracklet_count": 1,
        "effective_team_label": team,
        "source_team_label": team,
        "stable_slot_id": stable_slot_id,
        "canonical_player_id": None,
        "operator_actionable": True,
        "correction_scope": "whole_subject",
        "visual_evidence": {
            "anchor_crops": [
                {
                    "anchor_crop_id": f"{subject_id}-{frame}",
                    "artifact": f"anchor_crops/{subject_id}-{frame}.jpg",
                    "frame": frame,
                    "tracklet_id": tracklet_id,
                    "bbox_xyxy": [10, 10, 20, 40],
                }
                for frame in sample_frames
            ]
        },
    }


def _material_case(subject_id: str, frames: range) -> dict:
    unit = _continuity_unit(
        subject_id,
        subject_id,
        frames,
        stable_slot_id="A12",
        team="A",
    )
    return {
        **unit,
        "scope_kind": "material_continuity",
        "correction_scope": "material_continuity",
        "material_continuity_required": True,
        "continuity_group_id": f"continuity:A12:{frames.start}-{frames.stop - 1}",
        "current_resolution_status": "pending_material_continuity_review",
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
