from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.services.identity_canonical_ownership import (
    artifact_membership_integrity,
    global_observation_ownership,
    slot_claims,
)
from app.services.identity_reviewed_frame_uniqueness import build_frame_slot_demotions
from app.services.identity_reviewed_snapshot import (
    _canonical_observation_assignments,
    _summary,
    finalize_reviewed_identity,
)
from app.services.identity_stable_anonymous import resolve_stable_anonymous_entities


class CanonicalOwnershipTests(unittest.TestCase):
    def test_single_global_slot_remains_tracklet_level(self) -> None:
        global_identity = {"slots": [_slot("A05", "t1", [10, 11])]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root / "global_identity.json", global_identity)
            resolved, _ = resolve_stable_anonymous_entities(
                root,
                {"t1": _tracklet("t1", "A", [10, 11])},
                _subjects("t1"),
            )
        self.assertEqual(resolved["t1"]["stable_anonymous_slot_id"], "A05")
        self.assertEqual(global_observation_ownership(global_identity), [])

    def test_multi_slot_global_tracklet_keeps_disjoint_frame_ownership(self) -> None:
        global_identity = {
            "slots": [
                _slot("A05", "t1", [10, 11]),
                _slot("A08", "t1", [20, 21]),
            ]
        }
        claims = global_observation_ownership(global_identity)
        self.assertEqual(
            [(row["frame"], row["stable_slot_id"]) for row in claims],
            [(10, "A05"), (11, "A05"), (20, "A08"), (21, "A08")],
        )

    def test_frame_ownership_applies_slot_roster_binding_without_bleed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root / "match.json", _match())
            _write(root / "tracklets.json", {"tracklets": [_tracklet("t1", "A", [10, 20])]})
            _write(root / "identity_candidate_shadow.json", _subjects("t1"))
            global_identity = {
                "slots": [
                    _slot("A05", "t1", [10]),
                    _slot("A08", "t1", [20]),
                ]
            }
            _write(root / "global_identity.json", global_identity)
            _write(
                root / "stable_players.json",
                {
                    "players": [
                        {
                            "stable_player_id": row["stable_player_id"],
                            "stable_subject_id": row["stable_subject_id"],
                            "team_label": row["team_label"],
                            "tracklet_ids": row["tracklet_ids"],
                        }
                        for row in global_identity["slots"]
                    ]
                },
            )
            _write(
                root / "reviewed_identity_slot_assignments.json",
                {
                    "decisions": [
                        {
                            "candidate_subject_id": "s1",
                            "action": "assign_roster_player",
                            "stable_slot_id": "A05",
                            "player_id": "p05",
                        }
                    ]
                },
            )

            snapshot = finalize_reviewed_identity(root, _match())

        by_frame = {
            row["frame"]: row
            for row in snapshot["canonical_observation_assignments"]
        }
        self.assertEqual(by_frame[10]["stable_anonymous_slot_id"], "A05")
        self.assertEqual(by_frame[10]["canonical_player_id"], "p05")
        self.assertEqual(by_frame[20]["stable_anonymous_slot_id"], "A08")
        self.assertIsNone(by_frame[20]["canonical_player_id"])

    def test_stable_view_mirror_is_not_a_second_vote_and_mismatch_is_explicit(self) -> None:
        global_identity = {"slots": [_slot("A05", "t1", [10])]}
        stable_players = {
            "players": [
                {
                    "stable_player_id": "A05",
                    "team_label": "A",
                    "tracklet_ids": ["t1"],
                }
            ]
        }
        self.assertTrue(
            artifact_membership_integrity(global_identity, stable_players)["exact_mirror"]
        )
        stable_players["players"][0]["tracklet_ids"] = ["other"]
        integrity = artifact_membership_integrity(global_identity, stable_players)
        self.assertEqual(integrity["classification"], "stale_derived_artifact")
        self.assertFalse(integrity["exact_mirror"])

    def test_slot_claims_preserve_all_multi_slot_claims(self) -> None:
        claims = slot_claims(
            {"slots": [_slot("A05", "t1", [10]), _slot("A08", "t1", [20])]},
            "slots",
            source="global_identity",
        )
        self.assertEqual(
            [row["stable_slot_id"] for row in claims["t1"]], ["A05", "A08"]
        )

    def test_uniqueness_runs_after_canonical_observation_resolution(self) -> None:
        tracklets = {
            "canonical": _tracklet("canonical", "A", [10]),
            "duplicate": _tracklet("duplicate", "A", [10]),
        }
        assignments = [
            _assignment("canonical", None),
            {
                **_assignment("duplicate", "A05"),
                "identity_source": "candidate_shadow",
            },
        ]
        canonical = [
            {
                **_assignment("canonical", "A05"),
                "frame": 10,
                "identity_source": "canonical_frame_global_identity",
            }
        ]
        demotions, diagnostics = build_frame_slot_demotions(
            tracklets, assignments, canonical_ownership=canonical
        )
        self.assertEqual(diagnostics["duplicate_stable_slot_claim_groups"], 1)
        self.assertEqual([row["tracklet_id"] for row in demotions], ["duplicate"])

    def test_explicit_unresolved_suppresses_frame_slot_roster_binding(self) -> None:
        claims = [_ownership("A05", 10), _ownership("A08", 20)]
        base = {
            **_assignment("t1", None),
            "identity_status": "unresolved",
            "identity_source": "manual_review",
        }
        rows = _canonical_observation_assignments(
            claims,
            [base],
            {"A05": {"player_id": "p05", "source": "manual_stable_slot_binding"}},
            set(),
            {"p05": {"name": "Krzysiek", "number": "5"}},
        )
        by_frame = {row["frame"]: row for row in rows}
        self.assertEqual(by_frame[10]["stable_anonymous_slot_id"], "A05")
        self.assertEqual(by_frame[10]["identity_status"], "unresolved")
        self.assertIsNone(by_frame[10]["canonical_player_id"])
        self.assertEqual(by_frame[10]["display_label"], "A05")
        self.assertEqual(by_frame[20]["stable_anonymous_slot_id"], "A08")
        self.assertIsNone(by_frame[20]["canonical_player_id"])

    def test_special_operator_actions_are_not_overridden_by_frame_ownership(self) -> None:
        claim = [_ownership("A05", 10)]
        for action in ("false_detection", "referee", "team_unknown"):
            with self.subTest(action=action):
                rows = _canonical_observation_assignments(
                    claim,
                    [{**_assignment("t1", None), "identity_status": action}],
                    {"A05": {"player_id": "p05", "source": "manual_stable_slot_binding"}},
                    set(),
                    {"p05": {"name": "Krzysiek", "number": "5"}},
                )
                self.assertEqual(rows, [])

    def test_fully_owned_multi_slot_tracklet_is_not_a_phantom_conflict(self) -> None:
        assignments = [
            {
                **_assignment("t1", None),
                "identity_status": "conflicted",
                "hard_blockers": ["upstream_multi_slot_tracklet_membership"],
            }
        ]
        ownership = [
            {**_ownership("A05", 10), "identity_status": "confirmed"},
            {**_ownership("A08", 20), "identity_status": "confirmed"},
        ]
        summary = _summary(
            assignments,
            {},
            {"stable_anonymous_entities_total": 0, "unanchored_fragments": 0, "automatic_permanent_allocations": 0},
            {},
            {"t1": _tracklet("t1", "A", [10, 20])},
            ownership,
        )
        self.assertEqual(summary["confirmed"], 1)
        self.assertEqual(summary["conflicted"], 0)
        self.assertEqual(summary["fully_resolved_frame_owned_tracklets"], 1)

    def test_multi_slot_tracklet_with_ownership_gap_stays_reviewable(self) -> None:
        assignments = [
            {
                **_assignment("t1", None),
                "identity_status": "conflicted",
                "hard_blockers": ["upstream_multi_slot_tracklet_membership"],
            }
        ]
        summary = _summary(
            assignments,
            {},
            {"stable_anonymous_entities_total": 0, "unanchored_fragments": 0, "automatic_permanent_allocations": 0},
            {},
            {"t1": _tracklet("t1", "A", [10, 20])},
            [{**_ownership("A05", 10), "identity_status": "confirmed"}],
        )
        self.assertEqual(summary["conflicted"], 1)
        self.assertEqual(summary["frame_ownership_gap_tracklets"], 1)


def _slot(slot_id: str, tracklet_id: str, frames: list[int]) -> dict:
    return {
        "slot_id": slot_id,
        "stable_player_id": slot_id,
        "stable_subject_id": f"slot-{slot_id}",
        "team_label": slot_id[0],
        "tracklet_ids": [tracklet_id],
        "overlay_positions": [
            {
                "tracklet_id": tracklet_id,
                "frame": frame,
                "status": "detected",
                "source": "detected",
            }
            for frame in frames
        ],
    }


def _tracklet(tracklet_id: str, team: str, frames: list[int]) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "team_label": team,
        "positions_m": [
            {
                "frame": frame,
                "status": "detected",
                "source": "detected",
                "pitch_m": [float(frame), 1.0],
            }
            for frame in frames
        ],
    }


def _subjects(tracklet_id: str) -> dict:
    return {"subjects": [{"candidate_subject_id": "s1", "tracklet_ids": [tracklet_id]}]}


def _assignment(tracklet_id: str, slot_id: str | None) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "team_label": "A",
        "stable_anonymous_slot_id": slot_id,
        "identity_status": "unresolved",
        "fallback_label": slot_id or "A?",
        "identity_source": "global_identity",
        "hard_blockers": [],
        "conflicts": [],
    }


def _ownership(slot_id: str, frame: int) -> dict:
    return {
        "tracklet_id": "t1",
        "frame": frame,
        "stable_slot_id": slot_id,
        "team_label": "A",
        "ownership_evidence_source": "global_identity",
        "ownership_evidence_field": "overlay_positions",
    }


def _match() -> dict:
    return {
        "id": "canonical-frame-test",
        "teams": [
            {
                "id": "team-a",
                "players": [{"id": "p05", "name": "Krzysiek", "number": "5"}],
            }
        ],
    }


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
