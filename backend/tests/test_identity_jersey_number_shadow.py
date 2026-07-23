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
    stable_key,
)
from app.services.identity_jersey_number_recognizer_shadow import (
    build_identity_jersey_number_recognizer_shadow,
)
from app.services.identity_jersey_number_heldout_validation import (
    build_identity_jersey_number_heldout_case_contract,
    build_identity_jersey_number_heldout_validation,
)


def match_doc(*, duplicate: bool = False) -> dict:
    players = [
        {"id": "p92", "name": "Pawel", "number": 92},
        {"id": "p15", "name": "Piotrek", "number": 15},
    ]
    if duplicate:
        players.append({"id": "p92b", "name": "Other", "number": 92})
    return {
        "source_match_key": "match-1",
        "teams": [{"id": "ta", "name": "Corgi", "team_label": "A", "players": players}],
    }


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
        self.assertNotIn(_roster_lookup_key("match-1", "ta", "92"), roster["unique_number_lookup"])

    def test_roster_missing_scope_is_conflict_and_untrusted(self) -> None:
        for match, reason in (
            ({"teams": [{"id": "ta", "team_label": "A", "players": [{"id": "p1", "number": 92}]}]}, "missing_source_match_key"),
            ({"source_match_key": "match-1", "teams": [{"team_label": "A", "players": [{"id": "p1", "number": 92}]}]}, "missing_team_id"),
        ):
            roster = build_identity_jersey_number_roster_shadow(match, generated_at="fixed")

            row = roster["players"][0]
            self.assertEqual(row["roster_number_status"], "conflict")
            self.assertFalse(row["jersey_number_trusted"])
            self.assertIn(reason, row["conflicts"])
            self.assertEqual(roster["summary"]["untrusted_scope_rows"], 1)
            self.assertTrue(roster["gates"]["scope_required_for_trust"])
            self.assertEqual(roster["unique_number_lookup"], {})

    def test_roster_scopes_same_label_number_by_match_and_team(self) -> None:
        first = build_identity_jersey_number_roster_shadow(
            {
                "source_match_key": "match-one",
                "teams": [{"id": "team-one", "team_label": "A", "players": [{"id": "p1", "number": 92}]}],
            },
            generated_at="fixed",
        )
        second = build_identity_jersey_number_roster_shadow(
            {
                "source_match_key": "match-two",
                "teams": [{"id": "team-two", "team_label": "A", "players": [{"id": "p2", "number": 92}]}],
            },
            generated_at="fixed",
        )

        first_key = _roster_lookup_key("match-one", "team-one", "92")
        second_key = _roster_lookup_key("match-two", "team-two", "92")
        self.assertNotEqual(first_key, second_key)
        self.assertEqual(first["unique_number_lookup"][first_key]["player_id"], "p1")
        self.assertEqual(second["unique_number_lookup"][second_key]["player_id"], "p2")
        self.assertEqual(first["players"][0]["source_match_key"], "match-one")
        self.assertTrue(first["players"][0]["jersey_number_trusted"])
        self.assertEqual(second["unique_number_lookup"][second_key]["team_id"], "team-two")

    def test_missing_recognizer_result_is_unreadable_not_absent(self) -> None:
        roster = build_identity_jersey_number_roster_shadow(match_doc(), generated_at="fixed")
        documents = build_identity_jersey_number_evidence_shadow(
            {"cards": [{"candidate_subject_id": "s1", "team_label": "A", "source_match_key": "match-1", "source_video_key": "video-1", "team_id": "ta", "anchor_crops": [crop(10)]}]},
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
            {"cards": [{"candidate_subject_id": "s1", "team_label": "A", "source_match_key": "match-1", "source_video_key": "video-1", "team_id": "ta", "anchor_crops": [crop(10), rejected]}]},
            roster,
            generated_at="fixed",
        )

        self.assertEqual(len(documents["identity_jersey_number_evidence_shadow"]["evidence"]), 2)
        self.assertEqual(len(documents["identity_jersey_number_audit"]["cards"]), 1)
        self.assertEqual(documents["identity_jersey_number_audit"]["summary"]["excluded_unreliable_cards"], 1)

    def test_absent_requires_visible_number_panel(self) -> None:
        roster = build_identity_jersey_number_roster_shadow(match_doc(), generated_at="fixed")
        documents = build_identity_jersey_number_evidence_shadow(
            {"cards": [{"candidate_subject_id": "s1", "team_label": "A", "source_match_key": "match-1", "source_video_key": "video-1", "team_id": "ta", "anchor_crops": [crop(10)]}]},
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
            {"cards": [{"candidate_subject_id": "s1", "team_label": "A", "source_match_key": "match-1", "source_video_key": "video-1", "team_id": "ta", "anchor_crops": [crop(10)]}]},
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
            {"cards": [{"candidate_subject_id": "s1", "team_label": "A", "source_match_key": "match-1", "source_video_key": "video-1", "team_id": "ta", "anchor_crops": crops}]},
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

    def test_missing_or_unknown_scope_abstains_from_consensus(self) -> None:
        roster = build_identity_jersey_number_roster_shadow(match_doc(), generated_at="fixed")
        crops = [crop(10), crop(70), crop(130)]
        observations = {
            "observations": [
                {"anchor_crop_id": row["anchor_crop_id"], "state": "number_confirmed", "number": 92, "confidence": 0.97}
                for row in crops
            ]
        }
        for scope in ({}, {"source_match_key": "match-1", "source_video_key": "video-1", "team_id": "ta", "team_label": "U"}):
            card = {"candidate_subject_id": "s1", "team_label": "A", "anchor_crops": crops, **scope}
            evidence = build_identity_jersey_number_evidence_shadow(
                {"cards": [card]}, roster, observations_doc=observations, generated_at="fixed"
            )["identity_jersey_number_evidence_shadow"]
            consensus = build_identity_jersey_number_consensus_shadow(
                evidence, roster, generated_at="fixed"
            )["identity_jersey_number_consensus_shadow"]

            row = consensus["subjects"][0]
            self.assertFalse(row["strong_consensus"])
            self.assertIsNone(row["roster_match"])

    def test_wrong_scope_cannot_roster_confirm(self) -> None:
        roster = build_identity_jersey_number_roster_shadow(match_doc(), generated_at="fixed")
        crops = [crop(10), crop(70), crop(130)]
        observations = {
            "observations": [
                {"anchor_crop_id": row["anchor_crop_id"], "state": "number_confirmed", "number": 92, "confidence": 0.97}
                for row in crops
            ]
        }
        for scope in (
            {"source_match_key": "wrong-match", "source_video_key": "video-1", "team_id": "ta"},
            {"source_match_key": "match-1", "source_video_key": "video-1", "team_id": "wrong-team"},
        ):
            evidence = build_identity_jersey_number_evidence_shadow(
                {"cards": [{"candidate_subject_id": "s1", "team_label": "A", "anchor_crops": crops, **scope}]},
                roster,
                observations_doc=observations,
                generated_at="fixed",
            )["identity_jersey_number_evidence_shadow"]
            consensus = build_identity_jersey_number_consensus_shadow(
                evidence, roster, generated_at="fixed"
            )["identity_jersey_number_consensus_shadow"]

            row = consensus["subjects"][0]
            self.assertFalse(row["strong_consensus"])
            self.assertIsNone(row["roster_match"])

    def test_team_label_mismatch_cannot_roster_confirm(self) -> None:
        roster = build_identity_jersey_number_roster_shadow(match_doc(), generated_at="fixed")
        crops = [crop(10), crop(70), crop(130)]
        observations = {
            "observations": [
                {"anchor_crop_id": row["anchor_crop_id"], "state": "number_confirmed", "number": 92, "confidence": 0.97}
                for row in crops
            ]
        }
        evidence = build_identity_jersey_number_evidence_shadow(
            {"cards": [{
                "candidate_subject_id": "s1",
                "team_label": "B",
                "source_match_key": "match-1",
                "source_video_key": "video-1",
                "team_id": "ta",
                "anchor_crops": crops,
            }]},
            roster,
            observations_doc=observations,
            generated_at="fixed",
        )["identity_jersey_number_evidence_shadow"]
        consensus = build_identity_jersey_number_consensus_shadow(
            evidence, roster, generated_at="fixed"
        )["identity_jersey_number_consensus_shadow"]

        row = consensus["subjects"][0]
        self.assertFalse(row["strong_consensus"])
        self.assertIsNone(row["roster_match"])
        self.assertTrue(all("roster_lookup_scope_mismatch" in item["reason_codes"] for item in evidence["evidence"]))

    def test_consensus_does_not_coalesce_colliding_cross_match_evidence(self) -> None:
        roster = build_identity_jersey_number_roster_shadow(match_doc(), generated_at="fixed")
        evidence = {
            "evidence": [
                {
                    "evidence_key": f"{source_match_key}-{frame}",
                    "candidate_subject_id": "shared-subject",
                    "tracklet_id": "shared-tracklet",
                    "team_label": "A",
                    "source_match_key": source_match_key,
                    "source_video_key": f"video-{source_match_key}",
                    "team_id": team_id,
                    "frame": frame,
                    "visibility_episode_id": f"episode-{frame}",
                    "quality": {"eligible": True},
                    "state": "number_confirmed",
                    "number": "92",
                    "confidence": 0.97,
                }
                for source_match_key, team_id in (("match-1", "ta"), ("match-2", "tb"))
                for frame in (10, 70, 130)
            ]
        }
        consensus = build_identity_jersey_number_consensus_shadow(
            evidence, roster, generated_at="fixed"
        )["identity_jersey_number_consensus_shadow"]

        rows = consensus["subjects"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(consensus["tracklets"]), 2)
        self.assertEqual({row["source_match_key"] for row in rows}, {"match-1", "match-2"})
        self.assertTrue(all(row["supporting_reads"] == 3 for row in rows))
        self.assertTrue(next(row for row in rows if row["source_match_key"] == "match-1")["strong_consensus"])
        self.assertFalse(next(row for row in rows if row["source_match_key"] == "match-2")["strong_consensus"])

    def test_goldset_cross_match_subject_collision_cannot_hide_false_assignment(self) -> None:
        roster = {
            "unique_number_lookup": {
                _roster_lookup_key("match-1", "ta", "92"): {
                    "source_match_key": "match-1", "team_id": "ta", "team_label": "A", "player_id": "p1",
                },
                _roster_lookup_key("match-2", "tb", "92"): {
                    "source_match_key": "match-2", "team_id": "tb", "team_label": "A", "player_id": "p2",
                },
            }
        }
        evidence = {
            "evidence": [
                {
                    "evidence_key": f"{source_match_key}-{frame}",
                    "candidate_subject_id": "shared-subject",
                    "tracklet_id": "shared-tracklet",
                    "team_label": "A",
                    "source_match_key": source_match_key,
                    "source_video_key": f"video-{source_match_key}",
                    "team_id": team_id,
                    "frame": frame,
                    "visibility_episode_id": f"episode-{frame}",
                    "quality": {"eligible": True},
                    "state": "number_confirmed",
                    "number": "92",
                    "confidence": 0.97,
                }
                for source_match_key, team_id in (("match-1", "ta"), ("match-2", "tb"))
                for frame in (10, 70, 130)
            ]
        }
        result = build_identity_jersey_number_consensus_shadow(
            evidence,
            roster,
            goldset_doc={"subjects": [
                {"candidate_subject_id": "shared-subject", "source_match_key": "match-1", "source_video_key": "video-match-1", "team_id": "ta", "team_label": "A", "jersey_number": "15"},
                {"candidate_subject_id": "shared-subject", "source_match_key": "match-2", "source_video_key": "video-match-2", "team_id": "tb", "team_label": "A", "jersey_number": "92"},
            ]},
            generated_at="fixed",
        )["identity_jersey_number_report"]["goldset_evaluation"]

        self.assertTrue(result["scoped_subject_identity_available"])
        self.assertEqual(result["expected_subjects"], 2)
        self.assertEqual(result["identity_false_assignments"], 1)

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
            "matching_canonical_heldout_case_missing",
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

        self.assertEqual(candidate["status"], "disabled")
        self.assertEqual(candidate["suggestions"], [])
        self.assertEqual(candidate["safety"]["automatic_assignments"], 0)
        self.assertFalse(candidate["safety"]["mutates_candidate_identity"])
        self.assertIn(
            "matching_canonical_heldout_case_missing",
            candidate["safety"]["reason_codes"],
        )

    def test_candidate_integration_emits_reversible_suggestion_from_canonical_case(self) -> None:
        assignment = {
            "safety": {"benchmark_gate": {"passed": True}},
            "candidates": [{
                "candidate_subject_id": "s1",
                "strictly_eligible": True,
                "blockers": [],
                "player_id": "p15",
                "player_name": "Piotrek",
                "team_label": "A",
                "jersey_number": "15",
            }],
        }
        propagation = {
            "status": "fresh",
            "safety": {"lineage_gate": {"passed": True}},
            "summary": {"cross_subject_propagations": 0, "automatic_assignments": 0},
            "subjects": [{
                "candidate_subject_id": "s1",
                "subject_blockers": [],
                "number_seed_tracklet_ids": ["t1"],
                "number_propagated_tracklet_ids": ["t2"],
            }],
        }
        targeted = {
            "summary": {
                "safety_passed": True,
                "eligible_matched_hidden_target_tracklets": 1,
                "unexpected_propagated_tracklets": 0,
                "automatic_assignments": 0,
            }
        }
        recognizer = {"calibration": {"calibration_status": "measured", "total_false_confirmed_reads": 0}}
        hashes: dict[str, str | None] = {
            "global_identity.json": "same-global",
            "stable_players.json": "same-stable",
            "player_identity_assignments.json": "same-assignments",
        }
        heldout = build_identity_jersey_number_heldout_validation(
            [{"case_contract_doc": build_identity_jersey_number_heldout_case_contract(
                benchmark_id=f"case-{source_match_key}",
                source_match_key=source_match_key,
                recognizer_doc={**recognizer, "heldout_source_match_key": source_match_key},
                assignment_doc=assignment,
                propagation_doc=propagation,
                targeted_evaluation_doc=targeted,
                production_before=hashes,
                production_after=hashes,
                generated_at="fixed",
            )} for source_match_key in ("match-1", "match-2")],
            generated_at="fixed",
        )

        candidate = build_identity_jersey_number_candidate_integration_shadow(
            assignment,
            propagation,
            targeted_evaluation_doc=targeted,
            heldout_validation_doc=heldout,
            activation_requested=True,
            generated_at="fixed",
        )

        self.assertEqual(candidate["status"], "ready_shadow")
        self.assertEqual(len(candidate["suggestions"]), 1)
        self.assertFalse(candidate["suggestions"][0]["automatic_assignment"])
        self.assertEqual(candidate["safety"]["automatic_assignments"], 0)

    def test_recognizer_does_not_hallucinate_when_crop_is_missing(self) -> None:
        recognizer = build_identity_jersey_number_recognizer_shadow(
            {"cards": [{"candidate_subject_id": "s1", "team_label": "A", "source_match_key": "match-1", "team_id": "ta", "anchor_crops": [crop(10)]}]},
            build_identity_jersey_number_roster_shadow(match_doc(), generated_at="fixed"),
            crop_root=Path("/does/not/exist"),
            generated_at="fixed",
        )

        row = recognizer["observations"][0]
        self.assertEqual(row["state"], "number_unreadable")
        self.assertIsNone(row["number"])
        self.assertFalse(row["number_panel_visible"])

    def test_recognizer_roster_candidates_are_match_and_team_scoped(self) -> None:
        roster = {
            "players": [
                {
                    "source_match_key": "match-1",
                    "team_id": "team-1",
                    "team_label": "A",
                    "jersey_number": "10",
                    "jersey_number_trusted": True,
                    "roster_number_status": "confirmed",
                },
                {
                    "source_match_key": "match-2",
                    "team_id": "team-2",
                    "team_label": "A",
                    "jersey_number": "99",
                    "jersey_number_trusted": True,
                    "roster_number_status": "confirmed",
                },
            ]
        }
        recognizer = build_identity_jersey_number_recognizer_shadow(
            {
                "cards": [
                    {
                        "candidate_subject_id": "s1",
                        "source_match_key": "match-1",
                        "source_video_key": "video-1",
                        "team_id": "team-1",
                        "team_label": "A",
                        "anchor_crops": [crop(10)],
                    }
                ]
            },
            roster,
            crop_root=Path("/does/not/exist"),
            generated_at="fixed",
        )

        self.assertEqual(recognizer["observations"][0]["candidate_scope"]["roster_numbers"], ["10"])

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
            scoped_roster = build_identity_jersey_number_roster_shadow(
                {
                    "source_match_key": "match-1",
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
                    "cards": [_recognizer_subject(_canonical_episode_crops(first.name, second.name))]
                },
                scoped_roster,
                crop_root=root,
                generated_at="fixed",
                parameters={"minimum_template_score": 0.05, "minimum_score_margin": 0.0},
            )

        self.assertEqual(one_frame["observations"][0]["state"], "number_unreadable")
        self.assertEqual(
            one_frame["observations"][0]["reason_codes"],
            ["insufficient_temporal_episode_consensus"],
        )
        self.assertEqual(two_frames["summary"]["confirmed_numbers"], 3)
        self.assertTrue(all(row["number"] == "10" for row in two_frames["observations"]))
        self.assertEqual(
            {row["visibility_episode_id"] for row in two_frames["observations"]},
            {"episode-3509"},
        )
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
        "source_match_key": "match-1",
        "source_video_key": "video-1",
        "team_id": "ta",
        "anchor_crops": crops,
    }


def _canonical_episode_crops(first_artifact: str, second_artifact: str) -> list[dict]:
    crops = [
        _recognizer_crop("first", 3509, first_artifact),
        _recognizer_crop("second", 3510, second_artifact),
        _recognizer_crop("third", 3512, first_artifact),
    ]
    for row in crops:
        row["visibility_episode_id"] = "episode-3509"
    return crops


def _roster_lookup_key(source_match_key: str, team_id: str, jersey_number: str) -> str:
    return stable_key(
        "jersey-roster-lookup",
        {
            "source_match_key": source_match_key,
            "team_id": team_id,
            "jersey_number": jersey_number,
        },
    )


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
