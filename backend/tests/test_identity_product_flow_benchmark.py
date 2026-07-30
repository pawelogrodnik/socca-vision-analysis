from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from app.services.identity_initial_audit import (
    AUDIT_DIRECTORY,
    SELECTION_FILENAME,
    build_initial_identity_audit_document,
)
from app.services.identity_initial_audit_store import (
    InitialIdentityAuditStaleError,
    OperatorDecisionBudgetExceededError,
    save_initial_identity_audit_seeds,
)
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_product_flow_benchmark import (
    ProductFlowBenchmarkError,
    _frozen_h1_timeline_observation,
    _source_inventory_mutations,
    build_product_flow_benchmark_report,
    finish_product_flow_h1,
    finish_product_flow_h2,
    prepare_product_flow_benchmark,
)
from app.services.identity_product_flow_state import (
    ProductFlowStateError,
    benchmark_context_for_workspace,
    load_product_flow_session,
    transition_product_flow_session,
    write_json_atomic,
)
from app.services.identity_second_half_reanchor import (
    REANCHOR_DIRECTORY,
    SELECTION_FILENAME as REANCHOR_SELECTION_FILENAME,
    build_second_half_identity_reanchor_document,
)
from app.services.identity_second_half_reanchor_store import (
    save_second_half_identity_reanchor_seeds,
)


class IdentityProductFlowBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.matches = self.root / "matches"
        self.benchmarks = self.root / "benchmarks"
        self.matches.mkdir()
        self.benchmarks.mkdir()
        _create_source_match(
            self.matches / "h1-source",
            match_id="h1-source",
            title="Corgi 1 polowa",
            video_filename="1st_half.mp4",
            include_h2_artifacts=False,
        )
        _create_source_match(
            self.matches / "h2-source",
            match_id="h2-source",
            title="Ostatnie minuty drugiej polowy",
            video_filename="2nd_half.mp4",
            include_h2_artifacts=True,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_frozen_h1_adapter_preserves_crop_quality_evidence(self) -> None:
        observation = _frozen_h1_timeline_observation(
            {
                "frame": 42,
                "time_sec": 1.4,
                "status": "detected",
                "tracklet_id": "tracklet-42",
                "bbox_xyxy": [10, 20, 40, 100],
                "confidence": 0.91,
                "visual_trusted": True,
                "pitch_m": [20.0, 10.0],
            },
            slot={
                "mean_detection_confidence": 0.8,
                "team_confidence": 0.97,
            },
        )

        self.assertEqual(observation["confidence"], 0.91)
        self.assertTrue(observation["appearance_reliable"])
        self.assertTrue(observation["footpoint_reliable"])
        self.assertEqual(observation["play_area_status"], "inside_play")
        self.assertEqual(observation["quality_class"], "trusted")

    # 1
    def test_creates_isolated_h1_workspace(self) -> None:
        session = self._prepare()
        workspace = self._benchmark_root() / "h1_workspace"
        self.assertEqual(session["state"], "H1_READY")
        self.assertTrue(workspace.exists())
        self.assertNotEqual(
            (workspace / "tracklets.json").resolve(),
            (self.matches / "h1-source" / "tracklets.json").resolve(),
        )
        context = benchmark_context_for_workspace(
            self.matches / "benchmark-flow-test-h1"
        )
        self.assertIsNotNone(context)
        self.assertEqual(context["state"], "H1_READY")
        self.assertEqual(context["domain"], "H1")

    # 2
    def test_h2_does_not_exist_before_h1_finish(self) -> None:
        session = self._prepare()
        self.assertIsNone(session["workspaces"]["h2"])
        self.assertFalse((self._benchmark_root() / "h2_workspace").exists())
        self.assertFalse(
            (self.matches / "benchmark-flow-test-h2").exists()
        )

    # 3
    def test_all_legal_state_transitions(self) -> None:
        root = self._state_root()
        for state in (
            "H1_READY",
            "H1_FINISHED",
            "H1_REBUILT",
            "H2_READY",
            "H2_FINISHED",
            "REPORT_READY",
        ):
            transition_product_flow_session(
                root,
                state,
                action=f"to_{state.lower()}",
            )
        self.assertEqual(load_product_flow_session(root)["state"], "REPORT_READY")

    # 4
    def test_illegal_state_transition_is_rejected(self) -> None:
        root = self._state_root()
        with self.assertRaises(ProductFlowStateError):
            transition_product_flow_session(
                root,
                "H2_READY",
                action="skip_h1",
            )

    # 5
    def test_same_state_retry_is_idempotent(self) -> None:
        root = self._state_root()
        first = transition_product_flow_session(
            root,
            "H1_READY",
            action="prepare",
        )
        second = transition_product_flow_session(
            root,
            "H1_READY",
            action="retry",
        )
        self.assertEqual(first, second)
        self.assertEqual(len(second["audit_log"]), 1)

    # 6
    def test_h1_finish_runs_downstream_rebuild(self) -> None:
        _session, calls = self._finish_h1()
        self.assertTrue(calls["candidate"].called)
        self.assertTrue(calls["review"].called)

    # 7
    def test_h1_finish_requires_real_reduction_report(self) -> None:
        self._prepare()
        with patch(
            "app.services.identity_product_flow_benchmark."
            "rebuild_identity_seeded_candidate_assignments",
            return_value=_seeded_result(),
        ), patch(
            "app.services.identity_product_flow_benchmark."
            "rebuild_identity_seeded_subject_review",
            return_value={"status": "fresh"},
        ):
            with self.assertRaisesRegex(
                ProductFlowBenchmarkError,
                "H1_REDUCTION_REPORT_MISSING",
            ):
                finish_product_flow_h1(
                    root=self._benchmark_root(),
                    matches_root=self.matches,
                )
        self.assertEqual(
            load_product_flow_session(self._benchmark_root())["state"],
            "FAILED",
        )

    # 8
    def test_safely_resolved_players_come_from_seeded_assignments(self) -> None:
        self._finish_h1()
        result = _load(
            self._benchmark_root()
            / "h1_workspace"
            / "benchmark_h1_rebuild_result.json"
        )
        self.assertEqual(
            result["safely_resolved_players"][0]["player_id"],
            "player-a",
        )

    # 9
    def test_h1_safe_players_are_passed_to_h2(self) -> None:
        _session, calls = self._finish_h1()
        safe = calls["h2_prepare"].call_args.kwargs[
            "safely_resolved_players"
        ]
        self.assertEqual([row["player_id"] for row in safe], ["player-a"])

    # 10
    def test_h2_reid_is_advisory_top3_only(self) -> None:
        _session, calls = self._finish_h1()
        advisory = calls["h2_prepare"].call_args.kwargs[
            "advisory_suggestions"
        ]
        self.assertEqual(len(advisory[0]["suggestions"]), 3)
        self.assertTrue(advisory[0]["advisory_only"])

    # 11
    def test_h1_budget_accepts_twelve_active_decisions(self) -> None:
        workspace, match, keys = self._budget_workspace("H1", 13)
        result = save_initial_identity_audit_seeds(
            workspace,
            match,
            _updates(keys[:12]),
        )
        self.assertEqual(result["operator_budget"]["active_decisions"], 12)
        self.assertTrue(result["operator_budget"]["reached"])

    # 12
    def test_h1_budget_rejects_thirteenth_decision(self) -> None:
        workspace, match, keys = self._budget_workspace("H1", 13)
        save_initial_identity_audit_seeds(
            workspace,
            match,
            _updates(keys[:12]),
        )
        with self.assertRaises(OperatorDecisionBudgetExceededError):
            save_initial_identity_audit_seeds(
                workspace,
                match,
                _updates(keys[12:13], start=12),
            )
        stored = _load(workspace / "identity_operator_seeds.json")
        self.assertEqual(len(stored["decisions"]), 12)

    # 13
    def test_h2_budget_accepts_five_confirmations(self) -> None:
        workspace, match, keys = self._budget_workspace("H2", 6)
        result = save_second_half_identity_reanchor_seeds(
            workspace,
            match,
            _updates(keys[:5]),
        )
        self.assertEqual(result["operator_budget"]["active_decisions"], 5)

    # 14
    def test_h2_budget_rejects_sixth_confirmation(self) -> None:
        workspace, match, keys = self._budget_workspace("H2", 6)
        save_second_half_identity_reanchor_seeds(
            workspace,
            match,
            _updates(keys[:5]),
        )
        with self.assertRaises(OperatorDecisionBudgetExceededError):
            save_second_half_identity_reanchor_seeds(
                workspace,
                match,
                _updates(keys[5:6], start=5),
            )

    # 15
    def test_replace_and_delete_do_not_double_count_budget(self) -> None:
        workspace, match, keys = self._budget_workspace("H1", 2)
        save_initial_identity_audit_seeds(
            workspace,
            match,
            _updates(keys[:1]),
        )
        replaced = save_initial_identity_audit_seeds(
            workspace,
            match,
            [
                {
                    "update_id": "replace",
                    "observation_key": keys[0],
                    "action": "false_detection",
                }
            ],
        )
        cleared = save_initial_identity_audit_seeds(
            workspace,
            match,
            [
                {
                    "update_id": "clear",
                    "observation_key": keys[0],
                    "action": "clear",
                }
            ],
        )
        self.assertEqual(replaced["operator_budget"]["active_decisions"], 1)
        self.assertEqual(cleared["operator_budget"]["active_decisions"], 0)

    # 16
    def test_skip_does_not_consume_active_budget(self) -> None:
        workspace, match, keys = self._budget_workspace("H1", 1)
        result = save_initial_identity_audit_seeds(
            workspace,
            match,
            [
                {
                    "update_id": "skip",
                    "observation_key": keys[0],
                    "action": "skip",
                }
            ],
        )
        self.assertEqual(result["operator_budget"]["active_decisions"], 0)
        self.assertEqual(len(result["decisions"]), 1)

    # 17
    def test_operator_can_finish_h1_early_with_zero_decisions(self) -> None:
        session, _calls = self._finish_h1()
        self.assertEqual(session["state"], "H2_READY")
        metrics = session["audit_log"][1]["details"]
        self.assertEqual(metrics["operator_decisions"], 0)

    # 18
    def test_final_report_measures_zero_automatic_assignments(self) -> None:
        report = self._finish_full_flow()
        self.assertEqual(report["safety"]["automatic_assignments"], 0)

    # 19
    def test_final_report_measures_zero_production_apply(self) -> None:
        report = self._finish_full_flow()
        self.assertEqual(report["safety"]["production_apply_count"], 0)

    # 20
    def test_source_digests_remain_unchanged(self) -> None:
        self._finish_h1()
        session = load_product_flow_session(self._benchmark_root())
        self.assertEqual(_source_inventory_mutations(session), [])

    # 21
    def test_stale_seed_write_is_rejected(self) -> None:
        workspace, match, keys = self._budget_workspace("H1", 1)
        save_initial_identity_audit_seeds(
            workspace,
            match,
            _updates(keys),
        )
        selection_path = workspace / AUDIT_DIRECTORY / SELECTION_FILENAME
        changed = _load(selection_path)
        changed["selection_digest"] = "changed"
        _write(selection_path, changed)
        with self.assertRaises(InitialIdentityAuditStaleError):
            save_initial_identity_audit_seeds(
                workspace,
                match,
                [],
            )

    # 22
    def test_creation_rolls_back_temporary_workspace_on_failure(self) -> None:
        with patch(
            "app.services.identity_product_flow_benchmark._prepare_h1_audit",
            side_effect=RuntimeError("render failed"),
        ):
            with self.assertRaises(ProductFlowBenchmarkError):
                self._prepare()
        self.assertFalse(self._benchmark_root().exists())

    # 23
    def test_rollback_leaves_no_temp_directories_or_partial_aliases(self) -> None:
        with patch(
            "app.services.identity_product_flow_benchmark._prepare_h1_audit",
            side_effect=RuntimeError("render failed"),
        ):
            with self.assertRaises(ProductFlowBenchmarkError):
                self._prepare()
        self.assertEqual(
            list(self.benchmarks.glob(".flow-test.tmp-*")),
            [],
        )
        self.assertFalse(
            (self.matches / "benchmark-flow-test-h1").exists()
        )

    # 24
    def test_report_uses_operator_telemetry_and_reduction_artifact(self) -> None:
        report = self._finish_full_flow()
        self.assertEqual(report["h1"]["operator_decisions"], 0)
        self.assertEqual(report["h1"]["active_operator_seconds"], 0.0)
        self.assertEqual(report["h1"]["review_cards_before"], 4)
        self.assertEqual(report["h1"]["review_cards_after"], 3)

    # 25
    def test_report_is_deterministic_for_unchanged_artifacts(self) -> None:
        self._finish_full_flow()
        first = build_product_flow_benchmark_report(self._benchmark_root())
        second = build_product_flow_benchmark_report(self._benchmark_root())
        first.pop("generated_at")
        second.pop("generated_at")
        self.assertEqual(first, second)

    def test_h2_current_reduction_report_allows_report_ready(self) -> None:
        report = self._finish_full_flow()
        self.assertEqual(report["status"], "REPORT_READY")
        receipt = _load(
            self._benchmark_root()
            / "h2_workspace"
            / "benchmark_h2_rebuild_result.json"
        )
        self.assertTrue(receipt["reduction_report_digest"])

    def test_h2_missing_reduction_fails_without_success_report(self) -> None:
        self._finish_h1()
        before = load_product_flow_session(self._benchmark_root())
        with patch(
            "app.services.identity_product_flow_benchmark."
            "rebuild_identity_seeded_candidate_assignments",
            return_value=_h2_seeded_result(),
        ), patch(
            "app.services.identity_product_flow_benchmark."
            "rebuild_identity_seeded_subject_review",
            return_value={"status": "fresh"},
        ):
            with self.assertRaisesRegex(
                ProductFlowBenchmarkError,
                "H2_REDUCTION_REPORT_MISSING",
            ):
                finish_product_flow_h2(root=self._benchmark_root())
        failed = load_product_flow_session(self._benchmark_root())
        self.assertEqual(failed["state"], "FAILED")
        self.assertIn(
            "H2_REDUCTION_REPORT_MISSING",
            failed["audit_log"][-1]["details"]["reason"],
        )
        self.assertFalse(
            (self._benchmark_root() / "benchmark_report.json").exists()
        )
        self.assertEqual(_source_inventory_mutations(before), [])

    def test_h2_stale_reduction_report_is_rejected(self) -> None:
        self._finish_h1()

        def stale_review(path, _match, **_kwargs):
            stale_seeded = {**_h2_seeded_result(), "summary": {"stale": 1}}
            _write(
                path / "identity_seeded_review_reduction_report.json",
                _reduction_report(stale_seeded, before=3, after=3),
            )
            return {"status": "fresh"}

        with patch(
            "app.services.identity_product_flow_benchmark."
            "rebuild_identity_seeded_candidate_assignments",
            return_value=_h2_seeded_result(),
        ), patch(
            "app.services.identity_product_flow_benchmark."
            "rebuild_identity_seeded_subject_review",
            side_effect=stale_review,
        ):
            with self.assertRaisesRegex(
                ProductFlowBenchmarkError,
                "H2_REDUCTION_REPORT_INVALID",
            ):
                finish_product_flow_h2(root=self._benchmark_root())
        self.assertEqual(
            load_product_flow_session(self._benchmark_root())["state"],
            "FAILED",
        )

    def test_h2_finalization_can_retry_without_reopening_audit(self) -> None:
        self._finish_h1()
        seeded_result = _h2_seeded_result()

        def stale_review(path, _match, **_kwargs):
            stale_seeded = {
                **seeded_result,
                "summary": {"stale": 1},
            }
            _write(
                path / "identity_seeded_review_reduction_report.json",
                _reduction_report(stale_seeded, before=3, after=3),
            )
            return {"status": "fresh"}

        with patch(
            "app.services.identity_product_flow_benchmark."
            "rebuild_identity_seeded_candidate_assignments",
            return_value=seeded_result,
        ), patch(
            "app.services.identity_product_flow_benchmark."
            "rebuild_identity_seeded_subject_review",
            side_effect=stale_review,
        ):
            with self.assertRaisesRegex(
                ProductFlowBenchmarkError,
                "H2_REDUCTION_REPORT_INVALID",
            ):
                finish_product_flow_h2(root=self._benchmark_root())

        def fresh_review(path, _match, **_kwargs):
            _write(
                path / "identity_seeded_review_reduction_report.json",
                _reduction_report(seeded_result, before=3, after=3),
            )
            return {"status": "fresh"}

        with patch(
            "app.services.identity_product_flow_benchmark."
            "rebuild_identity_seeded_candidate_assignments",
            return_value=seeded_result,
        ), patch(
            "app.services.identity_product_flow_benchmark."
            "rebuild_identity_seeded_subject_review",
            side_effect=fresh_review,
        ):
            report = finish_product_flow_h2(root=self._benchmark_root())

        session = load_product_flow_session(self._benchmark_root())
        self.assertEqual(report["status"], "REPORT_READY")
        self.assertEqual(session["state"], "REPORT_READY")
        retry_event = next(
            row
            for row in session["audit_log"]
            if row.get("action") == "retry_finish_h2"
        )
        self.assertTrue(
            retry_event["details"]["operator_decisions_reused"]
        )

    def test_generated_reid_not_displayed_is_not_counted_displayed(self) -> None:
        report = self._finish_full_flow()
        self.assertEqual(report["reid"]["reid_subjects_available"], 2)
        self.assertEqual(report["reid"]["reid_suggestions_generated"], 6)
        self.assertEqual(report["reid"]["reid_suggestions_displayed"], 3)
        self.assertEqual(report["reid"]["reid_observations_displayed"], 1)

    def test_h1_safe_lineage_decision_is_not_reid_outcome(self) -> None:
        report = self._finish_flow_with_reid_decision(
            source="h1_safe_lineage",
            player_id="player-a",
        )
        self.assertEqual(report["reid"]["reid_suggestions_accepted"], 0)
        self.assertEqual(report["reid"]["reid_suggestions_rejected"], 0)
        self.assertEqual(
            report["h1_safe_lineage"]["suggestions_accepted"],
            1,
        )
        self.assertEqual(
            report["h1_safe_lineage"][
                "top1_accuracy_on_named_decisions"
            ],
            1.0,
        )

    def test_displayed_reid_top1_is_accepted_with_rank(self) -> None:
        report = self._finish_flow_with_reid_decision(
            source="cross_analysis_reid_top3_advisory",
            player_id="player-1",
            rank=1,
        )
        self.assertEqual(report["reid"]["reid_suggestions_accepted"], 1)
        self.assertEqual(report["reid"]["accepted_ranks"], [1])

    def test_displayed_reid_rank2_is_accepted_with_rank(self) -> None:
        report = self._finish_flow_with_reid_decision(
            source="cross_analysis_reid_top3_advisory",
            player_id="player-2",
            rank=2,
        )
        self.assertEqual(report["reid"]["reid_suggestions_accepted"], 1)
        self.assertEqual(report["reid"]["accepted_ranks"], [2])

    def test_different_player_is_reid_rejection(self) -> None:
        report = self._finish_flow_with_reid_decision(
            source=None,
            player_id="player-other",
        )
        self.assertEqual(report["reid"]["reid_suggestions_rejected"], 1)

    def test_reid_observation_skip_is_counted(self) -> None:
        report = self._finish_flow_with_reid_decision(
            source=None,
            action="skip",
        )
        self.assertEqual(report["reid"]["reid_suggestions_skipped"], 1)

    def test_retried_reid_decision_is_counted_once(self) -> None:
        report = self._finish_flow_with_reid_decision(
            source="cross_analysis_reid_top3_advisory",
            player_id="player-1",
            rank=1,
            retry=True,
        )
        self.assertEqual(report["reid"]["reid_suggestions_accepted"], 1)

    def _prepare(self) -> dict:
        return prepare_product_flow_benchmark(
            matches_root=self.matches,
            benchmark_root=self.benchmarks,
            source_match_id="h1-source",
            target_match_id="h2-source",
            benchmark_id="flow-test",
        )

    def _benchmark_root(self) -> Path:
        return self.benchmarks / "flow-test"

    def _state_root(self) -> Path:
        root = self.root / "state"
        root.mkdir()
        _write(
            root / "benchmark_session.json",
            {
                "benchmark_id": "state",
                "state": "CREATING",
                "status": "CREATING",
                "audit_log": [],
            },
        )
        return root

    def _finish_h1(self):
        self._prepare()
        seeded_result = _seeded_result()

        def review_side_effect(path, _match, **_kwargs):
            _write(
                path / "identity_seeded_review_reduction_report.json",
                _reduction_report(seeded_result, before=4, after=3),
            )
            return {
                "status": "fresh",
                "approved_appearance_gallery_summary": {"players": 1},
                "approved_appearance_reid_summary": {
                    "unresolved_subjects_ranked": 1
                },
            }

        def h2_prepare_side_effect(path, _match, **_kwargs):
            selection = _selection(1)
            selection["second_half"] = {
                "start_time_sec": 0.0,
                "start_frame": 0,
                "safely_resolved_players_before_reanchor": (
                    _kwargs.get("safely_resolved_players") or []
                ),
            }
            selection["reid_advisory_suggestions"] = _advisory()[
                "suggestions"
            ]
            _write(
                path / "identity_cross_analysis_reid_advisory.json",
                _advisory(),
            )
            target = path / REANCHOR_DIRECTORY / REANCHOR_SELECTION_FILENAME
            target.parent.mkdir(parents=True, exist_ok=True)
            _write(target, selection)
            return {"status": "ready", "summary": {"selected_frames": 1}}

        candidate = patch(
            "app.services.identity_product_flow_benchmark."
            "rebuild_identity_seeded_candidate_assignments",
            return_value=seeded_result,
        )
        review = patch(
            "app.services.identity_product_flow_benchmark."
            "rebuild_identity_seeded_subject_review",
            side_effect=review_side_effect,
        )
        advisory = patch(
            "app.services.identity_product_flow_benchmark."
            "_build_h2_cross_analysis_advisory",
            return_value=_advisory(),
        )
        h2_prepare = patch(
            "app.services.identity_product_flow_benchmark."
            "_prepare_h2_reanchor",
            side_effect=h2_prepare_side_effect,
        )
        mocks = {
            "candidate": candidate.start(),
            "review": review.start(),
            "advisory": advisory.start(),
            "h2_prepare": h2_prepare.start(),
        }
        self.addCleanup(candidate.stop)
        self.addCleanup(review.stop)
        self.addCleanup(advisory.stop)
        self.addCleanup(h2_prepare.stop)
        session = finish_product_flow_h1(
            root=self._benchmark_root(),
            matches_root=self.matches,
        )
        candidate.stop()
        review.stop()
        advisory.stop()
        h2_prepare.stop()
        return session, mocks

    def _finish_full_flow(self) -> dict:
        self._finish_h1()
        seeded_result = _h2_seeded_result()

        def review_side_effect(path, _match, **_kwargs):
            _write(
                path / "identity_seeded_review_reduction_report.json",
                _reduction_report(seeded_result, before=3, after=3),
            )
            return {"status": "fresh"}

        with patch(
            "app.services.identity_product_flow_benchmark."
            "rebuild_identity_seeded_candidate_assignments",
            return_value=seeded_result,
        ), patch(
            "app.services.identity_product_flow_benchmark."
            "rebuild_identity_seeded_subject_review",
            side_effect=review_side_effect,
        ):
            return finish_product_flow_h2(root=self._benchmark_root())

    def _finish_flow_with_reid_decision(
        self,
        *,
        source: str | None,
        player_id: str | None = None,
        rank: int | None = None,
        action: str = "assign_roster_player",
        retry: bool = False,
    ) -> dict:
        self._finish_h1()
        workspace = self._benchmark_root() / "h2_workspace"
        match = _load(workspace / "match.json")
        selection = _load(
            workspace / REANCHOR_DIRECTORY / REANCHOR_SELECTION_FILENAME
        )
        selection["second_half"][
            "safely_resolved_players_before_reanchor"
        ] = [
            {
                "player_id": "player-a",
                "player_name": "Player A",
                "team_label": "A",
                "candidate_subject_id": "h1-safe-subject",
                "tracklet_ids": ["tracklet-0"],
            }
        ]
        _write(
            workspace / REANCHOR_DIRECTORY / REANCHOR_SELECTION_FILENAME,
            selection,
        )
        document = build_second_half_identity_reanchor_document(
            selection,
            match,
        )
        observation = document["frames"][0]["observations"][0]
        update = {
            "update_id": "reid-decision",
            "observation_key": observation["observation_key"],
            "action": action,
        }
        if player_id is not None:
            update["player_id"] = player_id
        if source is not None:
            update["suggestion_context"] = {
                "suggestion_source": source,
                "advisory_only": (
                    source == "cross_analysis_reid_top3_advisory"
                ),
                "rank": rank,
                "candidate_subject_id": (
                    "h2-subject"
                    if source == "cross_analysis_reid_top3_advisory"
                    else "h1-safe-subject"
                ),
                "observation_key": observation["observation_key"],
                "player_id": player_id,
            }
        updates = [update, dict(update)] if retry else [update]
        save_second_half_identity_reanchor_seeds(
            workspace,
            match,
            updates,
        )
        return self._finish_h2_with_current_reduction()

    def _finish_h2_with_current_reduction(self) -> dict:
        seeded_result = _h2_seeded_result()

        def review_side_effect(path, _match, **_kwargs):
            _write(
                path / "identity_seeded_review_reduction_report.json",
                _reduction_report(seeded_result, before=3, after=3),
            )
            return {"status": "fresh"}

        with patch(
            "app.services.identity_product_flow_benchmark."
            "rebuild_identity_seeded_candidate_assignments",
            return_value=seeded_result,
        ), patch(
            "app.services.identity_product_flow_benchmark."
            "rebuild_identity_seeded_subject_review",
            side_effect=review_side_effect,
        ):
            return finish_product_flow_h2(root=self._benchmark_root())

    def _budget_workspace(
        self,
        domain: str,
        observations: int,
    ) -> tuple[Path, dict, list[str]]:
        root = self.root / f"budget-{domain.lower()}"
        workspace = root / f"{domain.lower()}_workspace"
        workspace.mkdir(parents=True)
        state = f"{domain}_READY"
        _write(
            root / "benchmark_session.json",
            {
                "benchmark_id": f"budget-{domain.lower()}",
                "state": state,
                "status": state,
                "operator_budget": {
                    "h1_maximum_actions": 12,
                    "h2_maximum_confirmations": 5,
                },
                "audit_log": [],
            },
        )
        match = _match_document(
            match_id=f"benchmark-budget-{domain.lower()}",
            title=domain,
            video_filename=f"{domain}.mp4",
        )
        match["benchmark_session"] = {
            "id": f"budget-{domain.lower()}",
            "domain": domain,
            "shadow_only": True,
            "reanchor_only": domain == "H2",
        }
        _write(workspace / "match.json", match)
        selection = _selection(observations)
        selection_path = (
            workspace / AUDIT_DIRECTORY / SELECTION_FILENAME
            if domain == "H1"
            else workspace
            / REANCHOR_DIRECTORY
            / REANCHOR_SELECTION_FILENAME
        )
        selection_path.parent.mkdir(parents=True, exist_ok=True)
        _write(selection_path, selection)
        public = build_initial_identity_audit_document(selection, match)
        keys = [
            row["observation_key"]
            for frame in public["frames"]
            for row in frame["observations"]
        ]
        return workspace, match, keys


def _create_source_match(
    path: Path,
    *,
    match_id: str,
    title: str,
    video_filename: str,
    include_h2_artifacts: bool,
) -> None:
    path.mkdir()
    video = path / "video.mp4"
    writer = cv2.VideoWriter(
        str(video),
        cv2.VideoWriter_fourcc(*"mp4v"),
        5.0,
        (32, 24),
    )
    writer.write(np.zeros((24, 32, 3), dtype=np.uint8))
    writer.release()
    match = _match_document(
        match_id=match_id,
        title=title,
        video_filename=video_filename,
    )
    match["video"]["path"] = str(video.resolve())
    _write(path / "match.json", match)
    _write(
        path / "analysis_report.json",
        {
            "run_id": f"run-{match_id}",
            "video": match["video"],
        },
    )
    _write(path / "global_identity.json", {"slots": []})
    _write(
        path / "tracklets.json",
        {"tracklets": [], "rejected_tracklets": []},
    )
    if include_h2_artifacts:
        _write(
            path / "identity_candidate_shadow.json",
            {"subjects": [], "algorithm": {"name": "candidate"}},
        )
        _write(
            path / "identity_offline_shadow_timeline.json",
            {"subjects": [], "algorithm": {"name": "timeline"}},
        )


def _match_document(
    *,
    match_id: str,
    title: str,
    video_filename: str,
) -> dict:
    return {
        "id": match_id,
        "title": title,
        "match_date": "2026-07-13",
        "video_filename": video_filename,
        "video": {
            "fps": 5.0,
            "frame_count": 1,
            "duration_sec": 0.2,
            "width": 32,
            "height": 24,
        },
        "teams": [
            {
                "id": "team-a",
                "name": "Corgi",
                "players": [
                    {
                        "id": "player-a",
                        "name": "Player A",
                        "number": "1",
                        "role": "player",
                    },
                    *[
                        {
                            "id": f"player-{index}",
                            "name": f"Player {index}",
                            "number": str(index + 1),
                            "role": "player",
                        }
                        for index in range(1, 4)
                    ],
                    {
                        "id": "player-other",
                        "name": "Other Player",
                        "number": "9",
                        "role": "player",
                    },
                ],
            },
            {"id": "team-b", "name": "Verisk", "players": []},
        ],
    }


def _selection(count: int) -> dict:
    detections = [
        {
            "stable_subject_id": f"subject-{index}",
            "tracklet_id": f"tracklet-{index}",
            "team_label": "A",
            "role": "field_player",
            "source": "detected",
            "bbox_xyxy": [index * 2, 0, index * 2 + 1, 3],
        }
        for index in range(count)
    ]
    selected = [
        {
            "frame": 0,
            "time_sec": 0.0,
            "capture_domain": "test",
            "visible_detections": detections,
            "full_frame_artifact": "frames/frame-000000.jpg",
            "thumbnail_artifact": "frames/frame-000000-thumb.jpg",
        }
    ]
    return {
        "schema_version": "0.1.0",
        "selection_digest": "selection-test",
        "video": {
            "fps": 5.0,
            "frame_count": 1,
            "duration_sec": 0.2,
            "width": 32,
            "height": 24,
        },
        "source": {"analysis_run_id": "run-test"},
        "selected_frames": selected,
        "summary": {"selected_frames": 1},
    }


def _updates(keys: list[str], *, start: int = 0) -> list[dict]:
    return [
        {
            "update_id": f"update-{start + index}",
            "observation_key": key,
            "action": "team_a_unknown",
        }
        for index, key in enumerate(keys)
    ]


def _seeded_result() -> dict:
    return {
        "accepted_assignments": [
            {
                "candidate_subject_id": "subject-a",
                "team_label": "A",
                "tracklet_ids": ["tracklet-a"],
                "assigned_player": {
                    "player_id": "player-a",
                    "player_name": "Player A",
                },
            }
        ],
        "summary": {
            "operator_decisions": 1,
            "candidate_subjects": 4,
            "subjects_resolved_after_seeding": 1,
            "unresolved_subjects": 3,
            "conflicts_created": 0,
        },
        "safety": {
            "automatic_assignments": 0,
            "cross_team_links": 0,
        },
    }


def _h2_seeded_result() -> dict:
    return {
        **_seeded_result(),
        "accepted_assignments": [],
        "summary": {
            "operator_decisions": 0,
            "candidate_subjects": 3,
            "subjects_resolved_after_seeding": 0,
            "unresolved_subjects": 3,
            "conflicts_created": 0,
        },
    }


def _reduction_report(
    seeded: dict,
    *,
    before: int,
    after: int,
) -> dict:
    return {
        "schema_version": "0.1.0",
        "mode": "seed_aware_review_reduction_shadow",
        "status": "fresh",
        "source": {
            "seeded_assignments_digest": canonical_digest(seeded),
        },
        "metrics": {
            "review_cards_before_seeding": before,
            "review_cards_after_seeding": after,
        },
        "safety": {
            "mutates_production_identity": False,
            "writes_shadow_review_state_only": True,
        },
    }


def _advisory() -> dict:
    return {
        "summary": {"ranked_subjects": 2, "suggestions_shown": 6},
        "safety": {"automatic_merges": 0, "advisory_only": True},
        "suggestions": [
            {
                "candidate_subject_id": "h2-subject",
                "team_label": "A",
                "tracklet_ids": ["tracklet-0"],
                "advisory_only": True,
                "suggestions": [
                    {
                        "player_id": f"player-{index}",
                        "player_name": f"Player {index}",
                        "rank": index,
                        "suggestion_source": (
                            "cross_analysis_reid_top3_advisory"
                        ),
                        "advisory_only": True,
                        "candidate_subject_id": "h2-subject",
                        "observation_key": None,
                    }
                    for index in range(1, 4)
                ],
            },
            {
                "candidate_subject_id": "not-displayed-subject",
                "team_label": "A",
                "tracklet_ids": ["not-visible-tracklet"],
                "advisory_only": True,
                "suggestions": [
                    {
                        "player_id": f"player-{index}",
                        "player_name": f"Player {index}",
                        "rank": index,
                        "suggestion_source": (
                            "cross_analysis_reid_top3_advisory"
                        ),
                        "advisory_only": True,
                        "candidate_subject_id": "not-displayed-subject",
                        "observation_key": None,
                    }
                    for index in range(1, 4)
                ],
            },
        ],
    }


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
