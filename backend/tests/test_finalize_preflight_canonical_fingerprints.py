from __future__ import annotations

"""Canonical-staleness regressions for the cheap finalize preflight.

Protects against the false-reject edge: a completed S1 stats/video/QA state
must not be able to permanently block ``finalize_identity`` after canonical
Reviewed Identity inputs change.  Cheap source-file generation fingerprints
(size+mtime_ns only) make the preflight treat such state as needing refresh
and admit the authoritative pass, which remains the final semantic truth.
"""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.identity_reviewed_progress import (
    build_reviewed_identity_progress,
)
from app.services.identity_reviewed_snapshot import (
    finalize_reviewed_identity,
)
from app.services.identity_reviewed_team_attribution_evidence import (
    materialize_team_attribution_evidence,
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

REVIEW_DECISIONS_S1 = {
    "decisions": [
        {"candidate_subject_id": "s1", "decision": "assign_roster_player", "player_id": "p1"},
        {"candidate_subject_id": "s2", "decision": "assign_roster_player", "player_id": "p2"},
    ]
}


class CanonicalStalePreflightRegressionTests(unittest.TestCase):
    """Cheap fingerprints must unblock authoritative finalize after a real
    canonical mutation, while preserving duplicate-finalize rejection when
    nothing canonical changed."""

    def _ready_generation(self, root: Path) -> dict:
        match = {**_match(), "status": "analyzed"}
        _write(root / "analysis_report.json", {"status": "completed"})
        _write(root / "match.json", match)
        _write(
            root / "identity_roster_subject_review_decisions_shadow.json",
            REVIEW_DECISIONS_S1,
        )
        snapshot = finalize_reviewed_identity(root, match)
        digest = str(snapshot["semantic_digest"])
        progress = build_reviewed_identity_progress(root, match)
        self.assertEqual(progress["summary"]["important_decisions_remaining"], 0)
        _write(root / "reviewed_identity_progress.json", progress)

        # Current S1 downstream outputs: stats, completed render, QA approval.
        _write(root / "reviewed_player_stats.json", {"source_snapshot_digest": digest})
        video = root / "reviewed_video.mp4"
        video.write_bytes(b"not-a-real-video-but-hashable")
        video_digest = hashlib.sha256(video.read_bytes()).hexdigest()
        _write(
            root / "reviewed_video_job.json",
            {
                "status": "completed",
                "job_key": "job-s1",
                "source_snapshot_digest": digest,
                "video_digest": video_digest,
            },
        )
        _write(root / "reviewed_output_manifest.json", {"stale": False})
        context = {
            "match": match,
            "digest": digest,
            "stats_doc": {"source_snapshot_digest": digest},
            "job": {
                "status": "completed",
                "job_key": "job-s1",
                "source_snapshot_digest": digest,
                "video_digest": video_digest,
            },
            "manifest": {"stale": False},
        }
        self._qa_current(root, context)
        return context

    def _qa_current(self, root: Path, context: dict) -> None:
        # Recompute the exact approval fingerprints from the CURRENT durable
        # artifacts, mirroring approve_review_video_qa().
        fingerprints = current_approval_fingerprint(
            context["digest"],
            _load(root / "reviewed_player_stats.json"),
            _load(root / "reviewed_video_job.json"),
            _load(root / "reviewed_output_manifest.json"),
        )
        save_video_qa_approval(
            root,
            match_id=str(context["match"]["id"]),
            fingerprints=fingerprints,
        )

    def test_canonical_change_after_complete_state_allows_authoritative_finalize(
        self,
    ) -> None:
        with _workspace() as root:
            context = self._ready_generation(root)
            audit_patch = patch(
                "app.services.review_workflow_state.load_initial_audit_completion_evidence",
                return_value=dict(COMPLETE_AUDIT_EVIDENCE),
            )
            with audit_patch:
                preflight = build_cheap_finalize_preflight_state(root, context["match"])
            self.assertEqual(preflight["phase"], "complete")
            self.assertNotIn("finalize_identity", preflight["allowed_actions"])

            # A REAL canonical Reviewed Identity dependency changes after S1;
            # report/stats/output/QA stay untouched at S1.
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

            with patch(
                "app.services.review_workflow_state.load_initial_audit_completion_evidence",
                return_value=dict(COMPLETE_AUDIT_EVIDENCE),
            ):
                stale_preflight = build_cheap_finalize_preflight_state(
                    root, context["match"]
                )
            self.assertNotIn(stale_preflight["phase"], {"video_qa", "complete"})
            self.assertIn("finalize_identity", stale_preflight["allowed_actions"])

            # The authoritative transaction is now enterable and remains the
            # final truth: it recomputes S2, discovers the real blocker and
            # returns the operator to review with fresh evidence.
            with patch(
                "app.services.review_workflow_state.load_initial_audit_completion_evidence",
                return_value=dict(COMPLETE_AUDIT_EVIDENCE),
            ), patch(
                "app.services.review_workflow_orchestrator.load_initial_audit_completion_evidence",
                return_value=dict(COMPLETE_AUDIT_EVIDENCE),
            ):
                with self.assertRaises(WorkflowActionError) as raised:
                    finalize_review_for_qa(root, context["match"])

            self.assertEqual(raised.exception.code, "identity_issues_remaining")
            # No NEW render may be queued when the recompute found blockers:
            # the durable job file must still be the untouched S1 artifact.
            self.assertEqual(
                _load(root / "reviewed_video_job.json").get("job_key"), "job-s1"
            )
            persisted_report = _load(root / "reviewed_identity_report.json")
            self.assertNotEqual(persisted_report["snapshot_digest"], context["digest"])
            persisted_progress = _load(root / "reviewed_identity_progress.json")
            self.assertEqual(
                persisted_progress["source_snapshot_digest"],
                persisted_report["snapshot_digest"],
            )
            self.assertGreaterEqual(
                persisted_progress["mixed_players"]["summary"]["unresolved"], 1
            )
            self.assertFalse((root / "review_workflow_recompute_failure.json").exists())

            with patch(
                "app.services.review_workflow_state.load_initial_audit_completion_evidence",
                return_value=dict(COMPLETE_AUDIT_EVIDENCE),
            ):
                state = get_review_workflow_state(root, context["match"])
            self.assertEqual(state["phase"], "mixed_players")
            self.assertNotIn("finalize_identity", state["allowed_actions"])
            # The refreshed report carries fresh generation fingerprints.
            self.assertIsNotNone(
                persisted_report.get("source_file_fingerprints")
            )

    def test_unchanged_canonical_state_keeps_video_qa_and_rejects_duplicate_finalize(
        self,
    ) -> None:
        with _workspace() as root:
            context = self._ready_generation(root)
            with patch(
                "app.services.review_workflow_state.load_initial_audit_completion_evidence",
                return_value=dict(COMPLETE_AUDIT_EVIDENCE),
            ):
                preflight = build_cheap_finalize_preflight_state(root, context["match"])
                self.assertEqual(preflight["phase"], "complete")
                self.assertTrue(preflight["freshness"].get("qa_approval_current"))
                self.assertNotIn("finalize_identity", preflight["allowed_actions"])

                with self.assertRaises(WorkflowActionError) as raised:
                    finalize_review_for_qa(root, context["match"])
            self.assertEqual(
                raised.exception.code, "workflow_action_not_allowed"
            )
            # No authoritative work happened: identical S1 generation.
            report = _load(root / "reviewed_identity_report.json")
            self.assertEqual(report["snapshot_digest"], context["digest"])

            # Without QA approval the same artifacts sit at video_qa.
            (root / "reviewed_video_qa_approval.json").unlink(missing_ok=True)
            with patch(
                "app.services.review_workflow_state.load_initial_audit_completion_evidence",
                return_value=dict(COMPLETE_AUDIT_EVIDENCE),
            ):
                qa_preflight = build_cheap_finalize_preflight_state(
                    root, context["match"]
                )
            self.assertEqual(qa_preflight["phase"], "video_qa")
            self.assertNotIn("finalize_identity", qa_preflight["allowed_actions"])

    def test_fingerprint_false_positive_stays_semantically_safe(self) -> None:
        with _workspace() as root:
            context = self._ready_generation(root)
            source = root / "global_identity.json"
            stat = source.stat()
            # Touch without any content change: size stays, mtime moves.
            import os

            os.utime(
                source,
                ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000),
            )

            with patch(
                "app.services.review_workflow_state.load_initial_audit_completion_evidence",
                return_value=dict(COMPLETE_AUDIT_EVIDENCE),
            ):
                preflight = build_cheap_finalize_preflight_state(root, context["match"])
            # The conservative cheap gate may demand one extra recompute...
            self.assertNotEqual(preflight["phase"], "complete")
            self.assertIn("finalize_identity", preflight["allowed_actions"])

            # ...and the authoritative pass then proves semantic equivalence:
            # an idempotent rebuild of identical inputs yields the SAME
            # generation, so no incorrect output can appear.
            with patch(
                "app.services.review_workflow_state.load_initial_audit_completion_evidence",
                return_value=dict(COMPLETE_AUDIT_EVIDENCE),
            ), patch(
                "app.services.review_workflow_orchestrator.load_initial_audit_completion_evidence",
                return_value=dict(COMPLETE_AUDIT_EVIDENCE),
            ), patch(
                "app.services.review_workflow_orchestrator.build_reviewed_stats",
                return_value={},
            ) as stats_stub, patch(
                "app.services.review_workflow_orchestrator.generate_reviewed_output",
                return_value={"status": "queued", "job_key": "bench-stub"},
            ) as render_stub:
                result = finalize_review_for_qa(root, context["match"])

            self.assertEqual(
                result["reviewed_identity"]["semantic_digest"], context["digest"]
            )
            report = _load(root / "reviewed_identity_report.json")
            self.assertEqual(report["snapshot_digest"], context["digest"])
            performance = result["performance"]
            self.assertGreater(performance["total_ms"], 0)
            self.assertGreaterEqual(performance["preflight_workflow_ms"], 0)
            self.assertIn("finalize_reviewed_identity_ms", performance)
            # The tail phases really ran inside this finalize transaction.
            stats_stub.assert_called_once()
            render_stub.assert_called_once()
            # Fresh fingerprints were re-recorded for the same generation.
            self.assertIsNotNone(report.get("source_file_fingerprints"))

    def test_accepted_terminal_team_u_residual_finalizes_without_stats_contamination(
        self,
    ) -> None:
        """Exercise the real accepted-residual finalize transaction end to end.

        This is deliberately not a client or mocked-workflow test.  The
        compact preflight, authoritative refresh, coverage policy, snapshot,
        progress and Reviewed stats all operate on durable fixture artifacts.
        Only the expensive reviewed-video submission is replaced.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            match = _accepted_team_u_residual_fixture(root)
            audit = patch(
                "app.services.review_workflow_state.load_initial_audit_completion_evidence",
                return_value=dict(COMPLETE_AUDIT_EVIDENCE),
            )
            with audit:
                snapshot = finalize_reviewed_identity(root, match)
                # The no-crop status comes from the real exact-source evidence
                # builder; the U tracklet's deliberately too-small boxes make
                # its evidence genuinely unavailable rather than missing.
                evidence = materialize_team_attribution_evidence(root)
                self.assertEqual(
                    evidence["cases"][0]["status"],
                    "no_team_attribution_evidence",
                )
                progress = build_reviewed_identity_progress(root, match)
                _write(root / "reviewed_identity_progress.json", progress)

                readiness = progress["coverage_readiness"]
                residual = readiness["team_attribution_residual"]
                self.assertTrue(readiness["allows_finalize"])
                self.assertEqual(residual["status"], "accepted_within_tolerance")
                self.assertEqual(residual["observations"], 3)
                self.assertEqual(residual["residual_budget_observations"], 3)
                self.assertTrue(residual["within_tolerance"])

                preflight = build_cheap_finalize_preflight_state(root, match)
                self.assertTrue(preflight["mandatory_operator_review_complete"])
                self.assertTrue(preflight["data_quality_ready_for_output"])
                self.assertIn("finalize_identity", preflight["allowed_actions"])

                with patch(
                    "app.services.review_workflow_orchestrator.load_initial_audit_completion_evidence",
                    return_value=dict(COMPLETE_AUDIT_EVIDENCE),
                ), patch(
                    "app.services.identity_reviewed_stats.read_match_video_metadata",
                    return_value={
                        "fps": 25.0,
                        "frame_count": 103,
                        "duration_sec": 4.12,
                        "source": "test",
                        "filename": "fixture.mp4",
                    },
                ), patch(
                    # Hot-state correction galleries are unrelated to this
                    # terminal Team-U policy test and would otherwise attempt
                    # to decode the intentionally non-video placeholder.
                    "app.services.identity_reviewed_hot_state.render_mixed_review_evidence",
                    return_value=set(),
                ), patch(
                    "app.services.review_workflow_orchestrator.generate_reviewed_output",
                    return_value={"status": "queued", "job_key": "accepted-residual"},
                ) as render:
                    result = finalize_review_for_qa(root, match)

            # The final, authoritative refresh independently retained the
            # policy state.  A stale compact progress document alone would not
            # satisfy this assertion.
            workflow = result["workflow"]
            self.assertIn("finalize_identity", workflow["allowed_actions"])
            self.assertTrue(workflow["mandatory_operator_review_complete"])
            self.assertTrue(workflow["data_quality_ready_for_output"])
            render.assert_called_once()

            refreshed_progress = _load(root / "reviewed_identity_progress.json")
            refreshed_residual = refreshed_progress["coverage_readiness"][
                "team_attribution_residual"
            ]
            self.assertEqual(refreshed_residual["status"], "accepted_within_tolerance")
            self.assertEqual(refreshed_residual["observations"], 3)
            self.assertEqual(refreshed_residual["residual_budget_observations"], 3)
            self.assertTrue(refreshed_residual["within_tolerance"])
            self.assertEqual(refreshed_progress["summary"]["important_decisions_remaining"], 0)
            self.assertEqual(refreshed_progress["mixed_players"]["summary"]["unresolved"], 0)

            final_snapshot = _load(root / "reviewed_identity_snapshot.json")
            unknown_assignments = [
                row
                for row in final_snapshot["tracklet_assignments"]
                if row["tracklet_id"] == "u-residual"
            ]
            self.assertEqual(len(unknown_assignments), 1)
            self.assertEqual(unknown_assignments[0]["team_label"], "U")
            self.assertIsNone(unknown_assignments[0]["canonical_player_id"])
            self.assertEqual(
                _load(root / "identity_roster_subject_review_decisions_shadow.json"),
                {"decisions": [{"candidate_subject_id": "a-player", "decision": "assign_roster_player", "player_id": "p1"}]},
            )

            stats = _load(root / "reviewed_player_stats.json")
            timeline = _load(root / "reviewed_player_timeline.json")
            heatmaps = _load(root / "reviewed_player_heatmaps.json")
            self.assertEqual([row["player_id"] for row in stats["players"]], ["p1"])
            player = stats["players"][0]
            # All player-dependent metrics are derived exclusively from the 27
            # confirmed Team-A observations; Team-U is absent from rows,
            # distance/speed/intensity, heatmap and average position alike.
            self.assertEqual(player["confirmed_detected_observations"], 27)
            self.assertEqual(player["detected_frames"], 27)
            self.assertEqual(timeline["players"][0]["player_id"], "p1")
            self.assertEqual(len(timeline["players"][0]["observations"]), 27)
            self.assertEqual(heatmaps["heatmaps"][0]["player_id"], "p1")
            self.assertEqual(heatmaps["heatmaps"][0]["samples"], 27)
            self.assertNotIn("u-residual", json.dumps({"stats": stats, "timeline": timeline, "heatmaps": heatmaps}))

            stats_readiness = _load(root / "reviewed_stats_readiness.json")
            stats_residual = stats_readiness["coverage_readiness"][
                "team_attribution_residual"
            ]
            self.assertEqual(stats_readiness["status"], "completed")
            self.assertEqual(stats_residual["status"], "accepted_within_tolerance")
            self.assertEqual(stats_residual["observations"], 3)
            self.assertEqual(stats_residual["residual_budget_observations"], 3)
            self.assertTrue(stats_residual["within_tolerance"])


def _accepted_team_u_residual_fixture(root: Path) -> dict:
    """Persist a minimal, production-shaped accepted Team-U terminal state."""
    a_positions = [
        {
            "frame": frame,
            "status": "detected",
            "pitch_m": [float(frame), 10.0],
            "smoothed_pitch_m": [float(frame), 10.0],
            "bbox_xyxy": [10, 10, 30, 50],
        }
        for frame in range(27)
    ]
    # These are real on-pitch detected observations, but their exact source
    # has no renderable team-attribution crop: width/height are below the
    # evidence builder's safety floor. They must remain U, never become p1.
    u_positions = [
        {
            "frame": frame,
            "status": "detected",
            "pitch_m": [float(frame), 20.0],
            "smoothed_pitch_m": [float(frame), 20.0],
            "bbox_xyxy": [10, 10, 11, 11],
        }
        for frame in range(100, 103)
    ]
    match = {
        "id": "accepted-team-u-residual",
        "status": "analyzed",
        "teams": [
            {
                "id": "team-a",
                "team_label": "A",
                "players": [{"id": "p1", "name": "Named A", "number": "7"}],
            },
            {
                "id": "team-b",
                "team_label": "B",
                "players": [{"id": "p2", "name": "Team B", "number": "9"}],
            },
        ],
        "identity_review_scope": {
            "teams": {"A": "complete_roster", "B": "team_stats_only"},
        },
    }
    _write(root / "match.json", match)
    _write(root / "analysis_report.json", {"status": "completed"})
    # There are no eligible crop requests for the deliberately tiny Team-U
    # boxes, so the renderer returns before opening this placeholder video.
    # Its presence lets the evidence builder preserve the evaluated
    # `no_team_attribution_evidence` status rather than reporting a missing
    # video lifecycle error.
    (root / "video.mp4").write_bytes(b"fixture-video-not-opened")
    _write(root / "tracklets.json", {"tracklets": [
        {
            "tracklet_id": "a-confirmed",
            "team_label": "A",
            "team_id": "team-a",
            "positions_m": a_positions,
        },
        {
            "tracklet_id": "u-residual",
            "team_label": "U",
            "positions_m": u_positions,
        },
    ]})
    _write(root / "identity_candidate_shadow.json", {"subjects": [
        {"candidate_subject_id": "a-player", "tracklet_ids": ["a-confirmed"]},
        {"candidate_subject_id": "u-player", "tracklet_ids": ["u-residual"]},
    ]})
    _write(root / "global_identity.json", {"slots": []})
    _write(root / "stable_players.json", {"players": []})
    _write(root / "identity_roster_subject_review_decisions_shadow.json", {
        "decisions": [{
            "candidate_subject_id": "a-player",
            "decision": "assign_roster_player",
            "player_id": "p1",
        }],
    })
    _write(root / "identity_roster_subject_review_shadow.json", {"cards": [
        {
            "review_card_key": "a-player-card",
            "candidate_subject_id": "a-player",
            "team_label": "A",
            "review_status": "reviewed",
            "requires_operator_review": False,
            "visual_evidence": {"anchor_crops": []},
        },
        {
            "review_card_key": "u-player-card",
            "candidate_subject_id": "u-player",
            "team_label": "U",
            "review_status": "no_visual_evidence",
            "requires_operator_review": True,
            "visual_evidence": {"anchor_crops": []},
        },
    ]})
    return match


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


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class _workspace:
    def __enter__(self) -> Path:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        _fixture(root)
        return root

    def __exit__(self, *args: object) -> None:
        self.temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
