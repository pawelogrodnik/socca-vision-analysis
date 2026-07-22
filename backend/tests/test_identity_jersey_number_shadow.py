from __future__ import annotations

import unittest

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
from app.services.identity_jersey_number_common import canonical_digest


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

    def test_absent_requires_visible_clean_jersey(self) -> None:
        roster = build_identity_jersey_number_roster_shadow(match_doc(), generated_at="fixed")
        documents = build_identity_jersey_number_evidence_shadow(
            {"cards": [{"candidate_subject_id": "s1", "team_label": "A", "anchor_crops": [crop(10)]}]},
            roster,
            observations_doc={"observations": [{"anchor_crop_id": "crop-10", "state": "number_absent", "confidence": 1.0}]},
            generated_at="fixed",
        )
        row = documents["identity_jersey_number_evidence_shadow"]["evidence"][0]

        self.assertEqual(row["state"], "number_unreadable")
        self.assertIn("number_absent_without_clean_jersey_evidence", row["reason_codes"])

    def test_consensus_requires_multiple_independent_reads(self) -> None:
        roster = build_identity_jersey_number_roster_shadow(match_doc(), generated_at="fixed")
        crops = [crop(10), crop(30), crop(50)]
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
        consensus = {
            "source": {"evidence_digest": "evidence", "roster_digest": "roster", "goldset_digest": "gold"},
            "subjects": [{
                "candidate_subject_id": "s1", "team_label": "A", "strong_consensus": True,
                "consensus_number": "92", "consensus_confidence": 0.98, "supporting_reads": 3,
                "conflicting_reads": 0,
                "roster_match": {"team_label": "A", "player_id": "p92", "player_name": "Pawel"},
            }],
        }
        review = {
            "source": {"jersey_consensus_digest": canonical_digest(consensus)},
            "cards": [{
                "candidate_subject_id": "s1", "recommended_player": {"player_id": "p92"},
                "blockers": [], "quality_flags": [], "reason_codes": [],
            }],
        }
        report = {
            "source": dict(consensus["source"]),
            "goldset_evaluation": {"available": True, "expected_subjects": 8, "identity_false_assignments": 0},
        }

        result = build_identity_jersey_number_assignment_shadow(
            consensus, review, report, activation_requested=True, generated_at="fixed"
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
        assignment, evidence, candidate, timeline = _propagation_documents()
        timeline["subjects"][0]["tracklet_ids"] = ["t1"]

        result = build_identity_jersey_number_propagation_shadow(
            assignment, evidence, candidate, timeline, generated_at="fixed"
        )

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
        first = build_identity_jersey_number_propagation_shadow(
            *documents, generated_at="fixed"
        )
        second = build_identity_jersey_number_propagation_shadow(
            *documents, generated_at="fixed"
        )

        self.assertEqual(first, second)
        self.assertEqual(documents, originals)


def _propagation_documents(
    *,
    event_overrides: dict | None = None,
    include_event: bool = True,
    extra_evidence: list[dict] | None = None,
) -> tuple[dict, dict, dict, dict]:
    assignment = {
        "candidates": [{
            "candidate_subject_id": "s1",
            "team_label": "A",
            "jersey_number": "92",
            "player_id": "p92",
            "player_name": "Pawel",
            "strictly_eligible": True,
        }]
    }
    evidence_rows = [{
        "candidate_subject_id": "s1",
        "team_label": "A",
        "tracklet_id": "t1",
        "state": "number_confirmed",
        "number": "92",
    }]
    evidence_rows.extend(extra_evidence or [])
    evidence = {"evidence": evidence_rows}
    candidate = {
        "subjects": [{
            "candidate_subject_id": "s1",
            "team_label": "A",
            "tracklet_ids": ["t1", "t2"],
            "quality_flags": [],
        }]
    }
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
    timeline = {
        "subjects": [{"shadow_subject_id": "s1", "team_label": "A", "tracklet_ids": ["t1", "t2"]}],
        "transition_events": [event] if include_event else [],
    }
    return assignment, evidence, candidate, timeline


def _propagation_result(**kwargs) -> dict:
    return build_identity_jersey_number_propagation_shadow(
        *_propagation_documents(**kwargs), generated_at="fixed"
    )


if __name__ == "__main__":
    unittest.main()
