from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import numpy as np

from app.services.identity_jersey_number_assignment_shadow import (
    build_identity_jersey_number_assignment_shadow,
)
from app.services.identity_jersey_number_consensus_shadow import (
    build_identity_jersey_number_consensus_shadow,
)
from app.services.identity_jersey_number_evidence_shadow import (
    build_identity_jersey_number_evidence_shadow,
)
from app.services.identity_jersey_number_roster import (
    build_identity_jersey_number_roster_shadow,
)
from app.services.identity_jersey_number_propagation_shadow import (
    build_identity_jersey_number_propagation_shadow,
)
from app.services.identity_jersey_number_candidate_shadow import (
    build_identity_jersey_number_candidate_integration_shadow,
)
from app.services.identity_jersey_number_common import (
    canonical_digest,
    lineage_entry,
)
from app.services.identity_jersey_number_recognizer_shadow import (
    build_identity_jersey_number_recognizer_shadow,
)


def match_doc(*, duplicate: bool = False) -> dict:
    players = [
        {"id": "p92", "name": "Pawel", "number": 92},
        {"id": "p15", "name": "Piotrek", "number": 15},
    ]
    if duplicate:
        players.append({"id": "p92b", "name": "Other", "number": 92})
    return {"teams": [{"id": "ta", "name": "Corgi", "team_label": "A", "players": players}]}


def crop(frame: int, *, tracklet: str = "t1") -> dict:
    return {
        "anchor_crop_id": f"crop-{frame}",
        "tracklet_id": tracklet,
        "frame": frame,
        "time_sec": frame / 30,
        "bbox_xyxy": [10, 10, 60, 130],
        "artifact": f"crops/{frame}.jpg",
        "selection_eligible": True,
        "quality_class": "trusted",
        "detection_confidence": 0.95,
        "appearance_reliable_ratio": 0.92,
    }


class JerseyNumberShadowTests(unittest.TestCase):
    def test_roster_duplicate_number_blocks_trust(self) -> None:
        roster = build_identity_jersey_number_roster_shadow(match_doc(duplicate=True), generated_at="fixed")

        rows = [row for row in roster["players"] if row["jersey_number"] == "92"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["roster_number_status"] == "conflict" for row in rows))
        self.assertNotIn("A:92", roster["unique_number_lookup"])

    def test_missing_recognizer_result_is_unreadable_not_absent(self) -> None:
        roster = build_identity_jersey_number_roster_shadow(match_doc(), generated_at="fixed")
        documents = build_identity_jersey_number_evidence_shadow(
            {"cards": [{"candidate_subject_id": "s1", "team_label": "A", "anchor_crops": [crop(10)]}]},
            roster,
            generated_at="fixed",
        )
        row = documents["identity_jersey_number_evidence_shadow"]["evidence"][0]

        self.assertEqual(row["state"], "number_unreadable")
        self.assertIn("recognizer_not_run", row["reason_codes"])

    def test_audit_contains_only_reliable_crops(self) -> None:
        roster = build_identity_jersey_number_roster_shadow(match_doc(), generated_at="fixed")
        rejected = crop(20)
        rejected["detection_confidence"] = 0.2
        documents = build_identity_jersey_number_evidence_shadow(
            {"cards": [{"candidate_subject_id": "s1", "team_label": "A", "anchor_crops": [crop(10), rejected]}]},
            roster,
            generated_at="fixed",
        )

        self.assertEqual(len(documents["identity_jersey_number_evidence_shadow"]["evidence"]), 2)
        self.assertEqual(len(documents["identity_jersey_number_audit"]["cards"]), 1)
        self.assertEqual(documents["identity_jersey_number_audit"]["summary"]["excluded_unreliable_cards"], 1)

    def test_absent_requires_visible_number_panel(self) -> None:
        roster = build_identity_jersey_number_roster_shadow(match_doc(), generated_at="fixed")
        documents = build_identity_jersey_number_evidence_shadow(
            {"cards": [{"candidate_subject_id": "s1", "team_label": "A", "anchor_crops": [crop(10)]}]},
            roster,
            observations_doc={"observations": [{"anchor_crop_id": "crop-10", "state": "number_absent", "confidence": 1.0}]},
            generated_at="fixed",
        )
        row = documents["identity_jersey_number_evidence_shadow"]["evidence"][0]

        self.assertEqual(row["state"], "number_unreadable")
        self.assertIn("number_absent_without_number_panel_evidence", row["reason_codes"])

    def test_visible_number_panel_allows_explicit_absent_state(self) -> None:
        roster = build_identity_jersey_number_roster_shadow(match_doc(), generated_at="fixed")
        documents = build_identity_jersey_number_evidence_shadow(
            {"cards": [{"candidate_subject_id": "s1", "team_label": "A", "anchor_crops": [crop(10)]}]},
            roster,
            observations_doc={
                "observations": [{
                    "anchor_crop_id": "crop-10",
                    "state": "number_absent",
                    "confidence": 1.0,
                    "view": "back",
                    "clean_jersey_visible": True,
                    "number_panel_visible": True,
                }]
            },
            generated_at="fixed",
        )

        row = documents["identity_jersey_number_evidence_shadow"]["evidence"][0]
        self.assertEqual(row["state"], "number_absent")

    def test_consensus_requires_multiple_independent_reads(self) -> None:
        roster = build_identity_jersey_number_roster_shadow(match_doc(), generated_at="fixed")
        crops = [crop(10), crop(70), crop(130)]
        observations = {
            "observations": [
                {"anchor_crop_id": row["anchor_crop_id"], "state": "number_confirmed", "number": 92, "confidence": 0.97}
                for row in crops
            ]
        }
        evidence = build_identity_jersey_number_evidence_shadow(
            {"cards": [{"candidate_subject_id": "s1", "team_label": "A", "anchor_crops": crops}]},
            roster,
            observations_doc=observations,
            generated_at="fixed",
        )["identity_jersey_number_evidence_shadow"]
        consensus = build_identity_jersey_number_consensus_shadow(
            evidence,
            roster,
            generated_at="fixed",
        )["identity_jersey_number_consensus_shadow"]

        row = consensus["subjects"][0]
        self.assertTrue(row["strong_consensus"])
        self.assertEqual(row["consensus_number"], "92")
        self.assertEqual(row["roster_match"]["player_id"], "p92")

    def test_n4_stays_disabled_without_zero_false_goldset(self) -> None:
        consensus = {
            "subjects": [
                {
                    "candidate_subject_id": "s1",
                    "team_label": "A",
                    "strong_consensus": True,
                    "consensus_number": "92",
                    "consensus_confidence": 0.98,
                    "supporting_reads": 3,
                    "conflicting_reads": 0,
                    "roster_match": {"team_label": "A", "player_id": "p92", "player_name": "Pawel"},
                }
            ]
        }
        review = {
            "cards": [
                {
                    "candidate_subject_id": "s1",
                    "recommended_player": {"player_id": "p92"},
                    "blockers": [],
                    "quality_flags": [],
                    "reason_codes": [],
                }
            ]
        }
        result = build_identity_jersey_number_assignment_shadow(
            consensus,
            review,
            {"goldset_evaluation": {"available": True, "expected_subjects": 10, "identity_false_assignments": 1}},
            activation_requested=True,
            generated_at="fixed",
        )

        self.assertFalse(result["safety"]["activation_enabled"])
        self.assertFalse(result["candidates"][0]["would_assign_if_enabled"])
        self.assertEqual(result["safety"]["automatic_assignments"], 0)
        self.assertIn("stale_or_missing_lineage", result["candidates"][0]["blockers"])

    def test_n4_can_become_eligible_but_never_writes_assignment(self) -> None:
        evidence = _document("evidence", {"evidence": []})
        roster = _document("roster", {"players": []})
        consensus = _document("consensus", {
            "source": {"evidence_digest": "evidence", "roster_digest": "roster", "goldset_digest": "gold"},
            "subjects": [{
                "candidate_subject_id": "s1", "team_label": "A", "strong_consensus": True,
                "consensus_number": "92", "consensus_confidence": 0.98, "supporting_reads": 3,
                "conflicting_reads": 0,
                "roster_match": {"team_label": "A", "player_id": "p92", "player_name": "Pawel"},
            }],
        })
        consensus["source"]["evidence_digest"] = canonical_digest(evidence)
        consensus["source"]["roster_digest"] = canonical_digest(roster)
        review = _document("review", {
            "source": {"jersey_consensus_digest": canonical_digest(consensus)},
            "cards": [{
                "candidate_subject_id": "s1", "recommended_player": {"player_id": "p92"},
                "blockers": [], "quality_flags": [], "reason_codes": [],
            }],
        })
        report = _document("report", {
            "source": dict(consensus["source"]),
            "goldset_evaluation": {
                "available": True,
                "reviewed_subjects": 8,
                "reviewed_numbered_subjects": 3,
                "reviewed_no_number_subjects": 1,
                "reviewed_unreadable_subjects": 1,
                "heldout_matches": 1,
                "identity_false_assignments": 0,
                "false_positive": 0,
                "precision": 1.0,
            },
        })

        result = build_identity_jersey_number_assignment_shadow(
            consensus,
            review,
            report,
            evidence_doc=evidence,
            roster_doc=roster,
            activation_requested=True,
            generated_at="fixed",
        )

        self.assertTrue(result["safety"]["activation_enabled"])
        self.assertTrue(result["candidates"][0]["strictly_eligible"])
        self.assertTrue(result["candidates"][0]["would_assign_if_enabled"])
        self.assertFalse(result["candidates"][0]["automatic_assignment"])
        self.assertEqual(result["safety"]["automatic_assignments"], 0)

    def test_n5_propagates_only_over_safe_explicit_edge(self) -> None:
        result = _propagation_result()

        self.assertEqual(result["subjects"][0]["seed_tracklet_ids"], ["t1"])
        self.assertEqual(result["subjects"][0]["propagated_tracklet_ids"], ["t2"])
        self.assertEqual(result["summary"]["safe_edges"], 1)
        self.assertEqual(result["safety"]["automatic_assignments"], 0)

    def test_n5_does_not_propagate_through_uncertain_transition(self) -> None:
        result = _propagation_result(event_overrides={"requires_review": True, "status": "uncertain"})

        self.assertEqual(result["subjects"][0]["propagated_tracklet_ids"], [])
        self.assertEqual(result["subjects"][0]["blocked_tracklet_ids"], ["t2"])
        self.assertIn("uncertain_transition", result["edge_audit"][0]["blockers"])

    def test_n5_does_not_propagate_through_cross_production_transition(self) -> None:
        result = _propagation_result(event_overrides={"current_identity_relation": "different_subjects"})

        self.assertEqual(result["subjects"][0]["propagated_tracklet_ids"], [])
        self.assertIn("cross_production_transition", result["edge_audit"][0]["blockers"])

    def test_n5_does_not_propagate_through_weak_reid_only_edge(self) -> None:
        result = _propagation_result(event_overrides={"recommendation_source": "same_match_reid"})

        self.assertEqual(result["subjects"][0]["propagated_tracklet_ids"], [])
        self.assertIn("weak_reid_only_edge", result["edge_audit"][0]["blockers"])

    def test_n5_blocks_candidate_timeline_tracklet_mismatch(self) -> None:
        documents = _propagation_documents()
        timeline = documents["timeline"]
        timeline["subjects"][0]["tracklet_ids"] = ["t1"]

        result = _build_propagation(documents)

        self.assertEqual(result["subjects"][0]["propagated_tracklet_ids"], [])
        self.assertIn(
            "candidate_timeline_tracklet_mismatch",
            result["subjects"][0]["subject_blockers"],
        )

    def test_n5_does_not_connect_disconnected_same_number_tracklet(self) -> None:
        result = _propagation_result(include_event=False)

        self.assertEqual(result["subjects"][0]["propagated_tracklet_ids"], [])
        self.assertEqual(result["subjects"][0]["blocked_tracklet_ids"], ["t2"])

    def test_n5_blocks_contradictory_number_evidence(self) -> None:
        result = _propagation_result(
            extra_evidence=[
                {
                    "candidate_subject_id": "s1",
                    "team_label": "A",
                    "tracklet_id": "t2",
                    "state": "number_confirmed",
                    "number": "15",
                }
            ]
        )

        self.assertEqual(result["subjects"][0]["propagated_tracklet_ids"], [])
        self.assertIn("contradictory_number_evidence", result["edge_audit"][0]["blockers"])

    def test_n5_is_deterministic_and_does_not_mutate_inputs(self) -> None:
        import copy

        documents = _propagation_documents()
        originals = copy.deepcopy(documents)
        first = _build_propagation(documents)
        second = _build_propagation(documents)

        self.assertEqual(first, second)
        self.assertEqual(documents, originals)

    def test_n5_blocks_stale_assignment_lineage(self) -> None:
        documents = _propagation_documents()
        documents["consensus"]["subjects"].append({"candidate_subject_id": "changed"})

        result = _build_propagation(documents)

        self.assertEqual(result["status"], "blocked")
        self.assertIn("stale_jersey_number_lineage", result["subjects"][0]["subject_blockers"])

    def test_operator_membership_is_not_reported_as_number_propagation(self) -> None:
        documents = _propagation_documents(operator_confirmed=True)

        result = _build_propagation(documents)
        subject = result["subjects"][0]

        self.assertEqual(subject["number_seed_tracklet_ids"], ["t1"])
        self.assertEqual(subject["number_propagated_tracklet_ids"], ["t2"])
        self.assertEqual(subject["operator_confirmed_tracklet_ids"], ["t1", "t2"])
        self.assertEqual(subject["operator_inherited_tracklet_ids"], [])

    def test_candidate_integration_is_disabled_by_default(self) -> None:
        documents = _propagation_documents()
        propagation = _build_propagation(documents)

        candidate = build_identity_jersey_number_candidate_integration_shadow(
            documents["assignment"], propagation, generated_at="fixed"
        )

        self.assertEqual(candidate["status"], "disabled")
        self.assertEqual(candidate["suggestions"], [])
        self.assertEqual(candidate["safety"]["automatic_assignments"], 0)

    def test_candidate_integration_requires_heldout_and_unchanged_production(self) -> None:
        candidate = build_identity_jersey_number_candidate_integration_shadow(
            {"safety": {"benchmark_gate": {"passed": True}}, "candidates": []},
            {
                "status": "fresh",
                "safety": {"lineage_gate": {"passed": True}},
                "summary": {"cross_subject_propagations": 0, "automatic_assignments": 0},
                "subjects": [],
            },
            activation_requested=True,
            generated_at="fixed",
        )

        self.assertEqual(candidate["status"], "disabled")
        self.assertIn("heldout_targeted_evaluation_missing", candidate["safety"]["reason_codes"])
        self.assertIn(
            "heldout_multi_match_validation_missing", candidate["safety"]["reason_codes"]
        )
        self.assertIn(
            "production_identity_unchanged_not_verified",
            candidate["safety"]["reason_codes"],
        )

    def test_candidate_integration_emits_only_reversible_review_suggestion(self) -> None:
        candidate = build_identity_jersey_number_candidate_integration_shadow(
            {
                "safety": {"benchmark_gate": {"passed": True}},
                "candidates": [
                    {
                        "candidate_subject_id": "s1",
                        "strictly_eligible": True,
                        "blockers": [],
                        "player_id": "p15",
                        "player_name": "Piotrek",
                        "team_label": "A",
                        "jersey_number": "15",
                    }
                ],
            },
            {
                "status": "fresh",
                "safety": {"lineage_gate": {"passed": True}},
                "summary": {"cross_subject_propagations": 0, "automatic_assignments": 0},
                "subjects": [
                    {
                        "candidate_subject_id": "s1",
                        "subject_blockers": [],
                        "number_seed_tracklet_ids": ["t1"],
                        "number_propagated_tracklet_ids": ["t2"],
                    }
                ],
            },
            targeted_evaluation_doc={
                "summary": {"safety_passed": True, "unexpected_propagated_tracklets": 0}
            },
            heldout_validation_doc={
                "summary": {"activation_gate_passed": True, "distinct_source_matches": 2}
            },
            production_identity_unchanged=True,
            activation_requested=True,
            generated_at="fixed",
        )

        self.assertEqual(candidate["status"], "ready_shadow")
        self.assertEqual(len(candidate["suggestions"]), 1)
        self.assertFalse(candidate["suggestions"][0]["automatic_assignment"])
        self.assertEqual(candidate["safety"]["automatic_assignments"], 0)
        self.assertFalse(candidate["safety"]["mutates_candidate_identity"])

    def test_recognizer_does_not_hallucinate_when_crop_is_missing(self) -> None:
        recognizer = build_identity_jersey_number_recognizer_shadow(
            {"cards": [{"candidate_subject_id": "s1", "team_label": "A", "anchor_crops": [crop(10)]}]},
            build_identity_jersey_number_roster_shadow(match_doc(), generated_at="fixed"),
            crop_root=Path("/does/not/exist"),
            generated_at="fixed",
        )

        row = recognizer["observations"][0]
        self.assertEqual(row["state"], "number_unreadable")
        self.assertIsNone(row["number"])
        self.assertFalse(row["number_panel_visible"])

    def test_shape_match_requires_multi_frame_episode_consensus(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first = _write_number_crop(root / "first.jpg")
            second = _write_number_crop(root / "second.jpg")
            roster = build_identity_jersey_number_roster_shadow(
                {
                    "teams": [{
                        "id": "ta",
                        "name": "Corgi",
                        "team_label": "A",
                        "players": [{"id": "p10", "name": "Krzysiek", "number": 10}],
                    }]
                },
                generated_at="fixed",
            )
            one_frame = build_identity_jersey_number_recognizer_shadow(
                {"cards": [_recognizer_subject([_recognizer_crop("first", 100, first.name)])]},
                roster,
                crop_root=root,
                generated_at="fixed",
                parameters={"minimum_template_score": 0.05, "minimum_score_margin": 0.0},
            )
            two_frames = build_identity_jersey_number_recognizer_shadow(
                {
                    "cards": [_recognizer_subject([
                        _recognizer_crop("first", 100, first.name),
                        _recognizer_crop("second", 103, second.name),
                    ])]
                },
                roster,
                crop_root=root,
                generated_at="fixed",
                parameters={"minimum_template_score": 0.05, "minimum_score_margin": 0.0},
            )

        self.assertEqual(one_frame["observations"][0]["state"], "number_unreadable")
        self.assertEqual(
            one_frame["observations"][0]["reason_codes"],
            ["insufficient_temporal_episode_consensus"],
        )
        self.assertEqual(two_frames["summary"]["confirmed_numbers"], 2)
        self.assertTrue(all(row["number"] == "10" for row in two_frames["observations"]))
        self.assertEqual(two_frames["safety"]["automatic_assignments"], 0)

    def test_flat_crop_does_not_expose_number_panel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            import cv2

            root = Path(temporary_directory)
            blank = np.full((160, 100, 3), (80, 130, 80), dtype=np.uint8)
            cv2.imwrite(str(root / "blank.jpg"), blank)
            roster = build_identity_jersey_number_roster_shadow(
                {
                    "teams": [{
                        "id": "ta",
                        "name": "Corgi",
                        "team_label": "A",
                        "players": [{"id": "p10", "name": "Krzysiek", "number": 10}],
                    }]
                },
                generated_at="fixed",
            )
            result = build_identity_jersey_number_recognizer_shadow(
                {
                    "cards": [_recognizer_subject([
                        _recognizer_crop("blank-1", 100, "blank.jpg"),
                        _recognizer_crop("blank-2", 102, "blank.jpg"),
                    ])]
                },
                roster,
                crop_root=root,
                generated_at="fixed",
            )

        self.assertEqual(result["summary"]["confirmed_numbers"], 0)
        self.assertTrue(all(not row["number_panel_visible"] for row in result["observations"]))

def _write_number_crop(path: Path) -> Path:
    import cv2

    image = np.full((160, 100, 3), (80, 130, 80), dtype=np.uint8)
    cv2.rectangle(image, (20, 25), (80, 135), (245, 245, 245), thickness=-1)
    cv2.putText(image, "10", (30, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (10, 10, 10), 2)
    cv2.imwrite(str(path), image)
    return path


def _recognizer_crop(crop_id: str, frame: int, artifact: str) -> dict:
    row = crop(frame, tracklet="t10")
    row.update({
        "anchor_crop_id": crop_id,
        "artifact": artifact,
        "bbox_xyxy": [0, 0, 60, 110],
    })
    return row


def _recognizer_subject(crops: list[dict]) -> dict:
    return {
        "candidate_subject_id": "subject-10",
        "team_label": "A",
        "anchor_crops": crops,
    }


def _propagation_documents(
    *,
    event_overrides: dict | None = None,
    include_event: bool = True,
    extra_evidence: list[dict] | None = None,
    operator_confirmed: bool = False,
) -> dict[str, dict]:
    evidence = _document("evidence", {"evidence": []})
    roster = _document("roster", {"players": []})
    consensus = _document("consensus", {
        "source": {
            "evidence_digest": canonical_digest(evidence),
            "roster_digest": canonical_digest(roster),
            "goldset_digest": "gold",
        },
        "subjects": [],
    })
    subject_review = _document("review", {
        "source": {"jersey_consensus_digest": canonical_digest(consensus)},
        "cards": [{
            "candidate_subject_id": "s1",
            "recommended_player": {"player_id": "p92"},
            "operator_decision": {
                "decision": "assign_roster_player" if operator_confirmed else "unresolved",
                "player_id": "p92" if operator_confirmed else None,
            },
        }],
    })
    report = _document("report", {
        "source": dict(consensus["source"]),
        "goldset_evaluation": {
            "available": True,
            "reviewed_subjects": 8,
            "reviewed_numbered_subjects": 3,
            "reviewed_no_number_subjects": 1,
            "reviewed_unreadable_subjects": 1,
            "heldout_matches": 1,
            "identity_false_assignments": 0,
            "false_positive": 0,
            "precision": 1.0,
        },
    })
    assignment = _document("assignment", {
        "candidates": [{
            "candidate_subject_id": "s1",
            "team_label": "A",
            "jersey_number": "92",
            "player_id": "p92",
            "player_name": "Pawel",
            "strictly_eligible": True,
        }],
    })
    assignment["source"] = {
        "consensus_digest": canonical_digest(consensus),
        "subject_review_digest": canonical_digest(subject_review),
        "jersey_report_digest": canonical_digest(report),
        "evidence_digest": canonical_digest(evidence),
        "roster_digest": canonical_digest(roster),
        "lineage": {
            "consensus": lineage_entry(consensus),
            "subject_review": lineage_entry(subject_review),
            "jersey_report": lineage_entry(report),
            "evidence": lineage_entry(evidence),
            "roster": lineage_entry(roster),
        },
    }
    evidence_rows = [{
        "candidate_subject_id": "s1",
        "team_label": "A",
        "tracklet_id": "t1",
        "state": "number_confirmed",
        "number": "92",
    }]
    evidence_rows.extend(extra_evidence or [])
    evidence["evidence"] = evidence_rows
    candidate = _document("candidate", {
        "subjects": [{
            "candidate_subject_id": "s1",
            "team_label": "A",
            "tracklet_ids": ["t1", "t2"],
            "quality_flags": [],
        }],
    })
    event = {
        "shadow_subject_id": "s1",
        "team_label": "A",
        "edge_key": "edge-1",
        "source_tracklet_id": "t1",
        "target_tracklet_id": "t2",
        "status": "missing",
        "recommendation_source": "stitching",
        "current_identity_relation": "same_subject",
        "requires_review": False,
        "overlap_frames": 0,
    }
    event.update(event_overrides or {})
    timeline = _document("timeline", {
        "subjects": [{"shadow_subject_id": "s1", "team_label": "A", "tracklet_ids": ["t1", "t2"]}],
        "transition_events": [event] if include_event else [],
    })
    consensus["source"]["evidence_digest"] = canonical_digest(evidence)
    consensus["source"]["roster_digest"] = canonical_digest(roster)
    subject_review["source"]["jersey_consensus_digest"] = canonical_digest(consensus)
    report["source"] = dict(consensus["source"])
    assignment["source"] = {
        "consensus_digest": canonical_digest(consensus),
        "subject_review_digest": canonical_digest(subject_review),
        "jersey_report_digest": canonical_digest(report),
        "evidence_digest": canonical_digest(evidence),
        "roster_digest": canonical_digest(roster),
        "lineage": {
            "consensus": lineage_entry(consensus),
            "subject_review": lineage_entry(subject_review),
            "jersey_report": lineage_entry(report),
            "evidence": lineage_entry(evidence),
            "roster": lineage_entry(roster),
        },
    }
    return {
        "assignment": assignment,
        "evidence": evidence,
        "candidate": candidate,
        "timeline": timeline,
        "subject_review": subject_review,
        "consensus": consensus,
        "roster": roster,
        "report": report,
    }


def _propagation_result(**kwargs) -> dict:
    return _build_propagation(_propagation_documents(**kwargs))


def _build_propagation(documents: dict[str, dict]) -> dict:
    return build_identity_jersey_number_propagation_shadow(
        documents["assignment"],
        documents["evidence"],
        documents["candidate"],
        documents["timeline"],
        subject_review_doc=documents["subject_review"],
        consensus_doc=documents["consensus"],
        roster_doc=documents["roster"],
        jersey_report_doc=documents["report"],
        generated_at="fixed",
    )


def _document(name: str, body: dict) -> dict:
    return {
        "schema_version": "test",
        "algorithm": {"name": f"test_{name}", "version": "1.0.0", "parameters": {}},
        **body,
    }


if __name__ == "__main__":
    unittest.main()
