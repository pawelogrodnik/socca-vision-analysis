from __future__ import annotations

"""Correctness contracts for authoritative progress across snapshot replacement.

These regressions protect the finalize transaction against stale request-scoped
memoization: the final authoritative review progress must be projected from the
newly written Reviewed Identity snapshot, never from the pre-finalize snapshot,
and the cheap finalize preflight must preserve every real durable workflow gate.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.identity_canonical_io import (
    invalidate_cached_json,
    review_build_context,
    scoped_memo_get,
    scoped_memo_invalidate,
    scoped_memo_put,
)
from app.services.identity_reviewed_hot_state import (
    load_existing_fresh_hot_state,
    rebuild_review_hot_state,
)
from app.services.identity_reviewed_progress import (
    build_reviewed_identity_progress,
)
from app.services.identity_reviewed_snapshot import (
    finalize_reviewed_identity,
)
from app.services.review_workflow_orchestrator import finalize_review_for_qa
from app.services.review_workflow_state import (
    WorkflowActionError,
    build_cheap_finalize_preflight_state,
    get_review_workflow_state,
)
from app.services.review_workflow_store import (
    current_approval_fingerprint,
    save_video_qa_approval,
)


COMPLETE_AUDIT_EVIDENCE = {
    "prepared": True,
    "complete": True,
    "completed": 2,
    "total": 2,
    "remaining": 0,
}


class FinalizeStaleProgressRegressionTests(unittest.TestCase):
    """§10/§11/§12: finalize progress is projected from the NEW snapshot."""

    def test_finalize_progress_is_projected_from_new_snapshot_not_memo(self) -> None:
        with _workspace() as root:
            _fixture(root)
            match = _match()

            # Generation S1: authoritative snapshot without any operator decision.
            snapshot_s1 = finalize_reviewed_identity(root, match)
            stale_generation_progress = build_reviewed_identity_progress(
                root,
                match,
                include_internal_units=True,
            )
            self.assertEqual(
                stale_generation_progress["source_snapshot_digest"],
                snapshot_s1["semantic_digest"],
            )
            _write(
                root / "reviewed_identity_progress.json",
                _durable_form(stale_generation_progress),
            )

            # Durable correction decisions arrive AFTER S1 and change identity
            # coverage (both subjects become confirmed roster players).
            _write(
                root / "identity_roster_subject_review_decisions_shadow.json",
                {
                    "decisions": [
                        {"candidate_subject_id": "s1", "decision": "assign_roster_player", "player_id": "p1"},
                        {"candidate_subject_id": "s2", "decision": "assign_roster_player", "player_id": "p2"},
                    ]
                },
            )

            # One finalize transaction scope: snapshot S2 is written, then the
            # final progress is produced inside the SAME request scope.
            with review_build_context():
                snapshot_s2 = finalize_reviewed_identity(root, match)
                final_progress = build_reviewed_identity_progress(
                    root,
                    match,
                    include_internal_units=True,
                )

            self.assertNotEqual(
                snapshot_s2["semantic_digest"], snapshot_s1["semantic_digest"]
            )
            self.assertEqual(
                final_progress["source_snapshot_digest"],
                snapshot_s2["semantic_digest"],
            )
            self.assertEqual(
                final_progress["_projection_inputs"]["source_snapshot_digest"],
                snapshot_s2["semantic_digest"],
            )
            # Not just relabeled: the coverage itself must come from S2.
            self.assertEqual(
                final_progress["_projection_inputs"]["source_snapshot_file"],
                _fingerprint(root / "reviewed_identity_snapshot.json"),
            )

            # Independent cold rebuild in a BRAND NEW context must agree exactly.
            cold = build_reviewed_identity_progress(
                root,
                match,
                include_internal_units=True,
            )
            _assert_same_product_projection(self, final_progress, cold)

    def test_hot_state_receives_fresh_snapshot_projection_and_stays_warm(self) -> None:
        with _workspace() as root:
            _fixture(root)
            match = _match()
            finalize_reviewed_identity(root, match)
            _write(
                root / "identity_roster_subject_review_decisions_shadow.json",
                {
                    "decisions": [
                        {"candidate_subject_id": "s1", "decision": "assign_roster_player", "player_id": "p1"},
                        {"candidate_subject_id": "s2", "decision": "assign_roster_player", "player_id": "p2"},
                    ]
                },
            )
            with review_build_context():
                snapshot = finalize_reviewed_identity(root, match)
                progress = build_reviewed_identity_progress(
                    root,
                    match,
                    include_internal_units=True,
                )

            state = rebuild_review_hot_state(root, match, prebuilt_progress=progress)
            self.assertEqual(
                state["projection_inputs"]["source_snapshot_digest"],
                snapshot["semantic_digest"],
            )
            expected_public = json.loads(json.dumps(_public_form(progress)))
            self.assertEqual(state["progress"], expected_public)

            # Restart simulation: a fresh reader must warm-hit the consistent
            # state instead of silently rebuilding or serving stale coverage.
            warm = load_existing_fresh_hot_state(root, match)
            self.assertIsNotNone(warm)
            self.assertEqual(
                warm["projection_inputs"]["source_snapshot_digest"],
                snapshot["semantic_digest"],
            )


class ScopedMemoDependencyTests(unittest.TestCase):
    """§28/§29: derived memos never survive a semantic dependency change."""

    def test_scoped_memo_invalidate_drops_derived_namespace(self) -> None:
        with review_build_context():
            scoped_memo_put("derived::alpha", {"value": 1})
            self.assertEqual(scoped_memo_get("derived::alpha"), {"value": 1})
            invalidated = scoped_memo_invalidate("derived::")
            self.assertEqual(invalidated, 1)
            self.assertIsNone(scoped_memo_get("derived::alpha"))

    def test_progress_memo_does_not_survive_snapshot_replacement_in_scope(self) -> None:
        with _workspace() as root:
            _fixture(root)
            match = _match()
            finalize_reviewed_identity(root, match)
            snapshot_path = root / "reviewed_identity_snapshot.json"
            original = _load(snapshot_path)

            with review_build_context():
                before = build_reviewed_identity_progress(
                    root,
                    match,
                    include_internal_units=True,
                )
                # Authoritative flows replace the snapshot inside an active
                # scope; the derived progress memo must be generation-aware.
                mutated = {
                    **original,
                    "summary": {**(original.get("summary") or {}), "confirmed": 99},
                    "semantic_digest": "mutated-generation",
                }
                _write(snapshot_path, mutated)
                # finalize_reviewed_identity performs this invalidation after
                # its authoritative write; replicate it exactly.
                invalidate_cached_json(snapshot_path)
                after = build_reviewed_identity_progress(
                    root,
                    match,
                    include_internal_units=True,
                )

            self.assertIsNot(before, after)
            self.assertEqual(after["source_snapshot_digest"], "mutated-generation")
            self.assertEqual(
                after["_projection_inputs"]["source_snapshot_digest"],
                "mutated-generation",
            )


class CheapPreflightCanonicalStalenessTests(unittest.TestCase):
    """§20/§22: cheap preflight may defer canonical freshness; the
    authoritative recompute remains the final safety gate."""

    def test_stale_canonical_source_after_ready_preflight_is_rejected_by_finalize(
        self,
    ) -> None:
        with _workspace() as root:
            _fixture(root)
            # One authoritative match document shared by disk and builders.
            match = {**_match(), "status": "analyzed"}
            _write(root / "analysis_report.json", {"status": "completed"})
            _write(root / "match.json", match)
            # Everything resolved: durable compact state says ready.
            _write(
                root / "identity_roster_subject_review_decisions_shadow.json",
                {
                    "decisions": [
                        {"candidate_subject_id": "s1", "decision": "assign_roster_player", "player_id": "p1"},
                        {"candidate_subject_id": "s2", "decision": "assign_roster_player", "player_id": "p2"},
                    ]
                },
            )
            snapshot = finalize_reviewed_identity(root, match)
            progress = build_reviewed_identity_progress(root, match)
            self.assertEqual(progress["summary"]["important_decisions_remaining"], 0)
            _write(root / "reviewed_identity_progress.json", progress)

            audit_patch = patch(
                "app.services.review_workflow_state.load_initial_audit_completion_evidence",
                return_value=dict(COMPLETE_AUDIT_EVIDENCE),
            )
            with audit_patch:
                preflight = build_cheap_finalize_preflight_state(root, match)
                # Intentional relaxation: compact durable state is ready, so the
                # cheap probe allows the authoritative attempt even though it
                # cannot prove canonical freshness.
                self.assertIn("finalize_identity", preflight["allowed_actions"])

            # Canonical/decision change AFTER the durable ready state: a real
            # unresolved mixed-player case creates a real authoritative blocker.
            _write(
                root / "reviewed_identity_mixed_players.json",
                {
                    "schema_version": "1.0.0",
                    "mode": "reviewed_identity_mixed_players",
                    "cases": [
                        {
                            "case_id": "case-s2",
                            "candidate_subject_id": "s2",
                            "original_issue": "mixed_players",
                            "resolution_status": "unresolved",
                        }
                    ],
                },
            )

            # Both the cheap preflight and the authoritative transaction read
            # audit completion through their own module imports; the fixture
            # stubs the evidence loader itself, not the recomputation.
            with patch(
                "app.services.review_workflow_state.load_initial_audit_completion_evidence",
                return_value=dict(COMPLETE_AUDIT_EVIDENCE),
            ), patch(
                "app.services.review_workflow_orchestrator.load_initial_audit_completion_evidence",
                return_value=dict(COMPLETE_AUDIT_EVIDENCE),
            ):
                with self.assertRaises(WorkflowActionError) as raised:
                    finalize_review_for_qa(root, match)

            self.assertEqual(raised.exception.code, "identity_issues_remaining")
            # No render may be queued when the recompute found blockers.
            self.assertFalse((root / "reviewed_video_job.json").exists())
            # Fresh progress based on the NEW authoritative snapshot is persisted.
            persisted = _load(root / "reviewed_identity_progress.json")
            new_snapshot = _load(root / "reviewed_identity_report.json")
            self.assertNotEqual(new_snapshot["snapshot_digest"], snapshot["semantic_digest"])
            self.assertEqual(
                persisted["source_snapshot_digest"], new_snapshot["snapshot_digest"]
            )
            self.assertGreaterEqual(persisted["mixed_players"]["summary"]["unresolved"], 1)
            # The recompute succeeded; no failure marker may remain.
            self.assertFalse((root / "review_workflow_recompute_failure.json").exists())

            # The next review-progress/workflow read surfaces the case.
            with patch(
                "app.services.review_workflow_state.load_initial_audit_completion_evidence",
                return_value=dict(COMPLETE_AUDIT_EVIDENCE),
            ):
                state = get_review_workflow_state(root, match)
            self.assertEqual(state["phase"], "mixed_players")
            self.assertGreaterEqual(state["issues"]["mixed_blocking"], 1)
            self.assertNotIn("finalize_identity", state["allowed_actions"])


class CheapPreflightActionEquivalenceTests(unittest.TestCase):
    """§19: cheap preflight allows/rejects finalize exactly like the full
    derivation for every durable, non-canonically-mutated state."""

    def _matrix_cases(self) -> dict[str, dict]:
        return {
            "J.ready": {},
            "A.analysis_incomplete": {
                "analysis_report": {"status": "failed"},
                "match_status": "uploaded",
            },
            "B.initial_audit_incomplete": {"audit_complete": False},
            "C.recompute_failed": {"recompute_failure": True},
            "D.required_blockers": {"important_decisions_remaining": 2},
            "E.mixed_blockers": {"mixed_unresolved": 1},
            "F.coverage_readiness_blocked": {"coverage_blocked": True},
            "G.render_queued": {
                "job": {"status": "queued", "job_key": "job-1"},
                "lock_alive": True,
            },
            "H.render_running": {
                "job": {"status": "running", "job_key": "job-1"},
                "lock_alive": True,
            },
            "I.render_failed": {
                "job": {"status": "failed", "job_key": "job-1", "error": {"message": "x"}},
            },
            "K.video_qa": {"current_outputs": True},
            "L.complete_qa_approved": {"current_outputs": True, "qa_approval": True},
        }

    def test_cheap_preflight_matches_full_derivation_for_every_durable_state(self) -> None:
        import contextlib

        for name, mutations in self._matrix_cases().items():
            with self.subTest(state=name):
                with _workspace() as root:
                    context = self._prepare(root, mutations)
                    match = context["match"]
                    with contextlib.ExitStack() as stack:
                        if mutations.get("audit_complete", True):
                            stack.enter_context(patch(
                                "app.services.review_workflow_state."
                                "load_initial_audit_completion_evidence",
                                return_value=dict(COMPLETE_AUDIT_EVIDENCE),
                            ))
                        if mutations.get("lock_alive"):
                            stack.enter_context(patch(
                                "app.services.identity_reviewed_output_jobs."
                                "_lock_owner_alive",
                                return_value=True,
                            ))
                        cheap = build_cheap_finalize_preflight_state(root, match)
                        full = get_review_workflow_state(root, match)

                    cheap_allows = "finalize_identity" in set(
                        cheap.get("allowed_actions") or []
                    )
                    full_allows = "finalize_identity" in set(
                        full.get("allowed_actions") or []
                    )
                    if name == "J.ready":
                        # The baseline fixture must genuinely allow finalize;
                        # parity between two rejections would prove nothing.
                        self.assertTrue(cheap_allows, msg="baseline must allow finalize")
                    self.assertEqual(
                        cheap_allows,
                        full_allows,
                        msg=(
                            f"{name}: allow/disagree cheap-blockers="
                            f"{cheap.get('blockers')} full-blockers={full.get('blockers')}"
                        ),
                    )
                    self.assertEqual(cheap.get("phase"), full.get("phase"), msg=name)
                    if not full_allows:
                        self.assertEqual(
                            self._rejection_code(cheap),
                            self._rejection_code(full),
                            msg=name,
                        )

    def _prepare(self, root: Path, mutations: dict) -> dict:
        context = self._ready_workspace_into(root)
        match = context["match"]
        progress = context["progress"]
        digest = context["digest"]
        if "analysis_report" in mutations:
            _write(root / "analysis_report.json", mutations["analysis_report"])
        if "match_status" in mutations:
            _write(
                root / "match.json",
                {**match, "status": mutations["match_status"]},
            )
        if mutations.get("recompute_failure"):
            _write(
                root / "review_workflow_recompute_failure.json",
                {"schema_version": "1.0.0", "code": "review_recompute_failed"},
            )
        progress_mutations: dict = {}
        if mutations.get("important_decisions_remaining") is not None:
            progress_mutations["summary"] = {
                **dict(progress.get("summary") or {}),
                "important_decisions_remaining": int(
                    mutations["important_decisions_remaining"]
                ),
            }
        if mutations.get("mixed_unresolved"):
            mixed = dict(progress.get("mixed_players") or {})
            progress_mutations["mixed_players"] = {
                **mixed,
                "summary": {
                    **dict(mixed.get("summary") or {}),
                    "unresolved": int(mutations["mixed_unresolved"]),
                },
            }
        if mutations.get("coverage_blocked"):
            progress_mutations["coverage_readiness"] = {
                "status": "incomplete",
                "allows_finalize": False,
                "blockers": [{"code": "significant_named_coverage_debt"}],
            }
        if progress_mutations:
            _write(
                root / "reviewed_identity_progress.json",
                {**progress, **progress_mutations},
            )
        job = mutations.get("job")
        stats = None
        manifest = None
        if mutations.get("current_outputs"):
            stats = {"source_snapshot_digest": digest}
            job = {
                "status": "completed",
                "job_key": "job-1",
                "source_snapshot_digest": digest,
                "video_digest": "video-1",
            }
            manifest = {"stale": False}
        if job is not None:
            _write(root / "reviewed_video_job.json", job)
        if manifest is not None:
            _write(root / "reviewed_output_manifest.json", manifest)
        if stats is not None:
            _write(root / "reviewed_player_stats.json", stats)
        if mutations.get("qa_approval"):
            fingerprints = current_approval_fingerprint(digest, stats, job, manifest)
            save_video_qa_approval(
                root,
                match_id=str(match["id"]),
                fingerprints=fingerprints,
            )
        return context

    def _ready_workspace_into(self, root: Path) -> dict:
        # One authoritative match document shared by disk and every builder:
        # finalize hashes the exact match_doc, so a divergent on-disk copy
        # would legitimately mark the snapshot stale in the full derivation.
        match = {**_match(), "status": "analyzed"}
        _fixture(root)
        _write(root / "analysis_report.json", {"status": "completed"})
        _write(root / "match.json", match)
        _write(
            root / "identity_roster_subject_review_decisions_shadow.json",
            {
                "decisions": [
                    {"candidate_subject_id": "s1", "decision": "assign_roster_player", "player_id": "p1"},
                    {"candidate_subject_id": "s2", "decision": "assign_roster_player", "player_id": "p2"},
                ]
            },
        )
        snapshot = finalize_reviewed_identity(root, match)
        progress = build_reviewed_identity_progress(root, match)
        _write(root / "reviewed_identity_progress.json", progress)
        return {
            "match": match,
            "digest": snapshot["semantic_digest"],
            "progress": progress,
        }

    @staticmethod
    def _rejection_code(state: dict) -> str:
        blocker = next(iter(state.get("blockers") or []), {})
        return str(blocker.get("code") or "workflow_action_not_allowed")


def _assert_same_product_projection(
    test: unittest.TestCase,
    optimized: dict,
    independent: dict,
) -> None:
    """§11/§35 differential contract over product-relevant fields only."""
    for field in (
        "summary",
        "identity_coverage",
        "coverage_readiness",
        "coverage_residuals",
        "workload",
        "optional_audit",
        "observations",
        "policy",
        "next_cases",
        "optional_audit_cases",
        "mixed_players",
        "technical_diagnostics",
    ):
        test.assertEqual(
            optimized.get(field),
            independent.get(field),
            msg=f"product field mismatch: {field}",
        )
    test.assertEqual(
        optimized.get("_projection_inputs", {}).get("coverage"),
        independent.get("_projection_inputs", {}).get("coverage"),
    )


def _durable_form(progress: dict) -> dict:
    """Compact durable progress contract (mirrors the orchestrator writer)."""
    return {
        key: value
        for key, value in progress.items()
        if key not in {"_internal_review_units", "_projection_inputs", "review_units"}
    }


def _public_form(progress: dict) -> dict:
    return {
        key: value
        for key, value in progress.items()
        if key not in {"_internal_review_units", "_projection_inputs"}
    }


def _fixture(root: Path) -> None:
    tracklets = [
        {
            "tracklet_id": "t1",
            "team_label": "A",
            "team_id": "ta",
            "positions_m": [
                {"frame": 3, "status": "detected", "pitch_m": [1.0, 1.0], "bbox_xyxy": [1, 1, 5, 8]},
                {"frame": 4, "status": "detected", "pitch_m": [1.5, 1.0], "bbox_xyxy": [2, 1, 6, 8]},
            ],
        },
        {
            "tracklet_id": "t1b",
            "team_label": "A",
            "team_id": "ta",
            "positions_m": [
                {"frame": 8, "status": "detected", "pitch_m": [3.0, 1.0], "bbox_xyxy": [3, 1, 7, 8]},
                {"frame": 9, "status": "detected", "pitch_m": [3.5, 1.0], "bbox_xyxy": [4, 1, 8, 8]},
            ],
        },
        {
            "tracklet_id": "t2",
            "team_label": "B",
            "team_id": "tb",
            "positions_m": [
                {"frame": 20, "status": "detected", "pitch_m": [1.0, 2.0], "bbox_xyxy": [1, 2, 5, 9]},
                {"frame": 21, "status": "detected", "pitch_m": [1.5, 2.0], "bbox_xyxy": [2, 2, 6, 9]},
            ],
        },
    ]
    _write(root / "tracklets.json", {"tracklets": tracklets})
    _write(
        root / "identity_candidate_shadow.json",
        {
            "subjects": [
                {"candidate_subject_id": "s1", "tracklet_ids": ["t1", "t1b"]},
                {"candidate_subject_id": "s2", "tracklet_ids": ["t2"]},
            ]
        },
    )
    _write(
        root / "global_identity.json",
        {
            "slots": [
                {"stable_player_id": slot_id, "team_label": slot_id[0], "tracklet_ids": []}
                for slot_id in ("A01", "A02", "A03", "B01", "B02", "B03")
            ]
        },
    )
    _write(root / "stable_players.json", {"players": []})
    _write(
        root / "identity_roster_subject_review_shadow.json",
        {
            "cards": [
                {
                    "review_card_key": "card-s1",
                    "candidate_subject_id": "s1",
                    "team_label": "A",
                    "review_status": "ready_for_operator_review",
                    "roster_candidates": [{"player_id": "p1"}],
                    "visual_evidence": {"anchor_crops": []},
                },
                {
                    "review_card_key": "card-s2",
                    "candidate_subject_id": "s2",
                    "team_label": "B",
                    "review_status": "ready_for_operator_review",
                    "roster_candidates": [{"player_id": "p2"}],
                    "visual_evidence": {"anchor_crops": []},
                },
            ]
        },
    )


def _match() -> dict:
    return {
        "id": "m1",
        "teams": [
            {"id": "ta", "players": [{"id": "p1", "name": "One", "number": "8"}]},
            {"id": "tb", "players": [{"id": "p2", "name": "Two", "number": "9"}]},
        ],
    }


def _fingerprint(path: Path) -> dict | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return {"mtime_ns": int(stat.st_mtime_ns), "size_bytes": int(stat.st_size)}


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class _workspace:
    def __enter__(self) -> Path:
        self.temporary = tempfile.TemporaryDirectory()
        return Path(self.temporary.name)

    def __exit__(self, *args: object) -> None:
        self.temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
