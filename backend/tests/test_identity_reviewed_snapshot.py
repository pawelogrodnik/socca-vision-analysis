from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.services.identity_reviewed_snapshot import (
    _slot_roster_bindings,
    finalize_reviewed_identity,
    get_reviewed_identity_status,
    reviewed_assignment_at,
)
from app.services.identity_reviewed_progress import build_reviewed_identity_progress
from app.services.identity_reviewed_slot_review import save_reviewed_slot_assignments


class ReviewedIdentitySnapshotTests(unittest.TestCase):
    def test_off_pitch_only_tracklet_does_not_block_product_completion(self) -> None:
        with _workspace() as root:
            _write_inputs(
                root,
                decisions=[_decision("s1", "assign_roster_player", "p1")],
            )
            tracklets = json.loads((root / "tracklets.json").read_text())
            by_id = {row["tracklet_id"]: row for row in tracklets["tracklets"]}
            by_id["t1"]["positions_m"] = [
                {
                    "frame": 1,
                    "status": "detected",
                    "source": "detected",
                    "play_area_status": "inside_play",
                }
            ]
            by_id["t2"]["positions_m"] = [
                {
                    "frame": 10,
                    "status": "detected",
                    "source": "detected",
                    "play_area_status": "outside_play",
                },
                {
                    "frame": 11,
                    "status": "detected",
                    "source": "detected",
                    "play_area_status": "boundary_transient",
                },
            ]
            (root / "tracklets.json").write_text(json.dumps(tracklets))

            result = finalize_reviewed_identity(root, _match())
            progress = build_reviewed_identity_progress(root, _match())

            self.assertEqual(result["status"], "complete_reviewed")
            self.assertEqual(result["summary"]["tracklets_total"], 2)
            self.assertEqual(result["summary"]["product_tracklets_total"], 1)
            self.assertEqual(result["summary"]["product_entities_total"], 1)
            self.assertEqual(
                result["summary"]["product_stable_anonymous_entities"], 0
            )
            self.assertEqual(result["summary"]["confirmed"], 1)
            self.assertEqual(result["summary"]["confirmed_players"], 1)
            self.assertEqual(result["summary"]["unresolved"], 0)
            self.assertEqual(result["summary"]["conflicted"], 0)
            self.assertEqual(result["summary"]["blocked"], 0)
            self.assertEqual(result["summary"]["off_pitch_only_tracklets"], 1)
            self.assertEqual(
                result["summary"]["tracklets_without_inside_play_detections"],
                1,
            )
            self.assertEqual(progress["summary"]["important_decisions_remaining"], 0)
            self.assertEqual(progress["summary"]["optional_cases_remaining"], 0)
            self.assertEqual(progress["summary"]["ignored_low_impact"], 1)
            self.assertEqual(
                result["count_semantics"]["tracklets_total"],
                "technical_all_tracklets",
            )
            self.assertEqual(
                result["count_semantics"]["status"],
                "product_tracklets_only",
            )

    def test_explicit_review_wins_and_unresolved_overrides_seed(self) -> None:
        with _workspace() as root:
            _write_inputs(root, decisions=[_decision("s1", "assign_roster_player", "p1"), _decision("s2", "mark_unresolved")], seeded=[_seed("s2", "p1")])
            result = finalize_reviewed_identity(root, _match())
            rows = {row["tracklet_id"]: row for row in result["tracklet_assignments"]}
            self.assertEqual(rows["t1"]["display_label"], "Paweł")
            self.assertEqual(rows["t2"]["identity_status"], "unresolved")
            self.assertEqual(rows["t2"]["display_label"], "A?")

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
            self.assertEqual(row["display_label"], "A? !")

    def test_labels_are_deterministic_and_snapshot_becomes_stale(self) -> None:
        with _workspace() as root:
            _write_inputs(root, decisions=[])
            first = finalize_reviewed_identity(root, _match()); second = finalize_reviewed_identity(root, _match())
            self.assertEqual(first["semantic_digest"], second["semantic_digest"])
            self.assertEqual([row["fallback_label"] for row in first["tracklet_assignments"]], ["A?", "A?"])
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

    def test_snapshot_from_previous_algorithm_is_stale(self) -> None:
        with _workspace() as root:
            _write_inputs(root, decisions=[])
            result = finalize_reviewed_identity(root, _match())
            result["source"]["algorithm_version"] = "reviewed_identity_snapshot:v3"
            (root / "reviewed_identity_snapshot.json").write_text(
                json.dumps(result), encoding="utf-8"
            )
            self.assertTrue(get_reviewed_identity_status(root)["stale"])

    def test_identical_manual_slot_save_is_semantically_stable(self) -> None:
        with _workspace() as root:
            _write_inputs(root, decisions=[])
            tracklets = json.loads((root / "tracklets.json").read_text())
            for index, row in enumerate(tracklets["tracklets"]):
                row["positions_m"] = [
                    {"frame": index * 10, "status": "detected"}
                ]
            (root / "tracklets.json").write_text(json.dumps(tracklets))
            (root / "global_identity.json").write_text(
                json.dumps(
                    {
                        "slots": [
                            {
                                "slot_id": f"A{number:02d}",
                                "stable_player_id": f"A{number:02d}",
                                "team_label": "A",
                                "tracklet_ids": [f"canonical-{number}"],
                            }
                            for number in range(1, 11)
                        ]
                    }
                )
            )
            candidates = json.loads(
                (root / "identity_candidate_shadow.json").read_text()
            )
            update = [
                {
                    "candidate_subject_id": "s1",
                    "action": "create_new_stable_player",
                    "team_label": "A",
                }
            ]
            first_document = save_reviewed_slot_assignments(
                root, candidates, update
            )
            first = finalize_reviewed_identity(root, _match())
            second_document = save_reviewed_slot_assignments(
                root, candidates, update
            )
            self.assertEqual(
                first_document["decisions"], second_document["decisions"]
            )
            self.assertFalse(get_reviewed_identity_status(root)["stale"])
            second = finalize_reviewed_identity(root, _match())
            self.assertEqual(first["semantic_digest"], second["semantic_digest"])

            changed = save_reviewed_slot_assignments(
                root,
                candidates,
                [
                    {
                        "candidate_subject_id": "s2",
                        "action": "create_new_stable_player",
                        "team_label": "A",
                    }
                ],
            )
            slots = {
                row["candidate_subject_id"]: row["stable_slot_id"]
                for row in changed["decisions"]
            }
            self.assertEqual(slots, {"s1": "A11", "s2": "A12"})
            self.assertTrue(get_reviewed_identity_status(root)["stale"])

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
            (root / "global_identity.json").write_text(json.dumps({
                "slots": [{"stable_player_id": "A01", "tracklet_ids": ["t1", "t2"]}]
            }))
            result = finalize_reviewed_identity(root, _match())
            self.assertEqual(
                [row["fallback_label"] for row in result["tracklet_assignments"]],
                ["A01", "A01"],
            )
            self.assertEqual(result["fragmentation_diagnostics"]["stable_anonymous_entities_total"], 1)

    def test_legacy_subject_assignment_binds_every_fragment_of_one_slot(self) -> None:
        with _workspace() as root:
            _write_inputs(root, decisions=[_decision("s1", "assign_roster_player", "p1")])
            (root / "global_identity.json").write_text(
                json.dumps(
                    {
                        "slots": [
                            {
                                "stable_player_id": "A01",
                                "team_label": "A",
                                "tracklet_ids": ["t1", "t2"],
                            }
                        ]
                    }
                )
            )
            result = finalize_reviewed_identity(root, _match())
            rows = {row["tracklet_id"]: row for row in result["tracklet_assignments"]}
            self.assertEqual(rows["t1"]["canonical_player_id"], "p1")
            self.assertEqual(rows["t2"]["canonical_player_id"], "p1")
            self.assertEqual(
                rows["t2"]["identity_source"],
                "legacy_subject_to_stable_slot_binding",
            )

    def test_conflicting_slot_claims_preserve_explicit_subject_assignments(self) -> None:
        with _workspace() as root:
            _write_inputs(
                root,
                decisions=[
                    _decision("s1", "assign_roster_player", "p1"),
                    _decision("s2", "assign_roster_player", "p3"),
                ],
            )
            _write_shared_slot_fixture(root, include_unreviewed_subject=False)

            result = finalize_reviewed_identity(root, _match())
            rows = {
                row["tracklet_id"]: row
                for row in result["tracklet_assignments"]
            }

            self.assertEqual(rows["t1"]["identity_status"], "confirmed")
            self.assertEqual(rows["t1"]["canonical_player_id"], "p1")
            self.assertEqual(rows["t2"]["identity_status"], "confirmed")
            self.assertEqual(rows["t2"]["canonical_player_id"], "p3")
            tracklets = {
                row["tracklet_id"]: row
                for row in json.loads((root / "tracklets.json").read_text())["tracklets"]
            }
            self.assertEqual(
                reviewed_assignment_at(result, tracklets, 0.0, 1)[0]["canonical_player_id"],
                "p1",
            )
            self.assertEqual(
                reviewed_assignment_at(result, tracklets, 10.0, 1)[0]["canonical_player_id"],
                "p3",
            )

    def test_conflicting_slot_claims_do_not_propagate_to_unreviewed_subject(self) -> None:
        with _workspace() as root:
            _write_inputs(
                root,
                decisions=[
                    _decision("s1", "assign_roster_player", "p1"),
                    _decision("s2", "assign_roster_player", "p3"),
                ],
            )
            _write_shared_slot_fixture(root, include_unreviewed_subject=True)

            rows = {
                row["tracklet_id"]: row
                for row in finalize_reviewed_identity(root, _match())[
                    "tracklet_assignments"
                ]
            }

            self.assertEqual(rows["t1"]["canonical_player_id"], "p1")
            self.assertEqual(rows["t2"]["canonical_player_id"], "p3")
            self.assertEqual(rows["t3"]["identity_status"], "conflicted")
            self.assertIsNone(rows["t3"]["canonical_player_id"])
            self.assertIn(
                "conflicting_stable_slot_roster_bindings",
                rows["t3"]["hard_blockers"],
            )
            self.assertIn(
                "stable_slot_propagation_conflicted",
                rows["t3"]["propagation_diagnostics"],
            )

    def test_conflicted_slot_diagnostic_survives_exact_observation_override(self) -> None:
        with _workspace() as root:
            _write_inputs(
                root,
                decisions=[
                    _decision("s1", "assign_roster_player", "p1"),
                    _decision("s2", "assign_roster_player", "p3"),
                ],
            )
            _write_shared_slot_fixture(root, include_unreviewed_subject=True)
            _set_shared_slot_frame(root, 500)
            (root / "identity_operator_seeds.json").write_text(
                json.dumps(
                    {
                        "decisions": [
                            _exact_seed("t3", 500, "p4"),
                        ]
                    }
                )
            )

            result = finalize_reviewed_identity(root, _match())
            assignments = {
                row["tracklet_id"]: row for row in result["tracklet_assignments"]
            }
            tracklets = {
                row["tracklet_id"]: row
                for row in json.loads((root / "tracklets.json").read_text())["tracklets"]
            }
            effective = {
                row["tracklet_id"]: row
                for row in reviewed_assignment_at(result, tracklets, 50.0, 10)
            }

            self.assertEqual(assignments["t3"]["identity_status"], "conflicted")
            self.assertIn(
                "stable_slot_propagation_conflicted",
                assignments["t3"]["propagation_diagnostics"],
            )
            self.assertEqual(effective["t1"]["canonical_player_id"], "p1")
            self.assertEqual(effective["t2"]["canonical_player_id"], "p3")
            self.assertEqual(effective["t3"]["canonical_player_id"], "p4")
            self.assertEqual(result["observation_demotions"], [])

    def test_exact_override_on_conflicted_slot_keeps_canonical_player_guard(self) -> None:
        with _workspace() as root:
            _write_inputs(
                root,
                decisions=[
                    _decision("s1", "assign_roster_player", "p1"),
                    _decision("s2", "assign_roster_player", "p3"),
                ],
            )
            _write_shared_slot_fixture(root, include_unreviewed_subject=True)
            _set_shared_slot_frame(root, 500)
            (root / "identity_operator_seeds.json").write_text(
                json.dumps({"decisions": [_exact_seed("t3", 500, "p1")]})
            )

            result = finalize_reviewed_identity(root, _match())
            conflict_codes = {
                conflict["code"]
                for row in result["observation_demotions"]
                for conflict in row["conflicts"]
            }

            self.assertIn("duplicate_canonical_player_in_frame", conflict_codes)
            self.assertNotIn("duplicate_stable_slot_in_frame", conflict_codes)

    def test_multi_slot_subject_is_not_inferred_as_a_slot_binding(self) -> None:
        stable = {
            "t1": {
                "candidate_subject_id": "s1",
                "stable_anonymous_slot_id": "A01",
                "hard_blockers": [],
            },
            "t2": {
                "candidate_subject_id": "s1",
                "stable_anonymous_slot_id": "A02",
                "hard_blockers": [],
            },
        }
        bindings, conflicts = _slot_roster_bindings(
            stable,
            {"s1": [_decision("s1", "assign_roster_player", "p1")]},
            {"decisions": []},
            {"p1": {"team_label": "A"}},
        )
        self.assertEqual(bindings, {})
        self.assertEqual(conflicts, set())

    def test_subject_propagation_blocker_prevents_legacy_slot_binding(self) -> None:
        bindings, conflicts = _slot_roster_bindings(
            {
                "t1": {
                    "candidate_subject_id": "s1",
                    "stable_anonymous_slot_id": "A02",
                    "hard_blockers": [],
                    "subject_propagation_blockers": [
                        "ambiguous_candidate_subject_membership"
                    ],
                }
            },
            {"s1": [_decision("s1", "assign_roster_player", "p1")]},
            {"decisions": []},
            {"p1": {"team_label": "A"}},
        )
        self.assertEqual(bindings, {})
        self.assertEqual(conflicts, set())

    def test_assign_team_preserves_slot_roster_binding(self) -> None:
        with _workspace() as root:
            _write_inputs(root, decisions=[])
            (root / "global_identity.json").write_text(
                json.dumps(
                    {
                        "slots": [
                            {
                                "stable_player_id": "A02",
                                "team_label": "A",
                                "tracklet_ids": ["t1", "t2"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            candidates = json.loads(
                (root / "identity_candidate_shadow.json").read_text(encoding="utf-8")
            )
            save_reviewed_slot_assignments(
                root,
                candidates,
                [
                    {
                        "candidate_subject_id": "s1",
                        "action": "assign_roster_player",
                        "player_id": "p1",
                        "team_label": "A",
                        "stable_slot_id": "A02",
                    }
                ],
            )
            save_reviewed_slot_assignments(
                root,
                candidates,
                [
                    {
                        "candidate_subject_id": "s2",
                        "action": "assign_team",
                        "team_label": "A",
                    }
                ],
            )
            rows = {
                row["tracklet_id"]: row
                for row in finalize_reviewed_identity(root, _match())[
                    "tracklet_assignments"
                ]
            }
            self.assertEqual(rows["t2"]["stable_anonymous_slot_id"], "A02")
            self.assertEqual(rows["t2"]["team_label"], "A")
            self.assertEqual(rows["t2"]["canonical_player_id"], "p1")
            self.assertEqual(rows["t2"]["display_label"], "Paweł")

    def test_explicit_unresolved_suppresses_slot_roster_binding(self) -> None:
        with _workspace() as root:
            _write_inputs(root, decisions=[])
            (root / "global_identity.json").write_text(
                json.dumps(
                    {
                        "slots": [
                            {
                                "stable_player_id": "A02",
                                "team_label": "A",
                                "tracklet_ids": ["t1", "t2"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            candidates = json.loads(
                (root / "identity_candidate_shadow.json").read_text(encoding="utf-8")
            )
            save_reviewed_slot_assignments(
                root,
                candidates,
                [
                    {
                        "candidate_subject_id": "s1",
                        "action": "assign_roster_player",
                        "player_id": "p1",
                        "team_label": "A",
                        "stable_slot_id": "A02",
                    }
                ],
            )
            save_reviewed_slot_assignments(
                root,
                candidates,
                [{"candidate_subject_id": "s2", "action": "unresolved"}],
            )
            rows = {
                row["tracklet_id"]: row
                for row in finalize_reviewed_identity(root, _match())[
                    "tracklet_assignments"
                ]
            }
            self.assertEqual(rows["t1"]["canonical_player_id"], "p1")
            self.assertIsNone(rows["t2"]["canonical_player_id"])
            self.assertEqual(rows["t2"]["identity_status"], "unresolved")

    def test_assign_team_with_opposite_team_is_rejected(self) -> None:
        with _workspace() as root:
            _write_inputs(root, decisions=[])
            candidates = json.loads(
                (root / "identity_candidate_shadow.json").read_text(encoding="utf-8")
            )
            with self.assertRaisesRegex(ValueError, "team mismatch"):
                save_reviewed_slot_assignments(
                    root,
                    candidates,
                    [
                        {
                            "candidate_subject_id": "s1",
                            "action": "assign_team",
                            "team_label": "B",
                        }
                    ],
                )

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
                {"frame": 0, "status": "detected", "play_area_status": "inside_play"},
                {"frame": 1, "status": "detected", "play_area_status": "inside_play"},
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

    def test_lookup_returns_only_real_detected_observation_at_frame(self) -> None:
        tracklets = {
            "t1": {
                "tracklet_id": "t1",
                "positions_m": [
                    {"frame": 1, "status": "detected", "play_area_status": "inside_play"},
                    {"frame": 5, "status": "predicted", "source": "predicted"},
                    {"frame": 10, "status": "detected", "play_area_status": "inside_play"},
                ],
            }
        }
        snapshot = {
            "tracklet_assignments": [
                {
                    "tracklet_id": "t1",
                    "identity_status": "unresolved",
                    "fallback_label": "A01",
                    "display_label": "A01",
                }
            ],
            "observation_overrides": [],
            "observation_demotions": [],
        }
        self.assertEqual(reviewed_assignment_at(snapshot, tracklets, 0.5, 10), [])
        self.assertEqual(
            reviewed_assignment_at(snapshot, tracklets, 1.0, 10)[0]["display_label"],
            "A01",
        )

    def test_lookup_applies_final_safety_after_exact_override(self) -> None:
        tracklets = {
            "t1": {
                "tracklet_id": "t1",
                "positions_m": [{"frame": 10, "status": "detected", "play_area_status": "inside_play"}],
            }
        }
        snapshot = {
            "tracklet_assignments": [
                {
                    "tracklet_id": "t1",
                    "identity_status": "unresolved",
                    "fallback_label": "A01",
                    "display_label": "A01",
                }
            ],
            "observation_overrides": [
                {
                    "tracklet_id": "t1",
                    "frame": 10,
                    "identity_status": "confirmed",
                    "canonical_player_id": "p1",
                    "display_label": "Player One",
                }
            ],
            "observation_demotions": [
                {
                    "tracklet_id": "t1",
                    "frame": 10,
                    "identity_status": "conflicted",
                    "canonical_player_id": None,
                    "display_label": "A01 !",
                }
            ],
        }
        entity = reviewed_assignment_at(snapshot, tracklets, 1.0, 10)[0]
        self.assertEqual(entity["identity_status"], "conflicted")
        self.assertIsNone(entity["canonical_player_id"])
        self.assertEqual(entity["display_label"], "A01 !")


class _workspace:
    def __enter__(self) -> Path:
        self._temp = tempfile.TemporaryDirectory(); return Path(self._temp.name)
    def __exit__(self, *args: object) -> None: self._temp.cleanup()


def _match() -> dict:
    return {"id": "m1", "teams": [{"id": "ta", "players": [{"id": "p1", "name": "Paweł", "number": "8"}, {"id": "p3", "name": "Kuba", "number": "10"}, {"id": "p4", "name": "Roman", "number": "11"}]}, {"id": "tb", "players": [{"id": "p2", "name": "Opponent"}]}]}
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


def _write_shared_slot_fixture(root: Path, *, include_unreviewed_subject: bool) -> None:
    tracklets = json.loads((root / "tracklets.json").read_text())
    candidates = json.loads((root / "identity_candidate_shadow.json").read_text())
    tracklet_ids = ["t1", "t2"]
    if include_unreviewed_subject:
        tracklets["tracklets"].append(_tracklet("t3", "A", "ta", 20))
        candidates["subjects"].append(
            {"candidate_subject_id": "s3", "tracklet_ids": ["t3"]}
        )
        tracklet_ids.append("t3")
    for tracklet in tracklets["tracklets"]:
        tracklet["positions_m"] = [
            {
                "frame": int(tracklet["start_frame"]),
                "status": "detected",
                "source": "detected",
                "play_area_status": "inside_play",
            }
        ]
    (root / "tracklets.json").write_text(json.dumps(tracklets))
    (root / "identity_candidate_shadow.json").write_text(json.dumps(candidates))
    (root / "global_identity.json").write_text(
        json.dumps(
            {
                "slots": [
                    {
                        "stable_player_id": "A03",
                        "team_label": "A",
                        "tracklet_ids": tracklet_ids,
                    }
                ]
            }
        )
    )


def _set_shared_slot_frame(root: Path, frame: int) -> None:
    tracklets = json.loads((root / "tracklets.json").read_text())
    for tracklet in tracklets["tracklets"]:
        tracklet["positions_m"] = [
            {
                "frame": frame,
                "status": "detected",
                "source": "detected",
                "play_area_status": "inside_play",
            }
        ]
    (root / "tracklets.json").write_text(json.dumps(tracklets))


def _exact_seed(tracklet_id: str, frame: int, player_id: str) -> dict:
    return {
        "observation_key": f"{tracklet_id}:{frame}",
        "frame_number": frame,
        "action": "assign_roster_player",
        "assigned_team": {"team_label": "A"},
        "assigned_player": {"player_id": player_id},
        "provenance": {"tracklet_id": tracklet_id},
    }
