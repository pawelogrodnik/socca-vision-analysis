from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.identity_reviewed_mixed_store import build_mixed_review_queue
from app.services.review_workflow_state import derive_review_workflow_state


class MixedScopeQueueContractTests(unittest.TestCase):
    def test_mandatory_queue_excludes_twenty_certain_team_b_sources(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            match = _match()
            _write_tracklets(root, a_frames={1, 2}, b_frames=set(range(3, 23)))
            markers = [
                _exact_marker(f"a-{frame}", "a", frame, "same_team_a", "A")
                for frame in (1, 2)
            ] + [
                _exact_marker(f"b-{frame}", "b", frame, "same_team_b", "B")
                for frame in range(3, 23)
            ]
            _write(root / "reviewed_identity_mixed_players.json", {"cases": markers})

            queue = build_mixed_review_queue(root, match)

            self.assertEqual([row["case_id"] for row in queue["cases"]], ["a-1", "a-2"])
            self.assertEqual(queue["summary"]["unresolved"], 2)
            self.assertEqual(queue["summary"]["unresolved_total"], 22)
            self.assertEqual(queue["summary"]["nonblocking_by_scope"], 20)
            self.assertEqual(_workflow(queue["summary"])["phase"], "mixed_players")

            markers[0]["resolution_status"] = "resolved"
            _write(root / "reviewed_identity_mixed_players.json", {"cases": markers})
            self.assertEqual(build_mixed_review_queue(root, match)["summary"]["unresolved"], 1)
            markers[1]["resolution_status"] = "resolved"
            _write(root / "reviewed_identity_mixed_players.json", {"cases": markers})
            finished = build_mixed_review_queue(root, match)
            self.assertEqual(finished["summary"]["unresolved"], 0)
            self.assertNotEqual(_workflow(finished["summary"])["phase"], "mixed_players")
            self.assertEqual(finished["summary"]["nonblocking_by_scope"], 20)

    def test_cross_team_and_stale_exact_sources_fail_closed_into_mandatory_queue(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            match = _match()
            _write_tracklets(root, a_frames={1}, b_frames={2})
            markers = [
                {
                    **_exact_marker("cross", "a", 1, "cross_team", "A"),
                    "source": {
                        "candidate_subject_id": "cross",
                        "effective_team_label": "A",
                        "owned_observations": [
                            {"tracklet_id": "a", "frame": 1},
                            {"tracklet_id": "b", "frame": 2},
                        ],
                    },
                },
                {
                    **_exact_marker("stale", "b", 2, "same_team_b", "B"),
                    "source": {
                        "candidate_subject_id": "subject-stale",
                        "effective_team_label": "B",
                        "source_ownership_digest": "ownership-stale",
                        # One materialized pair plus one missing pair: exact
                        # ownership cannot be safely narrowed to Team B.
                        "owned_observations": [
                            {"tracklet_id": "b", "frame": 2},
                            {"tracklet_id": "b", "frame": 99},
                        ],
                    },
                },
            ]
            _write(root / "reviewed_identity_mixed_players.json", {"cases": markers})

            queue = build_mixed_review_queue(root, match)

            self.assertEqual({row["case_id"] for row in queue["cases"]}, {"cross", "stale"})
            self.assertEqual(queue["summary"]["unresolved"], 2)
            stale = next(row for row in queue["cases"] if row["case_id"] == "stale")
            self.assertEqual(stale["scope_status"], "stale_or_unclassifiable_blocking")
            self.assertEqual(queue["summary"]["nonblocking_by_scope"], 0)


def _match() -> dict:
    return {
        "id": "scope-mixed",
        "status": "analyzed",
        "identity_review_scope": {"teams": {"A": "complete_roster", "B": "team_stats_only"}},
        "teams": [{"team_label": "A"}, {"team_label": "B"}],
    }


def _exact_marker(case_id: str, tracklet_id: str, frame: int, hint: str, team: str) -> dict:
    return {
        "case_id": case_id,
        "candidate_subject_id": f"subject-{case_id}",
        "original_issue": "mixed_players",
        "mixed_hint": hint,
        "resolution_status": "unresolved",
        "observation_count": 2,
        "source_subject_digest": f"digest-{case_id}",
        "source": {
            "candidate_subject_id": f"subject-{case_id}",
            "effective_team_label": team,
            "source_ownership_digest": f"ownership-{case_id}",
            "owned_observations": [{"tracklet_id": tracklet_id, "frame": frame}],
        },
    }


def _write_tracklets(root: Path, *, a_frames: set[int], b_frames: set[int]) -> None:
    rows = []
    for tracklet_id, team, frames in (("a", "A", a_frames), ("b", "B", b_frames)):
        rows.append({
            "tracklet_id": tracklet_id,
            "team_label": team,
            "positions_m": [
                {"frame": frame, "detected": True, "play_area_status": "inside_play"}
                for frame in sorted(frames)
            ],
        })
    _write(root / "tracklets.json", {"tracklets": rows})
    _write(root / "identity_candidate_shadow.json", {"subjects": []})
    _write(root / "identity_roster_subject_review_shadow.json", {"cards": []})
    _write(root / "global_identity.json", {"slots": []})


def _workflow(summary: dict) -> dict:
    return derive_review_workflow_state({
        "match_id": "scope-mixed",
        "analysis_completed": True,
        "initial_audit": {"complete": True},
        "issues": {"blocking": summary["unresolved"], "normal_blocking": 0, "mixed_blocking": summary["unresolved"]},
        "freshness": {"review_progress_current": True},
        "render": {"status": "missing"},
    })


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
