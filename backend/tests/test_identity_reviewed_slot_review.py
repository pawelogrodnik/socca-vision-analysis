from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app import main as app_main
from app.services.identity_initial_audit_store import production_identity_snapshot
from app.services.identity_reviewed_frame_uniqueness import build_frame_slot_demotions
from app.services.identity_reviewed_slot_registry import build_reviewed_slot_registry
from app.services.identity_reviewed_slot_review import save_reviewed_slot_assignments
from app.services.identity_stable_anonymous import resolve_stable_anonymous_entities


class ReviewedSlotReviewTests(unittest.TestCase):
    def test_api_returns_allocated_slot_and_reuses_it(self) -> None:
        with _workspace() as temporary:
            matches = temporary / "matches"
            root = matches / "m1"
            root.mkdir(parents=True)
            candidates, _ = _prepare(
                root,
                ("creator", "ta", "A", 1),
                ("fragment", "ta2", "A", 20),
                ("opponent", "tb", "B", 30),
                canonical_a=10,
            )
            _write(root / "identity_candidate_shadow.json", candidates)
            with patch.object(app_main, "MATCHES_DIR", matches):
                created = app_main.put_match_reviewed_slot_assignments(
                    "m1", {"updates": [_create("creator", "A")]}
                )
                created_decision = _decision(created, "creator")
                self.assertEqual(
                    {
                        key: created_decision[key]
                        for key in (
                            "candidate_subject_id",
                            "action",
                            "stable_slot_id",
                            "team_label",
                            "source",
                        )
                    },
                    {
                        "candidate_subject_id": "creator",
                        "action": "create_new_stable_player",
                        "stable_slot_id": "A11",
                        "team_label": "A",
                        "source": "manual_review",
                    },
                )
                reused = app_main.put_match_reviewed_slot_assignments(
                    "m1", {"updates": [_assign("fragment", "A11")]}
                )
                self.assertEqual(
                    _decision(reused, "fragment")["stable_slot_id"], "A11"
                )
                with self.assertRaises(HTTPException) as raised:
                    app_main.put_match_reviewed_slot_assignments(
                        "m1", {"updates": [_assign("opponent", "A11")]}
                    )
                self.assertEqual(raised.exception.status_code, 400)
                self.assertIn("team mismatch", str(raised.exception.detail))

    def test_manual_slot_persists_and_earlier_subject_does_not_renumber(self) -> None:
        with _workspace() as root:
            candidates, tracklets = _prepare(
                root,
                ("subject-20", "t20", "A", 20),
                ("subject-10", "t10", "A", 10),
                canonical_a=10,
            )
            production_before = production_identity_snapshot(root, {})
            global_before = (root / "global_identity.json").read_bytes()
            stable_before = (root / "stable_players.json").read_bytes()
            published = root / "published"
            published.mkdir(exist_ok=True)
            marker = published / "package.json"
            marker.write_text('{"unchanged": true}', encoding="utf-8")
            published_before = marker.read_bytes()

            first = save_reviewed_slot_assignments(
                root,
                candidates,
                [_create("subject-20", "A")],
            )
            self.assertEqual(_decision(first, "subject-20")["stable_slot_id"], "A11")
            first_resolved, _ = resolve_stable_anonymous_entities(
                root, tracklets, candidates, first
            )
            second_resolved, _ = resolve_stable_anonymous_entities(
                root, tracklets, candidates, first
            )
            self.assertEqual(first_resolved["t20"]["stable_anonymous_slot_id"], "A11")
            self.assertEqual(second_resolved["t20"]["stable_anonymous_slot_id"], "A11")

            second = save_reviewed_slot_assignments(
                root,
                candidates,
                [_create("subject-10", "A")],
            )
            self.assertEqual(_decision(second, "subject-20")["stable_slot_id"], "A11")
            self.assertEqual(_decision(second, "subject-10")["stable_slot_id"], "A12")
            final_resolved, diagnostics = resolve_stable_anonymous_entities(
                root, tracklets, candidates, second
            )
            self.assertEqual(final_resolved["t20"]["stable_anonymous_slot_id"], "A11")
            self.assertEqual(final_resolved["t10"]["stable_anonymous_slot_id"], "A12")
            self.assertEqual(diagnostics["manual_reviewed_slot_registry_entries"], 2)
            registry = build_reviewed_slot_registry(root, second)
            self.assertEqual(registry["A11"]["source"], "manual_new_player_confirmation")
            self.assertEqual(registry["A12"]["source"], "manual_new_player_confirmation")
            self.assertEqual(production_identity_snapshot(root, {}), production_before)
            self.assertEqual((root / "global_identity.json").read_bytes(), global_before)
            self.assertEqual((root / "stable_players.json").read_bytes(), stable_before)
            self.assertEqual(marker.read_bytes(), published_before)

    def test_three_non_overlapping_fragments_reuse_manual_slot(self) -> None:
        with _workspace() as root:
            candidates, tracklets = _prepare(
                root,
                ("s1", "t1", "A", 1),
                ("s2", "t2", "A", 10),
                ("s3", "t3", "A", 20),
                canonical_a=10,
            )
            document = save_reviewed_slot_assignments(
                root, candidates, [_create("s1", "A")]
            )
            document = save_reviewed_slot_assignments(
                root,
                candidates,
                [_assign("s2", "A11"), _assign("s3", "A11")],
            )
            resolved, _ = resolve_stable_anonymous_entities(
                root, tracklets, candidates, document
            )
            self.assertEqual(
                {row["stable_anonymous_slot_id"] for row in resolved.values()},
                {"A11"},
            )
            self.assertEqual(
                {row["fragment_id"] for row in resolved.values()},
                {"s1", "s2", "s3"},
            )

    def test_parallel_manual_slot_claims_are_demoted(self) -> None:
        with _workspace() as root:
            candidates, tracklets = _prepare(
                root,
                ("s1", "t1", "A", 10),
                ("s2", "t2", "A", 10),
                canonical_a=10,
            )
            document = save_reviewed_slot_assignments(
                root, candidates, [_create("s1", "A")]
            )
            document = save_reviewed_slot_assignments(
                root, candidates, [_assign("s2", "A11")]
            )
            resolved, _ = resolve_stable_anonymous_entities(
                root, tracklets, candidates, document
            )
            assignments = [
                {
                    "tracklet_id": tracklet_id,
                    "team_label": "A",
                    "stable_anonymous_slot_id": row["stable_anonymous_slot_id"],
                    "stable_anchor_source": row["stable_anchor_source"],
                    "identity_status": "unresolved",
                    "fallback_label": "A11",
                }
                for tracklet_id, row in resolved.items()
            ]
            demotions, diagnostics = build_frame_slot_demotions(
                tracklets, assignments
            )
            self.assertEqual({row["tracklet_id"] for row in demotions}, {"t1", "t2"})
            self.assertEqual(diagnostics["duplicate_stable_slot_claim_groups"], 1)
            self.assertEqual(diagnostics["duplicate_stable_labels_rendered"], 0)

    def test_manual_slot_team_mismatch_is_rejected_without_partial_write(self) -> None:
        with _workspace() as root:
            candidates, _ = _prepare(
                root,
                ("creator", "ta", "A", 1),
                ("opponent", "tb", "B", 20),
                canonical_a=10,
            )
            first = save_reviewed_slot_assignments(
                root, candidates, [_create("creator", "A")]
            )
            before = (root / "reviewed_identity_slot_assignments.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "team mismatch"):
                save_reviewed_slot_assignments(
                    root, candidates, [_assign("opponent", "A11")]
                )
            self.assertEqual(
                (root / "reviewed_identity_slot_assignments.json").read_bytes(),
                before,
            )
            self.assertNotIn(
                "opponent",
                {row["candidate_subject_id"] for row in first["decisions"]},
            )

    def test_assign_unknown_reviewed_slot_is_rejected(self) -> None:
        with _workspace() as root:
            candidates, _ = _prepare(
                root,
                ("s1", "t1", "A", 1),
                canonical_a=10,
            )
            with self.assertRaisesRegex(
                ValueError, "manual reviewed slot does not exist"
            ):
                save_reviewed_slot_assignments(
                    root, candidates, [_assign("s1", "A11")]
                )
            self.assertFalse(
                (root / "reviewed_identity_slot_assignments.json").exists()
            )

    def test_orphaned_manual_slot_is_preserved_and_not_recycled(self) -> None:
        with _workspace() as root:
            candidates, _ = _prepare(
                root,
                ("s1", "t1", "A", 1),
                ("s2", "t2", "A", 10),
                ("s3", "t3", "A", 20),
                ("s0", "t0", "A", 30),
                canonical_a=10,
            )
            document = save_reviewed_slot_assignments(
                root, candidates, [_create("s1", "A")]
            )
            document = save_reviewed_slot_assignments(
                root, candidates, [_assign("s2", "A11"), _assign("s3", "A11")]
            )
            document = save_reviewed_slot_assignments(
                root, candidates, [{"candidate_subject_id": "s1", "action": "referee"}]
            )
            self.assertEqual(_reviewed_slot(document, "A11")["status"], "active")
            document = save_reviewed_slot_assignments(
                root,
                candidates,
                [
                    {"candidate_subject_id": "s2", "action": "unresolved"},
                    {"candidate_subject_id": "s3", "action": "unresolved"},
                ],
            )
            self.assertEqual(_reviewed_slot(document, "A11")["status"], "orphaned")
            document = save_reviewed_slot_assignments(
                root, candidates, [_create("s0", "A")]
            )
            self.assertEqual(_decision(document, "s0")["stable_slot_id"], "A12")

    def test_identical_save_preserves_slot_and_review_timestamp(self) -> None:
        with _workspace() as root:
            candidates, _ = _prepare(
                root,
                ("s1", "t1", "A", 1),
                canonical_a=10,
            )
            first = save_reviewed_slot_assignments(
                root, candidates, [_create("s1", "A")]
            )
            second = save_reviewed_slot_assignments(
                root, candidates, [_create("s1", "A")]
            )
            self.assertEqual(_decision(first, "s1"), _decision(second, "s1"))
            self.assertEqual(first["reviewed_slots"], second["reviewed_slots"])

    def test_legacy_unallocated_decision_is_persisted_before_new_allocation(self) -> None:
        with _workspace() as root:
            candidates, _ = _prepare(
                root,
                ("subject-20", "t20", "A", 20),
                ("subject-10", "t10", "A", 10),
                canonical_a=10,
            )
            _write(
                root / "reviewed_identity_slot_assignments.json",
                {
                    "schema_version": "1.0.0",
                    "mode": "reviewed_identity_slot_assignments",
                    "decisions": [
                        {
                            "candidate_subject_id": "subject-20",
                            "action": "create_new_stable_player",
                            "stable_slot_id": None,
                            "team_label": "A",
                            "source": "manual_review",
                            "reviewed_at": "2026-08-01T10:00:00+00:00",
                        }
                    ],
                },
            )
            document = save_reviewed_slot_assignments(
                root, candidates, [_create("subject-10", "A")]
            )
            self.assertEqual(
                {
                    row["candidate_subject_id"]: row["stable_slot_id"]
                    for row in document["decisions"]
                },
                {"subject-10": "A12", "subject-20": "A11"},
            )

    def test_ambiguous_and_mixed_team_subjects_are_rejected(self) -> None:
        with _workspace() as root:
            candidates, _ = _prepare(
                root,
                ("ambiguous", "shared", "A", 1),
                ("other", "shared", "A", 1),
                ("mixed", "a", "A", 10),
                canonical_a=10,
            )
            candidates["subjects"].append(
                {"candidate_subject_id": "mixed", "tracklet_ids": ["b"]}
            )
            tracklets = json.loads((root / "tracklets.json").read_text(encoding="utf-8"))
            tracklets["tracklets"].append(_tracklet("b", "B", 11))
            _write(root / "tracklets.json", tracklets)
            with self.assertRaisesRegex(ValueError, "ambiguous subject"):
                save_reviewed_slot_assignments(
                    root, candidates, [_create("ambiguous", "A")]
                )
            with self.assertRaisesRegex(ValueError, "mixed-team subject"):
                save_reviewed_slot_assignments(
                    root, candidates, [_create("mixed", "A")]
                )

    def test_unknown_team_does_not_make_subject_mixed(self) -> None:
        with _workspace() as root:
            _, _ = _prepare(
                root,
                ("a", "a", "A", 1),
                ("a-u", "u-a", "U", 2),
                ("b", "b", "B", 3),
                ("b-u", "u-b", "U", 4),
                ("mixed-a", "mixed-a", "A", 5),
                ("mixed-b", "mixed-b", "B", 6),
                canonical_a=10,
            )
            candidates = {
                "subjects": [
                    {"candidate_subject_id": "subject-a-u", "tracklet_ids": ["a", "u-a"]},
                    {"candidate_subject_id": "subject-b-u", "tracklet_ids": ["b", "u-b"]},
                    {
                        "candidate_subject_id": "subject-a-b",
                        "tracklet_ids": ["mixed-a", "mixed-b"],
                    },
                ]
            }
            saved = save_reviewed_slot_assignments(
                root,
                candidates,
                [
                    {
                        "candidate_subject_id": "subject-a-u",
                        "action": "assign_team",
                        "team_label": "A",
                    },
                    {
                        "candidate_subject_id": "subject-b-u",
                        "action": "assign_team",
                        "team_label": "B",
                    },
                ],
            )
            self.assertEqual(
                {
                    row["candidate_subject_id"]
                    for row in saved["decisions"]
                },
                {"subject-a-u", "subject-b-u"},
            )
            with self.assertRaisesRegex(ValueError, "mixed-team subject"):
                save_reviewed_slot_assignments(
                    root,
                    candidates,
                    [
                        {
                            "candidate_subject_id": "subject-a-b",
                            "action": "assign_team",
                            "team_label": "A",
                        }
                    ],
                )


def _prepare(
    root: Path,
    *subjects: tuple[str, str, str, int],
    canonical_a: int,
) -> tuple[dict, dict[str, dict]]:
    slots = [
        _slot(f"A{number:02d}", f"canonical-{number}")
        for number in range(1, canonical_a + 1)
    ]
    _write(root / "global_identity.json", {"slots": slots})
    _write(root / "stable_players.json", {"players": []})
    candidates = {
        "subjects": [
            {"candidate_subject_id": subject, "tracklet_ids": [tracklet_id]}
            for subject, tracklet_id, _, _ in subjects
        ]
    }
    tracklets = {
        tracklet_id: _tracklet(tracklet_id, team, frame)
        for _, tracklet_id, team, frame in subjects
    }
    _write(root / "tracklets.json", {"tracklets": list(tracklets.values())})
    return candidates, tracklets


def _create(subject_id: str, team: str) -> dict:
    return {
        "candidate_subject_id": subject_id,
        "action": "create_new_stable_player",
        "team_label": team,
    }


def _assign(subject_id: str, slot_id: str) -> dict:
    return {
        "candidate_subject_id": subject_id,
        "action": "assign_existing_slot",
        "stable_slot_id": slot_id,
    }


def _decision(document: dict, subject_id: str) -> dict:
    return next(
        row
        for row in document["decisions"]
        if row["candidate_subject_id"] == subject_id
    )


def _reviewed_slot(document: dict, slot_id: str) -> dict:
    return next(
        row for row in document["reviewed_slots"] if row["stable_slot_id"] == slot_id
    )


def _tracklet(tracklet_id: str, team: str, frame: int) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "team_label": team,
        "positions_m": [{"frame": frame, "status": "detected"}],
    }


def _slot(slot_id: str, tracklet_id: str) -> dict:
    return {
        "slot_id": slot_id,
        "stable_player_id": slot_id,
        "team_label": slot_id[0],
        "tracklet_ids": [tracklet_id],
    }


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


class _workspace:
    def __enter__(self) -> Path:
        self._temporary = tempfile.TemporaryDirectory()
        return Path(self._temporary.name)

    def __exit__(self, *args: object) -> None:
        self._temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
