from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.identity_reviewed_corrections import (
    reviewed_correction_context,
    save_reviewed_identity_correction,
)
from app.services.identity_reviewed_snapshot import (
    finalize_reviewed_identity,
    get_reviewed_identity_status,
    reviewed_assignment_at,
)
from app.services.identity_reviewed_stats import build_reviewed_stats


class ReviewedIdentityCorrectionTests(unittest.TestCase):
    def test_structural_and_action_validation_are_order_independent(self) -> None:
        with _workspace() as root:
            _fixture(root)
            before = _decision_files(root)
            with self.assertRaisesRegex(ValueError, "Unknown candidate_subject_id"):
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {"candidate_subject_id": "missing", "action": "unresolved"},
                )
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {"candidate_subject_id": "s1", "action": "guess"},
                )
            candidates = _load(root / "identity_candidate_shadow.json")
            candidates["subjects"].append(
                {"candidate_subject_id": "another", "tracklet_ids": ["t1"]}
            )
            _write(root / "identity_candidate_shadow.json", candidates)
            with self.assertRaisesRegex(ValueError, "Ambiguous candidate subject"):
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {"candidate_subject_id": "s1", "action": "unresolved"},
                )
            self.assertEqual(_decision_files(root), before)

        with _workspace() as root:
            _fixture(root)
            candidates = _load(root / "identity_candidate_shadow.json")
            candidates["subjects"].append(
                {"candidate_subject_id": "s1", "tracklet_ids": ["t3"]}
            )
            _write(root / "identity_candidate_shadow.json", candidates)
            tracklets = _load(root / "tracklets.json")
            tracklets["tracklets"].append(
                {
                    "tracklet_id": "t3",
                    "team_label": "B",
                    "positions_m": [{"frame": 30, "status": "detected"}],
                }
            )
            _write(root / "tracklets.json", tracklets)
            before = _decision_files(root)
            with self.assertRaisesRegex(ValueError, "Mixed-team"):
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {"candidate_subject_id": "s1", "action": "unresolved"},
                )
            self.assertEqual(_decision_files(root), before)

    def test_roster_assignment_is_whole_subject_and_comment_is_nonsemantic(self) -> None:
        with _workspace() as root:
            _fixture(root)
            baseline = finalize_reviewed_identity(root, _match())
            first = save_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": "s1",
                    "action": "assign_roster_player",
                    "player_id": "p1",
                },
            )
            self.assertTrue(first["snapshot"]["stale"])
            self.assertEqual(get_reviewed_identity_status(root)["status"], "stale")
            same = save_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": "s1",
                    "action": "assign_roster_player",
                    "player_id": "p1",
                    "comment": "non-semantic note",
                },
            )
            self.assertEqual(
                first["semantic_decision_digest"],
                same["semantic_decision_digest"],
            )
            result = finalize_reviewed_identity(root, _match())
            self.assertNotEqual(baseline["semantic_digest"], result["semantic_digest"])
            rows = [
                row
                for row in result["tracklet_assignments"]
                if row["candidate_subject_id"] == "s1"
            ]
            self.assertEqual({row["identity_status"] for row in rows}, {"confirmed"})
            self.assertEqual({row["canonical_player_id"] for row in rows}, {"p1"})
            self.assertEqual(result["summary"]["confirmed_detected_observations"], 4)
            with patch(
                "app.services.identity_reviewed_stats.read_match_video_metadata",
                return_value={
                    "fps": 10.0,
                    "frame_count": 100,
                    "duration_sec": 10.0,
                    "source": "fixture",
                    "filename": "fixture.mp4",
                },
            ):
                stats = build_reviewed_stats(root, result, _match())
            player = stats["reviewed_player_stats.json"]["players"][0]
            self.assertEqual(player["confirmed_detected_observations"], 4)
            self.assertGreater(player["observed_distance_m"], 0)
            self.assertEqual(player["heatmap_samples"], 4)

    def test_roster_assignment_persists_safe_canonical_slot_binding(self) -> None:
        with _workspace() as root:
            _fixture(root)
            global_identity = _load(root / "global_identity.json")
            next(
                row
                for row in global_identity["slots"]
                if row["stable_player_id"] == "A03"
            )["tracklet_ids"] = ["t1", "t1b"]
            _write(root / "global_identity.json", global_identity)
            save_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": "s1",
                    "action": "assign_roster_player",
                    "player_id": "p1",
                },
            )
            decision = next(
                row
                for row in _load(
                    root / "reviewed_identity_slot_assignments.json"
                )["decisions"]
                if row["candidate_subject_id"] == "s1"
            )
            self.assertEqual(decision["stable_slot_id"], "A03")

    def test_cross_team_roster_assignment_rejects_without_decision_write(self) -> None:
        with _workspace() as root:
            _fixture(root)
            before = _decision_files(root)
            with self.assertRaisesRegex(ValueError, "Cross-team"):
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": "s1",
                        "action": "assign_roster_player",
                        "player_id": "p2",
                    },
                )
            self.assertEqual(_decision_files(root), before)

    def test_unknown_team_assignment_is_reviewed_only_and_deterministic(self) -> None:
        with _workspace() as root:
            _fixture(root)
            baseline = finalize_reviewed_identity(root, _match())
            immutable_before = _immutable_identity_files(root)
            first = save_reviewed_identity_correction(
                root,
                _match(),
                {"candidate_subject_id": "su", "action": "assign_team", "team_label": "B"},
            )
            same = save_reviewed_identity_correction(
                root,
                _match(),
                {"candidate_subject_id": "su", "action": "assign_team", "team_label": "B", "comment": "non-semantic"},
            )
            self.assertTrue(first["snapshot"]["stale"])
            self.assertEqual(first["semantic_decision_digest"], same["semantic_decision_digest"])
            self.assertEqual(first["review_progress"]["summary"]["completed_by_operator"], 1)
            self.assertEqual(first["decision_impact"]["affected_tracklets"], 1)
            self.assertEqual(first["decision_impact"]["affected_detected_observations"], 2)
            self.assertEqual(same["decision_impact"]["operator_reviewed_observations_delta"], 0)
            self.assertEqual(get_reviewed_identity_status(root)["status"], "stale")
            saved = first["saved_decision"]
            self.assertEqual(saved["action"], "assign_team")
            self.assertEqual(saved["team_label"], "B")
            self.assertIsNone(saved["stable_slot_id"])
            self.assertEqual(_immutable_identity_files(root), immutable_before)
            result = finalize_reviewed_identity(root, _match())
            row = _subject_row(result, "su")
            self.assertEqual(row["team_label"], "B")
            self.assertEqual(row["fallback_label"], "B?")
            self.assertIsNone(row["stable_anonymous_slot_id"])
            self.assertIsNone(row["canonical_player_id"])
            self.assertEqual(row["identity_status"], "unresolved")
            self.assertEqual(row["identity_source"], "operator_team_assignment")
            self.assertFalse(row["eligible_for_player_stats"])
            slot_document = _load(root / "reviewed_identity_slot_assignments.json")
            self.assertEqual(slot_document["reviewed_slots"], [])
            self.assertNotIn("U01", str(slot_document))
            self.assertEqual(
                baseline["fragmentation_diagnostics"]["reviewed_slot_registry_entries"],
                result["fragmentation_diagnostics"]["reviewed_slot_registry_entries"],
            )
            self.assertEqual(result["fragmentation_diagnostics"]["automatic_permanent_allocations"], 0)

    def test_unknown_subject_can_use_team_b_slot_or_roster_without_cross_team_escape(self) -> None:
        with _workspace() as root:
            _fixture(root)
            saved = save_reviewed_identity_correction(
                root,
                _match(),
                {"candidate_subject_id": "su", "action": "assign_existing_slot", "stable_slot_id": "B03"},
            )
            self.assertEqual(saved["saved_decision"]["team_label"], "B")
            result = finalize_reviewed_identity(root, _match())
            row = _subject_row(result, "su")
            self.assertEqual(row["team_label"], "B")
            self.assertEqual(row["stable_anonymous_slot_id"], "B03")
            self.assertEqual(_load(root / "reviewed_identity_slot_assignments.json")["reviewed_slots"], [])
            self.assertEqual(result["frame_uniqueness_diagnostics"]["duplicate_stable_slot_claim_groups"], 0)
            with self.assertRaisesRegex(ValueError, "team mismatch"):
                save_reviewed_identity_correction(
                    root, _match(), {"candidate_subject_id": "s1", "action": "assign_existing_slot", "stable_slot_id": "B03"}
                )
            with self.assertRaisesRegex(ValueError, "team mismatch"):
                save_reviewed_identity_correction(
                    root, _match(), {"candidate_subject_id": "s2", "action": "assign_existing_slot", "stable_slot_id": "A03"}
                )
            with self.assertRaisesRegex(ValueError, "team mismatch"):
                save_reviewed_identity_correction(
                    root, _match(), {"candidate_subject_id": "s1", "action": "assign_team", "team_label": "B"}
                )

        with _workspace() as root:
            _fixture(root)
            save_reviewed_identity_correction(
                root,
                _match(),
                {"candidate_subject_id": "su", "action": "assign_roster_player", "player_id": "p2"},
            )
            result = finalize_reviewed_identity(root, _match())
            row = _subject_row(result, "su")
            self.assertEqual(row["team_label"], "B")
            self.assertEqual(row["canonical_player_id"], "p2")
            self.assertEqual(row["identity_status"], "confirmed")
            self.assertEqual(result["frame_uniqueness_diagnostics"]["duplicate_canonical_player_claim_groups"], 0)
            with self.assertRaisesRegex(ValueError, "Cross-team"):
                save_reviewed_identity_correction(
                    root, _match(), {"candidate_subject_id": "s1", "action": "assign_roster_player", "player_id": "p2"}
                )

    def test_unknown_team_new_player_uses_bounded_target_team_slot(self) -> None:
        with _workspace() as root:
            _fixture(root)
            first = save_reviewed_identity_correction(
                root,
                _match(),
                {"candidate_subject_id": "su", "action": "create_new_stable_player", "team_label": "B"},
            )
            self.assertEqual(first["allocated_stable_slot_id"], "B04")
            result = finalize_reviewed_identity(root, _match())
            row = _subject_row(result, "su")
            self.assertEqual(row["team_label"], "B")
            self.assertEqual(row["stable_anonymous_slot_id"], "B04")

    def test_context_and_existing_slots_are_team_filtered(self) -> None:
        with _workspace() as root:
            _fixture(root)
            context = reviewed_correction_context(root, _match(), "s1")
            self.assertEqual([row["player_id"] for row in context["roster_options"]], ["p1"])
            self.assertEqual(
                [row["stable_slot_id"] for row in context["slot_options"]],
                ["A01", "A02", "A03"],
            )
            saved = save_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": "s1",
                    "action": "assign_existing_slot",
                    "stable_slot_id": "A03",
                },
            )
            self.assertEqual(saved["saved_decision"]["stable_slot_id"], "A03")
            with self.assertRaisesRegex(ValueError, "team mismatch"):
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": "s2",
                        "action": "assign_existing_slot",
                        "stable_slot_id": "A03",
                    },
                )

    def test_new_player_allocates_persistent_bounded_slot_and_checks_active_cap(self) -> None:
        with _workspace() as root:
            _fixture(root)
            first = save_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": "s1",
                    "action": "create_new_stable_player",
                    "team_label": "A",
                },
            )
            second = save_reviewed_identity_correction(
                root,
                _match(),
                {
                    "candidate_subject_id": "s1",
                    "action": "create_new_stable_player",
                    "team_label": "A",
                },
            )
            self.assertEqual(first["allocated_stable_slot_id"], "A04")
            self.assertEqual(second["allocated_stable_slot_id"], "A04")

        with _workspace() as root:
            _fixture(root, active_team_a=7)
            before = _decision_files(root)
            with self.assertRaisesRegex(ValueError, "Eighth simultaneous"):
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": "s1",
                        "action": "create_new_stable_player",
                        "team_label": "A",
                    },
                )
            self.assertEqual(_decision_files(root), before)

        with _workspace() as root:
            _fixture(root)
            global_identity = _load(root / "global_identity.json")
            global_identity["slots"] = [
                {
                    "stable_player_id": f"A{number:02d}",
                    "team_label": "A",
                    "tracklet_ids": [],
                }
                for number in range(1, 15)
            ]
            _write(root / "global_identity.json", global_identity)
            before = _decision_files(root)
            with self.assertRaisesRegex(ValueError, "bounded pool exhausted"):
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {
                        "candidate_subject_id": "s1",
                        "action": "create_new_stable_player",
                        "team_label": "A",
                    },
                )
            self.assertEqual(_decision_files(root), before)

    def test_special_actions_respect_overlay_stats_and_unknown_fallback(self) -> None:
        with _workspace() as root:
            _fixture(root)
            for subject, action in (("s1", "team_unknown"), ("s2", "referee")):
                save_reviewed_identity_correction(
                    root,
                    _match(),
                    {"candidate_subject_id": subject, "action": action},
                )
            result = finalize_reviewed_identity(root, _match())
            by_subject = {
                row["candidate_subject_id"]: row
                for row in result["tracklet_assignments"]
            }
            self.assertEqual(by_subject["s1"]["fallback_label"], "U?")
            self.assertIsNone(by_subject["s1"]["stable_anonymous_slot_id"])
            self.assertEqual(by_subject["s2"]["display_label"], "Sędzia")
            at = reviewed_assignment_at(result, _tracklets(root), 2.0, 10.0)
            self.assertEqual(at[0]["identity_status"], "referee")

            save_reviewed_identity_correction(
                root,
                _match(),
                {"candidate_subject_id": "s2", "action": "false_detection"},
            )
            result = finalize_reviewed_identity(root, _match())
            self.assertEqual(reviewed_assignment_at(result, _tracklets(root), 2.0, 10.0), [])

        with _workspace() as root:
            _fixture(root)
            global_identity = _load(root / "global_identity.json")
            next(row for row in global_identity["slots"] if row["stable_player_id"] == "A03")["tracklet_ids"] = ["t1", "t1b"]
            _write(root / "global_identity.json", global_identity)
            save_reviewed_identity_correction(
                root,
                _match(),
                {"candidate_subject_id": "s1", "action": "unresolved"},
            )
            result = finalize_reviewed_identity(root, _match())
            row = next(
                item
                for item in result["tracklet_assignments"]
                if item["candidate_subject_id"] == "s1"
            )
            self.assertEqual(row["identity_status"], "unresolved")
            self.assertEqual(row["stable_anonymous_slot_id"], "A03")
            self.assertIsNone(row["canonical_player_id"])

    def test_timestamp_lookup_returns_complete_real_detected_entity(self) -> None:
        with _workspace() as root:
            _fixture(root)
            result = finalize_reviewed_identity(root, _match())
            self.assertEqual(reviewed_assignment_at(result, _tracklets(root), 0.2, 10), [])
            entity = reviewed_assignment_at(result, _tracklets(root), 0.3, 10)[0]
            required = {
                "frame",
                "time_sec",
                "tracklet_id",
                "candidate_subject_id",
                "candidate_subject_ids",
                "team_label",
                "stable_anonymous_slot_id",
                "canonical_player_id",
                "player_name",
                "display_label",
                "identity_status",
                "identity_source",
                "fallback_label",
                "requires_review",
                "hard_blockers",
                "conflicts",
                "detected_evidence_count",
                "frame_start",
                "frame_end",
            }
            self.assertTrue(required.issubset(entity))


def _fixture(root: Path, active_team_a: int | None = None) -> None:
    tracklets = [
        {
            "tracklet_id": "t1",
            "team_label": "A",
            "team_id": "ta",
            "positions_m": [
                {"frame": 1, "status": "predicted", "source": "predicted"},
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
            ],
        },
        {
            "tracklet_id": "tu",
            "team_label": "U",
            "team_id": "",
            "positions_m": [
                {"frame": 30, "status": "detected", "pitch_m": [5.0, 2.0], "bbox_xyxy": [5, 2, 9, 9]},
                {"frame": 31, "status": "detected", "pitch_m": [5.5, 2.0], "bbox_xyxy": [6, 2, 10, 9]},
            ],
        },
    ]
    _write(root / "match.json", _match())
    _write(root / "tracklets.json", {"tracklets": tracklets})
    _write(
        root / "identity_candidate_shadow.json",
        {
            "subjects": [
                {"candidate_subject_id": "s1", "tracklet_ids": ["t1", "t1b"]},
                {"candidate_subject_id": "s2", "tracklet_ids": ["t2"]},
                {"candidate_subject_id": "su", "tracklet_ids": ["tu"]},
            ]
        },
    )
    slots = [
        {"stable_player_id": slot_id, "team_label": slot_id[0], "tracklet_ids": []}
        for slot_id in ("A01", "A02", "A03", "B01", "B02", "B03")
    ]
    global_identity: dict = {"slots": slots}
    if active_team_a is not None:
        global_identity["frames"] = [
            {"frame": frame, "active_team_a": active_team_a}
            for frame in (3, 4, 8, 9)
        ]
    _write(root / "global_identity.json", global_identity)
    _write(root / "stable_players.json", {"players": []})
    _write(
        root / "identity_roster_subject_review_shadow.json",
        {
            "cards": [
                _card("s1", "A", "card-s1", "p1"),
                _card("s2", "B", "card-s2", "p2"),
            ]
        },
    )


def _card(subject: str, team: str, key: str, player: str) -> dict:
    return {
        "review_card_key": key,
        "candidate_subject_id": subject,
        "team_label": team,
        "review_status": "ready_for_operator_review",
        "roster_candidates": [{"player_id": player}],
        "allowed_actions": ["assign_roster_player", "mark_unresolved"],
        "visual_evidence": {"anchor_crops": []},
    }


def _match() -> dict:
    return {
        "id": "m1",
        "teams": [
            {"id": "ta", "players": [{"id": "p1", "name": "One", "number": "8"}]},
            {"id": "tb", "players": [{"id": "p2", "name": "Two", "number": "9"}]},
        ],
    }


def _tracklets(root: Path) -> dict[str, dict]:
    return {
        row["tracklet_id"]: row
        for row in json.loads((root / "tracklets.json").read_text(encoding="utf-8"))["tracklets"]
    }


def _subject_row(snapshot: dict, subject_id: str) -> dict:
    return next(row for row in snapshot["tracklet_assignments"] if row["candidate_subject_id"] == subject_id)


def _immutable_identity_files(root: Path) -> dict[str, bytes]:
    return {
        name: (root / name).read_bytes()
        for name in ("tracklets.json", "global_identity.json", "stable_players.json")
    }


def _decision_files(root: Path) -> dict[str, bytes | None]:
    names = (
        "identity_roster_subject_review_decisions_shadow.json",
        "reviewed_identity_slot_assignments.json",
    )
    return {
        name: (root / name).read_bytes() if (root / name).exists() else None
        for name in names
    }


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
