from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.services.identity_reviewed_frame_uniqueness import build_frame_slot_demotions
from app.services.identity_reviewed_slot_review import save_reviewed_slot_assignments
from app.services.identity_stable_anonymous import resolve_stable_anonymous_entities


class StableAnonymousIdentityTests(unittest.TestCase):
    def test_forty_unanchored_fragments_do_not_allocate_permanent_slots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tracklets = {f"t{i}": _tracklet(f"t{i}", "A", i) for i in range(40)}
            candidates = _candidates(*[(f"s{i}", [f"t{i}"], None) for i in range(40)])
            resolved, diagnostics = resolve_stable_anonymous_entities(root, tracklets, candidates)
            self.assertTrue(all(row["stable_anonymous_slot_id"] is None for row in resolved.values()))
            self.assertEqual({row["fallback_label"] for row in resolved.values()}, {"A?"})
            self.assertEqual(diagnostics["automatic_permanent_allocations"], 0)

    def test_existing_a01_to_a12_remain_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            slots = [_slot(f"A{i:02d}", f"t{i}") for i in range(1, 13)]
            _write(root / "global_identity.json", {"slots": slots})
            tracklets = {f"t{i}": _tracklet(f"t{i}", "A", i) for i in range(1, 13)}
            candidates = _candidates(*[(f"s{i}", [f"t{i}"], None) for i in range(1, 13)])
            resolved, diagnostics = resolve_stable_anonymous_entities(root, tracklets, candidates)
            self.assertEqual(
                {row["stable_anonymous_slot_id"] for row in resolved.values()},
                {f"A{i:02d}" for i in range(1, 13)},
            )
            self.assertEqual(diagnostics["highest_fallback_number_by_team"]["A"], 12)

    def test_fragments_with_same_candidate_base_reuse_one_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root / "global_identity.json", {"slots": [_slot("A03", "canonical")]})
            tracklets = {"t1": _tracklet("t1", "A", 1), "t2": _tracklet("t2", "A", 5)}
            candidates = _candidates(("s1", ["t1"], "A03~2"), ("s2", ["t2"], "A03~7"))
            resolved, _ = resolve_stable_anonymous_entities(root, tracklets, candidates)
            self.assertEqual({row["stable_anonymous_slot_id"] for row in resolved.values()}, {"A03"})

    def test_unknown_mixed_ambiguous_and_no_position_remain_unanchored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tracklets = {
                "u": _tracklet("u", "U", 1),
                "a": _tracklet("a", "A", 2),
                "b": _tracklet("b", "B", 3),
                "amb": _tracklet("amb", "A", 4),
                "empty": {"tracklet_id": "empty", "team_label": "A", "positions_m": []},
            }
            candidates = _candidates(
                ("unknown", ["u"], None),
                ("mixed", ["a", "b"], "A01"),
                ("one", ["amb"], "A02"),
                ("two", ["amb"], "A03"),
                ("empty", ["empty"], None),
            )
            resolved, _ = resolve_stable_anonymous_entities(root, tracklets, candidates)
            self.assertEqual(resolved["u"]["fallback_label"], "U?")
            self.assertIsNone(resolved["a"]["stable_anonymous_slot_id"])
            self.assertIn("mixed_team_candidate_subject", resolved["a"]["hard_blockers"])
            self.assertIsNone(resolved["amb"]["stable_anonymous_slot_id"])
            self.assertIn("ambiguous_candidate_subject_membership", resolved["amb"]["hard_blockers"])
            self.assertTrue(resolved["empty"]["insufficient_evidence"])
            self.assertEqual(resolved["empty"]["detected_evidence_count"], 0)
            self.assertEqual(resolved["empty"]["fallback_label"], "A?")

    def test_conflicting_sources_create_hard_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root / "global_identity.json", {"slots": [_slot("A01", "t1")]})
            _write(root / "stable_players.json", {"players": [_slot("A02", "t1")]})
            resolved, diagnostics = resolve_stable_anonymous_entities(
                root,
                {"t1": _tracklet("t1", "A", 1)},
                _candidates(("s1", ["t1"], None)),
            )
            self.assertIsNone(resolved["t1"]["stable_anonymous_slot_id"])
            self.assertIn("conflicting_stable_anchor_sources", resolved["t1"]["hard_blockers"])
            self.assertEqual(diagnostics["conflicting_anchor_sources"], 1)

    def test_manual_existing_and_new_slot_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = _candidates(("existing", ["t1"], None), ("new", ["t2"], None))
            _write(root / "global_identity.json", {"slots": [_slot("A01", "old")]})
            manual = save_reviewed_slot_assignments(
                root,
                candidate,
                [
                    {"candidate_subject_id": "existing", "action": "assign_existing_slot", "stable_slot_id": "A01"},
                    {"candidate_subject_id": "new", "action": "create_new_stable_player", "team_label": "A"},
                ],
            )
            resolved, diagnostics = resolve_stable_anonymous_entities(
                root,
                {"t1": _tracklet("t1", "A", 1), "t2": _tracklet("t2", "A", 10)},
                candidate,
                manual,
            )
            self.assertEqual(resolved["t1"]["stable_anonymous_slot_id"], "A01")
            self.assertEqual(resolved["t2"]["stable_anonymous_slot_id"], "A02")
            self.assertEqual(diagnostics["manual_new_player_allocations"], 1)

    def test_manual_new_slot_respects_seven_visible_players(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            slots = [_slot(f"A{i:02d}", f"old{i}", frame=5) for i in range(1, 8)]
            _write(
                root / "global_identity.json",
                {
                    "parameters": {"players_per_team": 7},
                    "slots": slots,
                    "frames": [
                        {
                            "frame": 1500,
                            "active_team_a": 7,
                            "active_team_b": 4,
                        }
                    ],
                },
            )
            candidate = _candidates(
                ("new", ["t1"], None),
                ("fallback", ["t2"], None),
            )
            manual = save_reviewed_slot_assignments(
                root,
                candidate,
                [
                    {
                        "candidate_subject_id": "new",
                        "action": "create_new_stable_player",
                        "team_label": "A",
                    }
                ],
            )
            manual = save_reviewed_slot_assignments(
                root,
                candidate,
                [
                    {
                        "candidate_subject_id": "fallback",
                        "action": "create_new_stable_player",
                        "team_label": "A",
                    }
                ],
            )
            resolved, _ = resolve_stable_anonymous_entities(
                root,
                {
                    "t1": _tracklet("t1", "A", 1500),
                    "t2": _tracklet("t2", "A", 5),
                },
                candidate,
                manual,
            )
            self.assertIsNone(resolved["t1"]["stable_anonymous_slot_id"])
            self.assertIn("manual_new_player_active_team_cap_exceeded", resolved["t1"]["hard_blockers"])
            self.assertIsNone(resolved["t2"]["stable_anonymous_slot_id"])
            self.assertIn("manual_new_player_active_team_cap_exceeded", resolved["t2"]["hard_blockers"])

    def test_manual_new_slot_cannot_exceed_fourteen_match_players(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(
                root / "global_identity.json",
                {"slots": [_slot(f"A{i:02d}", f"old{i}", frame=i) for i in range(1, 15)]},
            )
            candidate = _candidates(("new", ["t1"], None))
            with self.assertRaisesRegex(ValueError, "bounded pool exhausted"):
                save_reviewed_slot_assignments(
                    root,
                    candidate,
                    [
                        {
                            "candidate_subject_id": "new",
                            "action": "create_new_stable_player",
                            "team_label": "A",
                        }
                    ],
                )
            self.assertFalse((root / "reviewed_identity_slot_assignments.json").exists())

    def test_frame_uniqueness_demotes_lower_priority_claim(self) -> None:
        tracklets = {"manual": _tracklet("manual", "A", 1), "auto": _tracklet("auto", "A", 1)}
        assignments = [
            _assignment("manual", "A01", "manual_review"),
            _assignment("auto", "A01", "candidate_shadow"),
        ]
        demotions, diagnostics = build_frame_slot_demotions(tracklets, assignments)
        self.assertEqual([(row["tracklet_id"], row["frame"]) for row in demotions], [("auto", 1)])
        self.assertEqual(diagnostics["duplicate_stable_labels_rendered"], 0)

    def test_exact_false_detection_does_not_demote_valid_stable_claim(self) -> None:
        tracklets = {
            "false": _tracklet("false", "A", 10),
            "valid": _tracklet("valid", "A", 10),
        }
        assignments = [
            _assignment("false", "A03", "global_identity"),
            _assignment("valid", "A03", "global_identity"),
        ]
        demotions, diagnostics = build_frame_slot_demotions(
            tracklets,
            assignments,
            [
                {
                    "tracklet_id": "false",
                    "frame": 10,
                    "identity_status": "false_detection",
                    "identity_source": "operator_seed_exact_observation",
                }
            ],
        )
        self.assertEqual(demotions, [])
        self.assertEqual(diagnostics["duplicate_stable_slot_claim_groups"], 0)

    def test_equal_exact_player_claims_are_both_demoted(self) -> None:
        tracklets = {
            "left": _tracklet("left", "A", 10),
            "right": _tracklet("right", "A", 10),
        }
        assignments = [
            _assignment("left", "A01", "global_identity"),
            _assignment("right", "A02", "global_identity"),
        ]
        overrides = [
            {
                "tracklet_id": tracklet_id,
                "frame": 10,
                "identity_status": "confirmed",
                "canonical_player_id": "p1",
                "player_name": "One",
                "identity_source": "operator_seed_exact_observation",
            }
            for tracklet_id in ("left", "right")
        ]
        demotions, diagnostics = build_frame_slot_demotions(
            tracklets, assignments, overrides
        )
        self.assertEqual({row["tracklet_id"] for row in demotions}, {"left", "right"})
        self.assertTrue(all(row["canonical_player_id"] is None for row in demotions))
        self.assertEqual(diagnostics["duplicate_canonical_player_claim_groups"], 1)
        self.assertEqual(diagnostics["demoted_canonical_player_observations"], 2)

    def test_exact_player_claim_wins_over_whole_subject_claim(self) -> None:
        tracklets = {
            "exact": _tracklet("exact", "A", 10),
            "whole": _tracklet("whole", "A", 10),
        }
        assignments = [
            _assignment("exact", "A01", "global_identity"),
            {
                **_assignment("whole", "A02", "global_identity"),
                "identity_status": "confirmed",
                "canonical_player_id": "p1",
                "identity_source": "operator_review",
            },
        ]
        overrides = [
            {
                "tracklet_id": "exact",
                "frame": 10,
                "identity_status": "confirmed",
                "canonical_player_id": "p1",
                "identity_source": "operator_seed_exact_observation",
            }
        ]
        demotions, _ = build_frame_slot_demotions(tracklets, assignments, overrides)
        self.assertEqual([row["tracklet_id"] for row in demotions], ["whole"])


def _tracklet(tracklet_id: str, team: str, frame: int) -> dict:
    return {"tracklet_id": tracklet_id, "team_label": team, "positions_m": [{"frame": frame, "status": "detected"}]}


def _slot(label: str, tracklet_id: str, *, frame: int = 0) -> dict:
    return {"slot_id": label, "stable_player_id": label, "team_label": label[0], "tracklet_ids": [tracklet_id], "trajectory_m": [{"frame": frame, "status": "detected", "source": "detected"}]}


def _candidates(*rows: tuple[str, list[str], str | None]) -> dict:
    return {"subjects": [{"candidate_subject_id": subject, "tracklet_ids": tracklets, "candidate_player_id": player} for subject, tracklets, player in rows]}


def _assignment(tracklet_id: str, slot: str, source: str) -> dict:
    return {"tracklet_id": tracklet_id, "team_label": slot[0], "stable_anonymous_slot_id": slot, "stable_anchor_source": source, "identity_status": "unresolved"}


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
