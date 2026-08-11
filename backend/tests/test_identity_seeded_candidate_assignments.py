from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.services.identity_initial_audit import (
    AUDIT_DIRECTORY,
    SELECTION_FILENAME,
)
from app.services.identity_initial_audit_store import SEEDS_FILENAME
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_seeded_candidate_assignments import (
    CANDIDATE_FILENAME,
    OUTPUT_FILENAME,
    TIMELINE_FILENAME,
    build_identity_seeded_candidate_assignments,
    rebuild_identity_seeded_candidate_assignments,
)
from app.services.identity_seeded_review_reduction import load_fresh_seeded_assignments


class IdentitySeededCandidateAssignmentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.generated_at = "2026-07-27T12:00:00+00:00"
        self.candidate_document = {
            "subjects": [
                self._candidate("subject-a", "A", "tracklet-a"),
                self._candidate("subject-b", "A", "tracklet-b"),
                self._candidate("subject-c", "B", "tracklet-c"),
                self._candidate(
                    "subject-blocked",
                    "A",
                    "tracklet-blocked",
                    quality_flags=["uncertain_transition"],
                ),
            ]
        }
        self.timeline_document = {
            "subjects": [
                self._timeline_subject(
                    "subject-a",
                    [
                        self._observation(30, "tracklet-a"),
                        self._observation(31, "tracklet-a"),
                    ],
                ),
                self._timeline_subject(
                    "subject-b",
                    [
                        self._observation(30, "tracklet-b"),
                        self._observation(32, "tracklet-b"),
                    ],
                ),
                self._timeline_subject(
                    "subject-c",
                    [self._observation(40, "tracklet-c")],
                ),
                self._timeline_subject(
                    "subject-blocked",
                    [self._observation(50, "tracklet-blocked")],
                ),
            ]
        }

    def _candidate(
        self,
        subject_id: str,
        team_label: str,
        tracklet_id: str,
        *,
        quality_flags: list[str] | None = None,
    ) -> dict[str, object]:
        return {
            "candidate_subject_id": subject_id,
            "candidate_player_id": f"candidate-{subject_id}",
            "team_label": team_label,
            "role": "field_player",
            "tracklet_ids": [tracklet_id],
            "start_frame": 1,
            "end_frame": 100,
            "quality_flags": quality_flags or [],
        }

    def _timeline_subject(
        self,
        subject_id: str,
        observations: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "shadow_subject_id": subject_id,
            "observations": observations,
        }

    def _observation(
        self,
        frame: int,
        tracklet_id: str,
        *,
        status: str = "detected",
    ) -> dict[str, object]:
        return {
            "frame": frame,
            "tracklet_id": tracklet_id,
            "status": status,
        }

    def _decision(
        self,
        observation_key: str,
        frame_number: int,
        tracklet_id: str,
        *,
        player_id: str = "player-1",
        player_name: str = "Pawel",
        team_label: str = "A",
        action: str = "assign_roster_player",
    ) -> dict[str, object]:
        assigned_player = None
        assigned_team = None
        if action == "assign_roster_player":
            assigned_player = {
                "player_id": player_id,
                "name": player_name,
            }
            assigned_team = {"team_label": team_label}
        return {
            "observation_key": observation_key,
            "frame_number": frame_number,
            "display_order": 0,
            "action": action,
            "assigned_player": assigned_player,
            "assigned_team": assigned_team,
            "provenance": {"tracklet_id": tracklet_id},
        }

    def _seeds(
        self,
        decisions: list[dict[str, object]],
        *,
        selection_artifact_digest: str = "selection-digest",
    ) -> dict[str, object]:
        return {
            "updated_at": self.generated_at,
            "source": {
                "selection_digest": "selection-1",
                "selection_artifact_digest": selection_artifact_digest,
            },
            "decisions": decisions,
        }

    def _build(
        self,
        decisions: list[dict[str, object]],
    ) -> dict[str, object]:
        return build_identity_seeded_candidate_assignments(
            self._seeds(decisions),
            self.candidate_document,
            self.timeline_document,
            generated_at=self.generated_at,
        )

    def test_accepts_exact_same_team_tracklet_lineage(self) -> None:
        document = self._build(
            [self._decision("observation-a", 30, "tracklet-a")]
        )

        self.assertEqual(
            [row["candidate_subject_id"] for row in document[
                "accepted_assignments"
            ]],
            ["subject-a"],
        )
        self.assertEqual(
            document["accepted_assignments"][0]["assigned_player"][
                "player_id"
            ],
            "player-1",
        )
        self.assertEqual(
            document["summary"]["subjects_resolved_after_seeding"],
            1,
        )
        self.assertFalse(document["safety"]["mutates_production_identity"])
        self.assertFalse(document["safety"]["eligible_for_player_stats"])
        self.assertEqual(document["safety"]["cross_team_links"], 0)

    def test_rejects_cross_team_propagation_and_keeps_subject_lineage(
        self,
    ) -> None:
        document = self._build(
            [self._decision("observation-c", 40, "tracklet-c")]
        )

        self.assertEqual(document["accepted_assignments"], [])
        rejection = document["rejected_propagations"][0]
        self.assertEqual(rejection["candidate_subject_ids"], ["subject-c"])
        self.assertIn(
            "candidate_team_conflicts_operator_team",
            rejection["reasons"],
        )
        unresolved = {
            row["candidate_subject_id"]: row
            for row in document["unresolved_subjects"]
        }
        self.assertIn(
            "candidate_team_conflicts_operator_team",
            unresolved["subject-c"]["reasons"],
        )

    def test_rejects_hard_structural_blocker(self) -> None:
        document = self._build(
            [
                self._decision(
                    "observation-blocked",
                    50,
                    "tracklet-blocked",
                )
            ]
        )

        self.assertEqual(document["accepted_assignments"], [])
        self.assertEqual(
            document["rejected_propagations"][0]["candidate_subject_ids"],
            ["subject-blocked"],
        )
        self.assertIn(
            "structural_blocker:uncertain_transition",
            document["rejected_propagations"][0]["reasons"],
        )

    def test_conflicting_players_for_one_subject_remain_unresolved(
        self,
    ) -> None:
        document = self._build(
            [
                self._decision("observation-a-1", 30, "tracklet-a"),
                self._decision(
                    "observation-a-2",
                    31,
                    "tracklet-a",
                    player_id="player-2",
                    player_name="Krzysiek",
                ),
            ]
        )

        self.assertEqual(document["accepted_assignments"], [])
        self.assertEqual(
            document["conflicts"][0]["conflict_type"],
            "multiple_players_for_candidate_subject",
        )
        self.assertEqual(document["summary"]["conflicts_created"], 1)

    def test_parallel_subjects_for_one_player_are_blocked(self) -> None:
        document = self._build(
            [
                self._decision("observation-a", 30, "tracklet-a"),
                self._decision("observation-b", 30, "tracklet-b"),
            ]
        )

        self.assertEqual(document["accepted_assignments"], [])
        self.assertEqual(
            document["conflicts"][0]["conflict_type"],
            "parallel_same_player",
        )
        self.assertEqual(
            document["safety"]["impossible_parallel_assignments"],
            0,
        )
        self.assertEqual(
            document["safety"]["parallel_assignment_conflicts_detected"],
            1,
        )
        self.assertEqual(
            document["safety"]["parallel_assignment_conflicts_blocked"],
            1,
        )

    def test_build_is_deterministic_for_the_same_inputs(self) -> None:
        decisions = [self._decision("observation-a", 30, "tracklet-a")]

        first = self._build(decisions)
        second = self._build(decisions)

        self.assertEqual(first, second)

    def test_rebuild_is_atomic_and_keeps_production_identity_unchanged(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            match_path = Path(directory)
            selection_path = (
                match_path / AUDIT_DIRECTORY / SELECTION_FILENAME
            )
            selection_path.parent.mkdir(parents=True)
            selection = {"selection_digest": "selection-1"}
            self._write_json(selection_path, selection)
            self._write_json(
                match_path / SEEDS_FILENAME,
                self._seeds(
                    [self._decision("observation-a", 30, "tracklet-a")],
                    selection_artifact_digest=canonical_digest(selection),
                ),
            )
            self._write_json(
                match_path / CANDIDATE_FILENAME,
                self.candidate_document,
            )
            self._write_json(
                match_path / TIMELINE_FILENAME,
                self.timeline_document,
            )
            production_path = match_path / "global_identity.json"
            production_path.write_text(
                '{"stable_subjects":["production-a"]}\n',
                encoding="utf-8",
            )
            production_before = production_path.read_bytes()

            first = rebuild_identity_seeded_candidate_assignments(
                match_path,
                {},
            )
            review_decisions_path = (
                match_path
                / "identity_roster_subject_review_decisions_shadow.json"
            )
            self._write_json(review_decisions_path, {"decisions": [{"id": "one"}]})
            still_fresh, freshness = load_fresh_seeded_assignments(match_path)
            self.assertIsNotNone(still_fresh)
            self.assertEqual(freshness["status"], "fresh")
            second = rebuild_identity_seeded_candidate_assignments(
                match_path,
                {},
            )
            refreshed, freshness = load_fresh_seeded_assignments(match_path)

            self.assertNotEqual(first["source"], second["source"])
            self.assertEqual(
                first["accepted_assignments"],
                second["accepted_assignments"],
            )
            self.assertEqual(
                first["rejected_propagations"],
                second["rejected_propagations"],
            )
            self.assertIsNotNone(refreshed)
            self.assertEqual(freshness["status"], "fresh")
            self.assertEqual(production_path.read_bytes(), production_before)
            self.assertTrue((match_path / OUTPUT_FILENAME).exists())
            self.assertEqual(
                list(match_path.glob(f".{OUTPUT_FILENAME}.*.tmp")),
                [],
            )

    def _write_json(self, path: Path, document: dict[str, object]) -> None:
        path.write_text(
            json.dumps(document, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
