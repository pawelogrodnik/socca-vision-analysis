from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.services.identity_initial_audit_store import SEEDS_FILENAME
from app.services.identity_initial_audit import (
    AUDIT_DIRECTORY,
    SELECTION_FILENAME,
)
from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_operator_seed_digest import (
    identity_operator_seed_decisions_digest,
)
from app.services.identity_seeded_candidate_assignments import OUTPUT_FILENAME
from app.services.identity_seeded_candidate_assignments import (
    load_combined_operator_seeds,
)
from app.services.identity_seeded_review_reduction import (
    apply_identity_seeded_review_reduction,
    load_fresh_seeded_assignments,
)


def review_card(
    subject_id: str,
    *,
    start_frame: int,
    end_frame: int,
) -> dict:
    options = [
        {"player_id": "p1", "player_name": "One"},
        {"player_id": "p2", "player_name": "Two"},
    ]
    return {
        "review_card_key": f"card-{subject_id}",
        "candidate_subject_id": subject_id,
        "review_status": "ready_for_operator_review",
        "start_frame": start_frame,
        "end_frame": end_frame,
        "recommended_player": options[0],
        "roster_candidates": options,
        "operator_roster_options": options,
        "reason_codes": [],
        "blockers": [],
        "allowed_actions": [
            "confirm_recommended_player",
            "assign_roster_player",
            "mark_unresolved",
            "open_debug_context",
        ],
        "decision_contract": {"decision_schema": {"player_id": ["p1", "p2"]}},
    }


def accepted_assignment(
    subject_id: str = "subject-1",
    *,
    player_id: str = "p1",
    start_frame: int = 10,
    end_frame: int = 100,
) -> dict:
    return {
        "candidate_subject_id": subject_id,
        "start_frame": start_frame,
        "end_frame": end_frame,
        "tracklet_ids": ["tracklet-1"],
        "assigned_player": {
            "player_id": player_id,
            "name": "One" if player_id == "p1" else "Two",
            "team_label": "A",
        },
        "seed_observations": ["observation-1"],
        "reason_codes": ["safe_seeded_subject_assignment"],
    }


def seeded_document(
    accepted: list[dict] | None = None,
    conflicts: list[dict] | None = None,
) -> dict:
    accepted_rows = accepted or []
    conflict_rows = conflicts or []
    return {
        "schema_version": "0.1.0",
        "accepted_assignments": accepted_rows,
        "conflicts": conflict_rows,
        "summary": {
            "subjects_resolved_after_seeding": len(accepted_rows),
            "tracklets_resolved_after_seeding": len(accepted_rows),
            "frames_resolved_after_seeding": sum(
                int(row["end_frame"]) - int(row["start_frame"]) + 1
                for row in accepted_rows
            ),
            "conflicts_created": len(conflict_rows),
        },
        "safety": {"production_identity_untouched": True},
    }


class IdentitySeededReviewReductionTests(unittest.TestCase):
    def test_accepted_seed_completes_matching_subject(self) -> None:
        cards, report = apply_identity_seeded_review_reduction(
            [
                review_card("subject-1", start_frame=10, end_frame=100),
                review_card("subject-2", start_frame=110, end_frame=180),
            ],
            seeded_document([accepted_assignment()]),
            freshness={"status": "fresh", "reason_codes": []},
        )

        completed = next(
            card for card in cards if card["candidate_subject_id"] == "subject-1"
        )
        self.assertEqual(completed["review_status"], "completed_by_initial_audit")
        self.assertFalse(completed["requires_operator_review"])
        self.assertEqual(
            completed["recommended_player"]["source"],
            "initial_identity_audit",
        )
        self.assertEqual(report["metrics"]["review_cards_before_seeding"], 2)
        self.assertEqual(report["metrics"]["review_cards_after_seeding"], 1)
        self.assertEqual(report["metrics"]["review_cards_reduced"], 1)

    def test_seeded_player_is_blocked_only_during_overlapping_interval(self) -> None:
        cards, report = apply_identity_seeded_review_reduction(
            [
                review_card("subject-2", start_frame=50, end_frame=80),
                review_card("subject-3", start_frame=101, end_frame=140),
            ],
            seeded_document([accepted_assignment(end_frame=100)]),
            freshness={"status": "fresh", "reason_codes": []},
        )

        overlapping = next(
            card for card in cards if card["candidate_subject_id"] == "subject-2"
        )
        later = next(
            card for card in cards if card["candidate_subject_id"] == "subject-3"
        )
        self.assertEqual(
            [row["player_id"] for row in overlapping["operator_roster_options"]],
            ["p2"],
        )
        self.assertEqual(overlapping["overlapping_seeded_player_ids"], ["p1"])
        self.assertEqual(
            [row["player_id"] for row in later["operator_roster_options"]],
            ["p1", "p2"],
        )
        self.assertEqual(
            report["metrics"]["blocked_overlapping_player_options"],
            1,
        )

    def test_manual_contradiction_remains_visible_as_conflict(self) -> None:
        card = review_card("subject-1", start_frame=10, end_frame=100)
        card["operator_decision"] = {
            "decision": "assign_roster_player",
            "player_id": "p2",
        }

        cards, report = apply_identity_seeded_review_reduction(
            [card],
            seeded_document([accepted_assignment()]),
            freshness={"status": "fresh", "reason_codes": []},
        )

        self.assertEqual(cards[0]["review_status"], "blocked_seed_conflict")
        self.assertTrue(cards[0]["requires_operator_review"])
        self.assertIn("assign_roster_player", cards[0]["allowed_actions"])
        self.assertEqual(report["metrics"]["false_assignments_found"], 1)

    def test_explicit_seed_conflict_is_prioritized_for_review(self) -> None:
        conflict = {
            "candidate_subject_ids": ["subject-2"],
            "reason_codes": ["seed_observations_disagree"],
        }
        cards, _ = apply_identity_seeded_review_reduction(
            [
                review_card("subject-1", start_frame=10, end_frame=20),
                review_card("subject-2", start_frame=30, end_frame=40),
            ],
            seeded_document(conflicts=[conflict]),
            freshness={"status": "fresh", "reason_codes": []},
        )

        self.assertEqual(cards[0]["candidate_subject_id"], "subject-2")
        self.assertEqual(cards[0]["review_status"], "blocked_seed_conflict")

    def test_missing_seed_artifacts_preserve_existing_review(self) -> None:
        card = review_card("subject-1", start_frame=10, end_frame=100)

        cards, report = apply_identity_seeded_review_reduction(
            [card],
            None,
            freshness={"status": "missing", "reason_codes": ["missing"]},
        )

        self.assertEqual(cards, [card])
        self.assertEqual(report["status"], "missing")
        self.assertEqual(report["metrics"]["review_cards_reduced"], 0)

    def test_freshness_loader_rejects_stale_seed_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            seeds = {"schema_version": "0.1.0", "decisions": []}
            seeded = seeded_document([accepted_assignment()])
            seeded["source"] = {
                "operator_seed_decisions_digest": (
                    identity_operator_seed_decisions_digest(seeds)
                ),
                **_fresh_artifact_digests(),
            }
            (path / SEEDS_FILENAME).write_text(json.dumps(seeds), encoding="utf-8")
            (path / OUTPUT_FILENAME).write_text(json.dumps(seeded), encoding="utf-8")

            loaded, freshness = load_fresh_seeded_assignments(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(freshness["status"], "fresh")

            seeds["operator_telemetry"] = {
                "active_operator_seconds": 42.0,
                "events": [{"event_type": "session_finished"}],
            }
            seeds["updated_at"] = "2026-07-27T14:00:00+00:00"
            (path / SEEDS_FILENAME).write_text(
                json.dumps(seeds),
                encoding="utf-8",
            )
            loaded, freshness = load_fresh_seeded_assignments(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(freshness["status"], "fresh")

            seeds["decisions"].append({"observation_id": "new"})
            (path / SEEDS_FILENAME).write_text(json.dumps(seeds), encoding="utf-8")
            loaded, freshness = load_fresh_seeded_assignments(path)

            self.assertIsNone(loaded)
            self.assertEqual(freshness["status"], "stale")

    def test_freshness_uses_canonical_combined_audit_seeds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            selection = {"schema_version": "0.1.0", "frames": []}
            selection_path = path / AUDIT_DIRECTORY / SELECTION_FILENAME
            selection_path.parent.mkdir(parents=True)
            selection_path.write_text(json.dumps(selection), encoding="utf-8")
            seeds = {
                "schema_version": "0.1.0",
                "mode": "initial_identity_audit",
                "source": {
                    "selection_digest": "selection-digest",
                    "selection_artifact_digest": canonical_digest(selection),
                },
                "decisions": [
                    {
                        "observation_key": "frame-1-tracklet-1",
                        "frame_number": 1,
                        "action": "assign_roster_player",
                    }
                ],
            }
            (path / SEEDS_FILENAME).write_text(json.dumps(seeds), encoding="utf-8")
            combined = load_combined_operator_seeds(path)
            seeded = seeded_document([accepted_assignment()])
            seeded["source"] = {
                "operator_seed_decisions_digest": (
                    identity_operator_seed_decisions_digest(combined)
                ),
                **_fresh_artifact_digests(),
            }
            (path / OUTPUT_FILENAME).write_text(
                json.dumps(seeded), encoding="utf-8"
            )

            loaded, freshness = load_fresh_seeded_assignments(path)

            self.assertIsNotNone(loaded)
            self.assertEqual(freshness["status"], "fresh")

    def test_freshness_accepts_reanchor_only_operator_seeds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            match = {
                "benchmark_session": {
                    "reanchor_only": True,
                }
            }
            (path / "match.json").write_text(
                json.dumps(match),
                encoding="utf-8",
            )
            reanchor_directory = path / "identity_second_half_reanchor"
            reanchor_directory.mkdir()
            selection = {"schema_version": "0.1.0", "selected_frames": []}
            (reanchor_directory / "identity_second_half_reanchor_selection.json").write_text(
                json.dumps(selection),
                encoding="utf-8",
            )
            seeds = {
                "schema_version": "0.1.0",
                "source": {
                    "selection_digest": "selection-h2",
                    "selection_artifact_digest": canonical_digest(selection),
                },
                "decisions": [
                    {
                        "observation_key": "frame-h2-tracklet-1",
                        "frame_number": 200,
                        "action": "false_detection",
                    }
                ],
            }
            (
                reanchor_directory
                / "identity_second_half_reanchor_seeds.json"
            ).write_text(json.dumps(seeds), encoding="utf-8")
            combined = load_combined_operator_seeds(path)
            seeded = seeded_document([accepted_assignment()])
            seeded["source"] = {
                "operator_seed_decisions_digest": (
                    identity_operator_seed_decisions_digest(combined)
                ),
                **_fresh_artifact_digests(),
            }
            (path / OUTPUT_FILENAME).write_text(
                json.dumps(seeded),
                encoding="utf-8",
            )

            loaded, freshness = load_fresh_seeded_assignments(path)

            self.assertIsNotNone(loaded)
            self.assertEqual(freshness["status"], "fresh")

    def test_freshness_rejects_changed_candidate_or_timeline_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            seeds = {"schema_version": "0.1.0", "decisions": []}
            candidate = {"subjects": []}
            timeline = {"subjects": []}
            seeded = seeded_document([accepted_assignment()])
            seeded["source"] = {
                "operator_seed_decisions_digest": identity_operator_seed_decisions_digest(seeds),
                "candidate_identity_digest": canonical_digest(candidate),
                "timeline_digest": canonical_digest(timeline),
                "tracklets_digest": canonical_digest({}),
                "whole_subject_review_decisions_digest": canonical_digest({}),
            }
            (path / SEEDS_FILENAME).write_text(json.dumps(seeds), encoding="utf-8")
            (path / OUTPUT_FILENAME).write_text(json.dumps(seeded), encoding="utf-8")
            (path / "identity_candidate_shadow.json").write_text(json.dumps(candidate), encoding="utf-8")
            (path / "identity_offline_shadow_timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
            loaded, freshness = load_fresh_seeded_assignments(path)
            self.assertIsNotNone(loaded)
            self.assertEqual(freshness["status"], "fresh")
            candidate["subjects"].append({"candidate_subject_id": "changed"})
            (path / "identity_candidate_shadow.json").write_text(json.dumps(candidate), encoding="utf-8")
            loaded, freshness = load_fresh_seeded_assignments(path)
            self.assertIsNone(loaded)
            self.assertIn("candidate_identity_digest_mismatch", freshness["reason_codes"])

    def test_freshness_rejects_changed_tracklets_or_review_decisions(self) -> None:
        for filename, source_key in (
            ("tracklets.json", "tracklets_digest"),
            (
                "identity_roster_subject_review_decisions_shadow.json",
                "whole_subject_review_decisions_digest",
            ),
        ):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary)
                seeds = {"schema_version": "0.1.0", "decisions": []}
                source_artifact = {"rows": []}
                seeded = seeded_document([accepted_assignment()])
                seeded["source"] = {
                    "operator_seed_decisions_digest": identity_operator_seed_decisions_digest(seeds),
                    "candidate_identity_digest": canonical_digest({}),
                    "timeline_digest": canonical_digest({}),
                    "tracklets_digest": canonical_digest({}),
                    "whole_subject_review_decisions_digest": canonical_digest({}),
                    source_key: canonical_digest(source_artifact),
                }
                (path / SEEDS_FILENAME).write_text(json.dumps(seeds), encoding="utf-8")
                (path / OUTPUT_FILENAME).write_text(json.dumps(seeded), encoding="utf-8")
                (path / filename).write_text(json.dumps(source_artifact), encoding="utf-8")
                loaded, freshness = load_fresh_seeded_assignments(path)
                self.assertIsNotNone(loaded)
                self.assertEqual(freshness["status"], "fresh")
                source_artifact["rows"].append({"changed": True})
                (path / filename).write_text(json.dumps(source_artifact), encoding="utf-8")
                loaded, freshness = load_fresh_seeded_assignments(path)
                self.assertIsNone(loaded)
                self.assertIn(f"{source_key}_mismatch", freshness["reason_codes"])


def _fresh_artifact_digests() -> dict[str, str]:
    return {
        "candidate_identity_digest": canonical_digest({}),
        "timeline_digest": canonical_digest({}),
        "tracklets_digest": canonical_digest({}),
        "whole_subject_review_decisions_digest": canonical_digest({}),
    }


if __name__ == "__main__":
    unittest.main()
