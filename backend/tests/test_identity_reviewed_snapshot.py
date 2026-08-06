from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.services.identity_reviewed_snapshot import finalize_reviewed_identity, get_reviewed_identity_status


class ReviewedIdentitySnapshotTests(unittest.TestCase):
    def test_explicit_review_wins_and_unresolved_overrides_seed(self) -> None:
        with _workspace() as root:
            _write_inputs(root, decisions=[_decision("s1", "assign_roster_player", "p1"), _decision("s2", "mark_unresolved")], seeded=[_seed("s2", "p1")])
            result = finalize_reviewed_identity(root, _match())
            rows = {row["tracklet_id"]: row for row in result["tracklet_assignments"]}
            self.assertEqual(rows["t1"]["display_label"], "Paweł")
            self.assertEqual(rows["t2"]["identity_status"], "unresolved")
            self.assertEqual(rows["t2"]["display_label"], "A02")

    def test_cross_team_and_invalid_player_are_not_named(self) -> None:
        with _workspace() as root:
            _write_inputs(root, decisions=[_decision("s1", "assign_roster_player", "p2"), _decision("s2", "assign_roster_player", "gone")])
            result = finalize_reviewed_identity(root, _match())
            rows = {row["tracklet_id"]: row for row in result["tracklet_assignments"]}
            self.assertEqual(rows["t1"]["identity_status"], "conflicted")
            self.assertEqual(rows["t2"]["identity_status"], "blocked")

    def test_conflicting_explicit_decisions_are_not_silently_resolved(self) -> None:
        with _workspace() as root:
            _write_inputs(root, decisions=[_decision("s1", "assign_roster_player", "p1"), _decision("s1", "mark_unresolved")])
            result = finalize_reviewed_identity(root, _match())
            row = {item["tracklet_id"]: item for item in result["tracklet_assignments"]}["t1"]
            self.assertEqual(row["identity_status"], "conflicted")
            self.assertEqual(row["display_label"], "A01 !")

    def test_labels_are_deterministic_and_snapshot_becomes_stale(self) -> None:
        with _workspace() as root:
            _write_inputs(root, decisions=[])
            first = finalize_reviewed_identity(root, _match()); second = finalize_reviewed_identity(root, _match())
            self.assertEqual(first["semantic_digest"], second["semantic_digest"])
            self.assertEqual([row["fallback_label"] for row in first["tracklet_assignments"]], ["A01", "A02"])
            tracklets = json.loads((root / "tracklets.json").read_text()); tracklets["tracklets"][0]["team_label"] = "B"; (root / "tracklets.json").write_text(json.dumps(tracklets))
            self.assertTrue(get_reviewed_identity_status(root)["stale"])

    def test_telemetry_timestamp_does_not_change_semantic_snapshot(self) -> None:
        with _workspace() as root:
            _write_inputs(root, decisions=[])
            first_match = {**_match(), "updated_at": "2026-01-01T00:00:00Z"}
            (root / "match.json").write_text(json.dumps(first_match))
            first = finalize_reviewed_identity(root, first_match)
            second_match = {**_match(), "updated_at": "2026-01-02T00:00:00Z"}
            (root / "match.json").write_text(json.dumps(second_match))
            second = finalize_reviewed_identity(root, second_match)
            self.assertEqual(first["semantic_digest"], second["semantic_digest"])

    def test_candidate_fragments_reuse_base_stable_slot(self) -> None:
        with _workspace() as root:
            _write_inputs(root, decisions=[])
            candidates = {
                "subjects": [
                    {"candidate_subject_id": "s1", "candidate_player_id": "A01~2", "tracklet_ids": ["t1"]},
                    {"candidate_subject_id": "s2", "candidate_player_id": "A01~7", "tracklet_ids": ["t2"]},
                ]
            }
            (root / "identity_candidate_shadow.json").write_text(json.dumps(candidates))
            result = finalize_reviewed_identity(root, _match())
            self.assertEqual(
                [row["fallback_label"] for row in result["tracklet_assignments"]],
                ["A01", "A01"],
            )
            self.assertEqual(result["fragmentation_diagnostics"]["stable_anonymous_entities_total"], 1)

    def test_ambiguous_subject_membership_is_a_hard_conflict(self) -> None:
        with _workspace() as root:
            _write_inputs(root, decisions=[])
            candidates = {
                "subjects": [
                    {"candidate_subject_id": "s1", "candidate_player_id": "A01", "tracklet_ids": ["t1"]},
                    {"candidate_subject_id": "s-extra", "candidate_player_id": "A02", "tracklet_ids": ["t1"]},
                ]
            }
            (root / "identity_candidate_shadow.json").write_text(json.dumps(candidates))
            result = finalize_reviewed_identity(root, _match())
            row = {item["tracklet_id"]: item for item in result["tracklet_assignments"]}["t1"]
            self.assertEqual(row["identity_status"], "conflicted")
            self.assertIn("ambiguous_candidate_subject_membership", row["hard_blockers"])

    def test_exact_seed_overrides_only_its_detected_observation(self) -> None:
        with _workspace() as root:
            _write_inputs(root, decisions=[])
            tracklets = json.loads((root / "tracklets.json").read_text())
            tracklets["tracklets"][0]["positions_m"] = [
                {"frame": 0, "status": "detected"},
                {"frame": 1, "status": "detected"},
            ]
            (root / "tracklets.json").write_text(json.dumps(tracklets))
            seeds = {
                "decisions": [{
                    "observation_key": "o1",
                    "frame_number": 1,
                    "action": "assign_roster_player",
                    "assigned_team": {"team_label": "A"},
                    "assigned_player": {"player_id": "p1"},
                    "provenance": {"tracklet_id": "t1"},
                }]
            }
            (root / "identity_operator_seeds.json").write_text(json.dumps(seeds))
            result = finalize_reviewed_identity(root, _match())
            row = {item["tracklet_id"]: item for item in result["tracklet_assignments"]}["t1"]
            self.assertEqual(row["identity_status"], "unresolved")
            self.assertEqual(result["observation_overrides"][0]["display_label"], "Paweł")
            self.assertEqual(result["summary"]["exact_named_observations"], 1)
            self.assertEqual(result["summary"]["confirmed_detected_observations"], 1)


class _workspace:
    def __enter__(self) -> Path:
        self._temp = tempfile.TemporaryDirectory(); return Path(self._temp.name)
    def __exit__(self, *args: object) -> None: self._temp.cleanup()


def _match() -> dict:
    return {"id": "m1", "teams": [{"id": "ta", "players": [{"id": "p1", "name": "Paweł", "number": "8"}]}, {"id": "tb", "players": [{"id": "p2", "name": "Opponent"}]}]}
def _tracklet(tracklet_id: str, label: str, team_id: str, start: int) -> dict:
    return {"tracklet_id": tracklet_id, "team_label": label, "team_id": team_id, "start_frame": start, "end_frame": start + 2}
def _decision(subject: str, decision: str, player: str | None = None) -> dict:
    return {"candidate_subject_id": subject, "review_card_key": subject, "decision": decision, "player_id": player}
def _seed(subject: str, player: str) -> dict:
    return {"candidate_subject_id": subject, "assigned_player": {"player_id": player}, "propagation_provenance": {"team_consistency": True, "structural_gates_passed": True, "local_tracklet_continuity": True}, "seed_observations": []}
def _write_inputs(root: Path, *, decisions: list[dict], seeded: list[dict] | None = None) -> None:
    (root / "match.json").write_text(json.dumps(_match()))
    (root / "tracklets.json").write_text(json.dumps({"tracklets": [_tracklet("t1", "A", "ta", 0), _tracklet("t2", "A", "ta", 10)]}))
    (root / "identity_candidate_shadow.json").write_text(json.dumps({"subjects": [{"candidate_subject_id": "s1", "tracklet_ids": ["t1"]}, {"candidate_subject_id": "s2", "tracklet_ids": ["t2"]}]}))
    (root / "identity_roster_subject_review_decisions_shadow.json").write_text(json.dumps({"decisions": decisions}))
    (root / "identity_seeded_candidate_assignments.json").write_text(json.dumps({"accepted_assignments": seeded or []}))
