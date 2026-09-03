from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.main import get_artifact
from app.services.identity_reviewed_coverage import apply_coverage_policy
from app.services.identity_reviewed_team_attribution_evidence import (
    FOCUSED_SOURCE_ALREADY_ACTIONABLE,
    _focused_source_failure_status,
    build_team_attribution_evidence,
    evidence_status_for_unit,
    materialize_team_attribution_evidence,
    resolve_current_team_attribution_sources,
    source_ownership_digest,
    visual_evidence_for_unit,
)
from app.services.identity_reviewed_progress import (
    _attach_team_attribution_evidence,
    materialize_reviewed_identity_units,
)
from app.services.review_workflow_orchestrator import (
    _not_materialized_team_attribution_sources,
)


class TeamAttributionEvidenceTests(unittest.TestCase):
    def test_progress_materialization_uses_trimmed_exact_source_for_team_evidence_recovery(self) -> None:
        tracklets = {"t": {"tracklet_id": "t", "team_label": "U", "positions_m": [
            _position(frame) for frame in range(1, 7)
        ]}}
        full_pairs = [("t", frame) for frame in range(1, 7)]
        current_pairs = [("t", frame) for frame in range(1, 5)]
        parent_document = build_team_attribution_evidence(
            _candidate_document(),
            {"tracklets": list(tracklets.values())},
            _review_cards_document(),
            focused_sources=[_focused_source("u", full_pairs)],
        )
        parent_document["cases"][0]["rendered_anchor_crops"] = list(
            parent_document["cases"][0]["anchor_crops"]
        )

        units = _materialized_units(
            tracklets=tracklets,
            resolved_pairs={("t", 5), ("t", 6)},
            team_evidence=parent_document,
        )
        unit = _unit_by_scope(units, "whole_subject")

        self.assertEqual(unit["detected_pairs"], current_pairs)
        self.assertEqual(
            unit["team_attribution_evidence_source_digest"],
            source_ownership_digest("u", current_pairs),
        )
        self.assertNotEqual(
            unit["team_attribution_evidence_source_digest"],
            source_ownership_digest("u", full_pairs),
        )
        # A rendered parent artifact must not be treated as evidence for the
        # smaller child scope after material ownership has been trimmed.
        self.assertEqual(
            unit["team_attribution_evidence_status"],
            "team_attribution_evidence_not_materialized",
        )

        progress = {
            "coverage_residuals": {
                "U": {
                    "non_actionable_required_team_uncertainty_cases": [
                        {
                            "candidate_subject_id": "u",
                            "scope_kind": unit["scope_kind"],
                            "team_attribution_evidence_source_digest": unit[
                                "team_attribution_evidence_source_digest"
                            ],
                            "team_attribution_evidence_status": unit[
                                "team_attribution_evidence_status"
                            ],
                        }
                    ]
                }
            },
            "_internal_review_units": units,
        }
        workflow = {"issues": {
            "coverage_readiness_blocked": True,
            "normal_blocking": 0,
            "mixed_blocking": 0,
        }}
        sources = _not_materialized_team_attribution_sources(workflow, progress)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["detected_pairs"], current_pairs)
        self.assertEqual(
            sources[0]["source_ownership_digest"],
            source_ownership_digest("u", current_pairs),
        )
        # The focused evidence builder accepts exactly the source emitted by
        # real progress materialization; it does not need the raw parent.
        rebuilt = build_team_attribution_evidence(
            _candidate_document(),
            {"tracklets": list(tracklets.values())},
            _review_cards_document(),
            focused_sources=sources,
        )
        self.assertEqual(rebuilt["cases"][0]["source_observation_pairs"], [
            ["t", frame] for frame in range(1, 5)
        ])

    def test_progress_materialization_attaches_current_exact_evidence_after_trim(self) -> None:
        tracklets = {"t": {"tracklet_id": "t", "team_label": "U", "positions_m": [
            _position(frame) for frame in range(1, 7)
        ]}}
        current_pairs = [("t", frame) for frame in range(1, 5)]
        exact_document = build_team_attribution_evidence(
            _candidate_document(),
            {"tracklets": list(tracklets.values())},
            _review_cards_document(),
            focused_sources=[_focused_source("u", current_pairs)],
        )
        exact_document["cases"][0]["rendered_anchor_crops"] = list(
            exact_document["cases"][0]["anchor_crops"]
        )

        unit = _unit_by_scope(_materialized_units(
            tracklets=tracklets,
            resolved_pairs={("t", 5), ("t", 6)},
            team_evidence=exact_document,
        ), "whole_subject")

        self.assertEqual(unit["detected_pairs"], current_pairs)
        self.assertEqual(
            unit["team_attribution_evidence_source_digest"],
            source_ownership_digest("u", current_pairs),
        )
        self.assertTrue(unit["has_operator_visual_evidence"])
        self.assertEqual(unit["visual_evidence"]["kind"], "team_attribution")
        self.assertNotIn("team_attribution_evidence_status", unit)

        policy = apply_coverage_policy(
            [unit],
            {
                "reliable_observations": 4,
                "per_team": {
                    "U": {
                        "reliable_observations": 4,
                        "confirmed_named_observations": 0,
                    }
                },
            },
            {
                pair: {
                    "team_label": "U",
                    "identity_status": "unresolved",
                    "canonical_player_id": None,
                }
                for pair in current_pairs
            },
            _team_u_match(),
        )

        self.assertEqual(
            [case["candidate_subject_id"] for case in policy["next_cases"]],
            ["u"],
        )
        self.assertEqual(policy["readiness"]["team_attribution_residual"]["status"], "none")

    def test_progress_materialization_attaches_team_evidence_metadata_to_canonical_segment(self) -> None:
        segment = {
            "candidate_subject_id": "segment-subject",
            "review_target_id": "segment-1",
            "source_team_label": "U",
            "effective_team_label": "U",
            "tracklet_ids": ["segment-track"],
            "owned_frames": [30, 31, 32],
            "owned_observations": [
                {"tracklet_id": "segment-track", "frame": frame}
                for frame in (30, 31, 32)
            ],
            "frame_start": 30,
            "frame_end": 32,
            "frame_ranges": [[30, 32]],
            "reason_codes": ["team_attribution_uncertain"],
        }
        unit = _unit_by_scope(_materialized_units(
            tracklets={},
            subjects={},
            segment_review={"targets": [segment]},
            team_evidence={},
        ), "canonical_segment")
        pairs = [("segment-track", frame) for frame in (30, 31, 32)]

        self.assertEqual(unit["detected_pairs"], pairs)
        self.assertEqual(
            unit["team_attribution_evidence_source_digest"],
            source_ownership_digest("segment-subject", pairs),
        )
        self.assertEqual(
            unit["team_attribution_evidence_status"],
            "team_attribution_evidence_not_materialized",
        )

    def test_progress_keeps_exact_ordinary_evidence_out_of_focused_failure_state(self) -> None:
        pairs = [("t", frame) for frame in range(1, 5)]
        source_digest = source_ownership_digest("u", pairs)
        unit = {
            "candidate_subject_id": "u",
            "source_team_label": "U",
            "effective_team_label": "U",
            "has_operator_visual_evidence": True,
            "detected_pairs": pairs,
            "visual_evidence": {
                "status": "ready_for_visual_audit",
                "anchor_crops": [
                    {
                        "tracklet_id": tracklet_id,
                        "frame": frame,
                        "artifact": f"anchor_crops/u/{frame}.jpg",
                        "selection_eligible": True,
                    }
                    for tracklet_id, frame in pairs[:3]
                ],
            },
            "reason_codes": ["team_attribution_evidence_unavailable"],
        }
        document = {
            "cases": [{
                "candidate_subject_id": "u",
                "source_ownership_digest": source_digest,
                "status": "focused_source_not_reviewable",
            }]
        }

        _attach_team_attribution_evidence([unit], document)

        self.assertEqual(unit["team_attribution_evidence_source_digest"], source_digest)
        self.assertNotIn("team_attribution_evidence_status", unit)
        self.assertNotIn("team_attribution_evidence_unavailable", unit["reason_codes"])

    def test_focused_source_with_exact_normal_evidence_is_already_actionable(self) -> None:
        pairs = [("t", frame) for frame in range(1, 5)]
        status = _focused_source_failure_status(
            _focused_source("u", pairs),
            {"u": _candidate_document()["subjects"][0]},
            {"t": {"tracklet_id": "t", "positions_m": [_position(frame) for frame in range(1, 5)]}},
            {
                "u": {
                    "review_status": "ready_for_operator_review",
                    "requires_operator_review": True,
                    "visual_evidence": {
                        "status": "ready_for_visual_audit",
                        "anchor_crops": [
                            {
                                "tracklet_id": tracklet_id,
                                "frame": frame,
                                "artifact": f"anchor_crops/u/{frame}.jpg",
                                "selection_eligible": True,
                            }
                            for tracklet_id, frame in pairs[:3]
                        ],
                    },
                }
            },
        )

        self.assertEqual(status, FOCUSED_SOURCE_ALREADY_ACTIONABLE)

    def test_generated_team_attribution_crop_is_available_through_match_artifact_route(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            relatives = [
                Path("team_attribution_evidence") / subject / "a1b2c3d4e5f60708" / "01_f000001.jpg"
                for subject in (
                    "shadow-a-a1b2c3d4e5f60708",
                    "shadow-b-a1b2c3d4e5f60708",
                    "shadow-u-a1b2c3d4e5f60708",
                )
            ]
            for relative in relatives:
                artifact = root / relative
                artifact.parent.mkdir(parents=True)
                artifact.write_bytes(b"jpeg")

            with patch("app.main.match_dir", return_value=root):
                responses = [get_artifact("match", str(relative)) for relative in relatives]

            for response in responses:
                self.assertIsInstance(response, FileResponse)
                self.assertEqual(response.media_type, "image/jpeg")

    def test_team_attribution_artifact_route_rejects_an_unowned_path_shape(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative = Path("team_attribution_evidence/shadow-a-a1b2c3d4e5f60708/01_f000001.jpg")
            artifact = root / relative
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"jpeg")

            with patch("app.main.match_dir", return_value=root), self.assertRaises(HTTPException) as error:
                get_artifact("match", str(relative))

        self.assertEqual(error.exception.status_code, 404)

    def test_recovers_exact_inside_play_team_u_observations_without_reid_gates(self) -> None:
        document = build_team_attribution_evidence(
            {
                "subjects": [
                    {
                        "candidate_subject_id": "u-subject",
                        "team_label": "U",
                        "tracklet_ids": ["u-track"],
                    },
                    {
                        "candidate_subject_id": "a-subject",
                        "team_label": "A",
                        "tracklet_ids": ["a-track"],
                    },
                ]
            },
            {
                "tracklets": [
                    {
                        "tracklet_id": "u-track",
                        "positions_m": [
                            _position(frame, confidence=0.08)
                            for frame in range(10, 16)
                        ] + [
                            _position(16, play_area_status="outside_play"),
                            {"frame": 17, "source": "predicted", "bbox_xyxy": [1, 1, 30, 80]},
                        ],
                    },
                    {
                        "tracklet_id": "a-track",
                        "positions_m": [_position(10, bbox=[250, 100, 280, 180])],
                    },
                ]
            },
            {
                "cards": [
                    {
                        "candidate_subject_id": "u-subject",
                        "review_status": "no_visual_evidence",
                    },
                    {
                        "candidate_subject_id": "a-subject",
                        "review_status": "no_visual_evidence",
                    },
                ]
            },
        )

        self.assertEqual(document["summary"]["cases"], 1)
        case = document["cases"][0]
        self.assertEqual(case["candidate_subject_id"], "u-subject")
        self.assertEqual(case["status"], "ready_for_team_attribution")
        self.assertEqual(case["detected_observation_count"], 6)
        self.assertEqual(
            case["source_observation_pairs"],
            [["u-track", frame] for frame in range(10, 16)],
        )
        self.assertEqual(len(case["anchor_crops"]), 5)
        self.assertTrue(all(
            crop["tracklet_id"] == "u-track"
            and crop["frame"] in range(10, 16)
            and "exact_detected_inside_play_observation" in crop["selection_reasons"]
            for crop in case["anchor_crops"]
        ))
        self.assertTrue(case["safety"]["does_not_assign_team_automatically"])
        self.assertTrue(case["safety"]["does_not_mutate_canonical_identity"])

    def test_visual_evidence_rejects_stale_ownership_and_requires_rendered_crops(self) -> None:
        generated = build_team_attribution_evidence(
            {"subjects": [{"candidate_subject_id": "u", "team_label": "U", "tracklet_ids": ["t"]}]},
            {"tracklets": [{"tracklet_id": "t", "positions_m": [_position(frame) for frame in range(1, 5)]}]},
            {"cards": [{"candidate_subject_id": "u", "review_status": "no_visual_evidence"}]},
        )
        case = generated["cases"][0]
        case["rendered_anchor_crops"] = list(case["anchor_crops"])

        current = visual_evidence_for_unit(
            generated,
            candidate_subject_id="u",
            detected_pairs={("t", frame) for frame in range(1, 5)},
        )
        stale = visual_evidence_for_unit(
            generated,
            candidate_subject_id="u",
            detected_pairs={("t", frame) for frame in range(1, 4)},
        )

        self.assertEqual(current and current["kind"], "team_attribution")
        self.assertEqual(len((current or {})["anchor_crops"]), 4)
        self.assertIsNone(stale)
        self.assertEqual(
            evidence_status_for_unit(
                generated,
                candidate_subject_id="u",
                detected_pairs={("t", frame) for frame in range(1, 4)},
            ),
            "team_attribution_evidence_not_materialized",
        )

    def test_invalid_bbox_and_only_duplicate_overlap_remain_non_actionable(self) -> None:
        invalid = build_team_attribution_evidence(
            {"subjects": [{"candidate_subject_id": "u", "team_label": "U", "tracklet_ids": ["t"]}]},
            {"tracklets": [{"tracklet_id": "t", "positions_m": [
                _position(1, bbox=[1, 2, 3]),
                _position(2, bbox=["x", 2, 3, 4]),
                _position(3, bbox=[1, 2, 3, 4]),
            ]}]},
            {"cards": [{"candidate_subject_id": "u", "review_status": "no_visual_evidence"}]},
        )
        self.assertEqual(invalid["cases"][0]["status"], "no_team_attribution_evidence")
        self.assertEqual(invalid["cases"][0]["rejected_observations"]["invalid_bbox"], 2)
        self.assertEqual(invalid["cases"][0]["rejected_observations"]["bbox_too_small"], 1)

        overlapping = build_team_attribution_evidence(
            {"subjects": [{"candidate_subject_id": "u", "team_label": "U", "tracklet_ids": ["t"]}]},
            {"tracklets": [
                {"tracklet_id": "t", "positions_m": [_position(frame) for frame in range(1, 5)]},
                {"tracklet_id": "other", "positions_m": [_position(frame) for frame in range(1, 5)]},
            ]},
            {"cards": [{"candidate_subject_id": "u", "review_status": "no_visual_evidence"}]},
        )
        self.assertEqual(overlapping["cases"][0]["status"], "no_team_attribution_evidence")
        self.assertEqual(
            overlapping["cases"][0]["rejected_observations"]["overlaps_nearby_person"],
            4,
        )

    def test_focused_evidence_can_materialize_an_exact_cross_team_source(self) -> None:
        pairs = [("t", frame) for frame in range(1, 5)]
        document = build_team_attribution_evidence(
            {"subjects": [{"candidate_subject_id": "b-cross", "team_label": "B", "tracklet_ids": ["t"]}]},
            {"tracklets": [{"tracklet_id": "t", "positions_m": [_position(frame) for frame in range(1, 5)]}]},
            {"cards": [{"candidate_subject_id": "b-cross", "review_status": "no_visual_evidence"}]},
            focused_sources=[
                {
                    "candidate_subject_id": "b-cross",
                    "scope_kind": "whole_subject",
                    "source_team_label": "B",
                    "source_ownership_digest": source_ownership_digest("b-cross", pairs),
                    "detected_pairs": pairs,
                }
            ],
        )

        self.assertEqual(len(document["cases"]), 1)
        self.assertEqual(document["cases"][0]["source_team_label"], "B")
        self.assertEqual(document["cases"][0]["source_observation_pairs"], [list(pair) for pair in pairs])
        self.assertEqual(document["cases"][0]["status"], "ready_for_team_attribution")

    def test_focused_evidence_preserves_trimmed_exact_source_ownership(self) -> None:
        subject = {"candidate_subject_id": "same-subject", "team_label": "U", "tracklet_ids": ["t"]}
        tracklets = {"tracklets": [{"tracklet_id": "t", "positions_m": [_position(frame) for frame in range(1, 7)]}]}
        trimmed_pairs = [("t", frame) for frame in range(1, 5)]
        document = build_team_attribution_evidence(
            {"subjects": [subject]},
            tracklets,
            {"cards": [{"candidate_subject_id": "same-subject", "review_status": "no_visual_evidence"}]},
            focused_sources=[
                {
                    "candidate_subject_id": "same-subject",
                    "scope_kind": "whole_subject",
                    "source_ownership_digest": source_ownership_digest("same-subject", trimmed_pairs),
                    "detected_pairs": trimmed_pairs,
                }
            ],
        )

        self.assertEqual(len(document["cases"]), 1)
        case = document["cases"][0]
        self.assertEqual(case["source_observation_pairs"], [list(pair) for pair in trimmed_pairs])
        self.assertEqual([crop["frame"] for crop in case["anchor_crops"]], [1, 2, 3, 4])
        case["rendered_anchor_crops"] = list(case["anchor_crops"])
        self.assertIsNotNone(
            visual_evidence_for_unit(
                document,
                candidate_subject_id="same-subject",
                detected_pairs=trimmed_pairs,
            )
        )
        self.assertIsNone(
            visual_evidence_for_unit(
                document,
                candidate_subject_id="same-subject",
                detected_pairs=[("t", frame) for frame in range(1, 7)],
            )
        )

    def test_focused_sources_with_one_subject_do_not_collapse_distinct_ownership(self) -> None:
        subject = {"candidate_subject_id": "same-subject", "team_label": "U", "tracklet_ids": ["t"]}
        first_pairs = [("t", frame) for frame in range(1, 4)]
        second_pairs = [("t", frame) for frame in range(4, 7)]
        document = build_team_attribution_evidence(
            {"subjects": [subject]},
            {"tracklets": [{"tracklet_id": "t", "positions_m": [_position(frame) for frame in range(1, 7)]}]},
            {"cards": [{"candidate_subject_id": "same-subject", "review_status": "no_visual_evidence"}]},
            focused_sources=[
                {
                    "candidate_subject_id": "same-subject",
                    "scope_kind": "whole_subject",
                    "source_ownership_digest": source_ownership_digest("same-subject", first_pairs),
                    "detected_pairs": first_pairs,
                },
                {
                    "candidate_subject_id": "same-subject",
                    "scope_kind": "segment",
                    "review_target_id": "segment-2",
                    "source_ownership_digest": source_ownership_digest("same-subject", second_pairs),
                    "detected_pairs": second_pairs,
                },
            ],
        )

        self.assertEqual(len(document["cases"]), 2)
        self.assertEqual(
            {case["source_ownership_digest"] for case in document["cases"]},
            {
                source_ownership_digest("same-subject", first_pairs),
                source_ownership_digest("same-subject", second_pairs),
            },
        )

    def test_focused_evidence_rejects_stale_or_broadened_source_digest(self) -> None:
        document = build_team_attribution_evidence(
            {"subjects": [{"candidate_subject_id": "u", "team_label": "U", "tracklet_ids": ["t"]}]},
            {"tracklets": [{"tracklet_id": "t", "positions_m": [_position(frame) for frame in range(1, 5)]}]},
            {"cards": [{"candidate_subject_id": "u", "review_status": "no_visual_evidence"}]},
            focused_sources=[
                {
                    "candidate_subject_id": "u",
                    "source_ownership_digest": "stale-digest",
                    "detected_pairs": [("t", frame) for frame in range(1, 4)],
                }
            ],
        )

        self.assertEqual(document["cases"], [])

    def test_prefers_clean_temporally_distinct_observations_over_overlap(self) -> None:
        document = build_team_attribution_evidence(
            {"subjects": [{"candidate_subject_id": "u", "team_label": "U", "tracklet_ids": ["t"]}]},
            {"tracklets": [
                {"tracklet_id": "t", "positions_m": [_position(frame) for frame in range(1, 7)]},
                {"tracklet_id": "other", "positions_m": [_position(1)]},
            ]},
            {"cards": [{"candidate_subject_id": "u", "review_status": "no_visual_evidence"}]},
        )

        selected_frames = [crop["frame"] for crop in document["cases"][0]["anchor_crops"]]
        self.assertNotIn(1, selected_frames)
        self.assertEqual(selected_frames, [2, 3, 4, 5, 6])

    def test_missing_source_video_stays_explicitly_unavailable(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root / "identity_candidate_shadow.json", {
                "subjects": [{"candidate_subject_id": "u", "team_label": "U", "tracklet_ids": ["t"]}],
            })
            _write(root / "tracklets.json", {
                "tracklets": [{"tracklet_id": "t", "positions_m": [_position(frame) for frame in range(1, 5)]}],
            })
            _write(root / "identity_roster_subject_review_shadow.json", {
                "cards": [{"candidate_subject_id": "u", "review_status": "no_visual_evidence"}],
            })

            document = materialize_team_attribution_evidence(root)

            self.assertEqual(document["cases"][0]["status"], "source_video_unavailable")
            self.assertEqual(document["cases"][0]["rendered_anchor_crops"], [])
            self.assertEqual(document["summary"]["rendered_reviewable_cases"], 0)

    def test_focused_source_digest_mismatch_is_persisted_as_exact_technical_outcome(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_team_u_inputs(root)
            source = {
                "candidate_subject_id": "u",
                "scope_kind": "whole_subject",
                "review_target_id": None,
                "continuity_group_id": None,
                "source_team_label": "U",
                "source_ownership_digest": "forged-digest",
                "detected_pairs": [("t", 1), ("t", 2)],
            }

            document = materialize_team_attribution_evidence(
                root,
                focused_sources=[source],
            )

            self.assertEqual(len(document["cases"]), 1)
            case = document["cases"][0]
            self.assertEqual(case["status"], "focused_source_digest_mismatch")
            self.assertEqual(case["materialization_reason"], "focused_source_digest_mismatch")
            self.assertEqual(case["source_ownership_digest"], "forged-digest")

    def test_current_rendered_evidence_is_reused_across_decision_refreshes(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_team_u_inputs(root)
            (root / "video.mp4").write_bytes(b"video-placeholder")

            def render(_video: Path, output_root: Path, artifact: dict) -> set[str]:
                rendered = set()
                for card in artifact["cards"]:
                    for crop in card["anchor_crops"]:
                        path = output_root / crop["artifact"]
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(b"crop")
                        rendered.add(crop["artifact"])
                return rendered

            with patch(
                "app.services.identity_reviewed_team_attribution_evidence.render_identity_roster_anchor_crops",
                side_effect=render,
            ) as renderer:
                first = materialize_team_attribution_evidence(root)
                second = materialize_team_attribution_evidence(root)

            self.assertEqual(renderer.call_count, 1)
            self.assertEqual(first["source_inputs_digest"], second["source_inputs_digest"])
            self.assertEqual(second["summary"]["rendered_reviewable_cases"], 1)

    def test_current_durable_technical_source_requires_exact_canonical_ownership(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_team_u_inputs(root)
            pairs = [("t", frame) for frame in range(1, 5)]
            descriptor = {
                "candidate_subject_id": "u",
                "scope_kind": "whole_subject",
                "team_attribution_evidence_source_digest": source_ownership_digest("u", pairs),
            }

            resolved = resolve_current_team_attribution_sources(root, [descriptor])

            self.assertEqual(resolved, [{
                "candidate_subject_id": "u",
                "scope_kind": "whole_subject",
                "review_target_id": None,
                "continuity_group_id": None,
                "source_team_label": "U",
                "source_ownership_digest": descriptor["team_attribution_evidence_source_digest"],
                "detected_pairs": pairs,
            }])
            descriptor["team_attribution_evidence_source_digest"] = "stale-digest"
            self.assertIsNone(resolve_current_team_attribution_sources(root, [descriptor]))


def _position(
    frame: int,
    *,
    confidence: float = 0.12,
    bbox: list[int] | None = None,
    play_area_status: str = "inside_play",
) -> dict:
    return {
        "frame": frame,
        "time_sec": frame / 30,
        "source": "detected",
        "status": "detected",
        "play_area_status": play_area_status,
        "bbox_xyxy": bbox or [100, 100, 130, 180],
        "confidence": confidence,
    }


def _write(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def _write_team_u_inputs(root: Path) -> None:
    _write(root / "identity_candidate_shadow.json", {
        "subjects": [{"candidate_subject_id": "u", "team_label": "U", "tracklet_ids": ["t"]}],
    })
    _write(root / "tracklets.json", {
        "tracklets": [{"tracklet_id": "t", "positions_m": [_position(frame) for frame in range(1, 5)]}],
    })
    _write(root / "identity_roster_subject_review_shadow.json", {
        "cards": [{"candidate_subject_id": "u", "review_status": "no_visual_evidence"}],
    })


def _candidate_document() -> dict:
    return {
        "subjects": [{
            "candidate_subject_id": "u",
            "team_label": "U",
            "tracklet_ids": ["t"],
        }]
    }


def _review_cards_document() -> dict:
    return {"cards": [{
        "candidate_subject_id": "u",
        "review_status": "no_visual_evidence",
    }]}


def _team_u_match() -> dict:
    return {
        "id": "match",
        "teams": [
            {"team_label": "A", "players": []},
            {"team_label": "B", "players": []},
        ],
        "identity_review_scope": {
            "schema_version": "1.0.0",
            "teams": {"A": "complete_roster", "B": "team_stats_only"},
        },
    }


def _focused_source(
    subject_id: str,
    pairs: list[tuple[str, int]],
) -> dict:
    return {
        "candidate_subject_id": subject_id,
        "source_ownership_digest": source_ownership_digest(subject_id, pairs),
        "detected_pairs": pairs,
    }


def _materialized_units(
    *,
    tracklets: dict[str, dict],
    subjects: dict[str, set[str]] | None = None,
    resolved_pairs: set[tuple[str, int]] | None = None,
    segment_review: dict | None = None,
    team_evidence: dict | None = None,
) -> list[dict]:
    """Run the real progress unit pipeline with compact canonical inputs."""
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        with patch("app.services.identity_reviewed_progress._tracklets", return_value=tracklets), patch(
            "app.services.identity_reviewed_progress._subjects",
            return_value=subjects if subjects is not None else {"u": {"t"}},
        ), patch(
            "app.services.identity_reviewed_progress._subject_stable_slots", return_value={}
        ), patch("app.services.identity_reviewed_progress._cards", return_value={}), patch(
            "app.services.identity_reviewed_progress._manual_decisions", return_value={}
        ), patch(
            "app.services.identity_reviewed_progress.load_fresh_seeded_assignments",
            return_value=({}, {"status": "fresh"}),
        ), patch("app.services.identity_reviewed_progress._fps", return_value=30.0), patch(
            "app.services.identity_reviewed_progress.load_segment_review",
            return_value=segment_review or {"targets": []},
        ), patch(
            "app.services.identity_reviewed_progress.load_mixed_player_cases",
            return_value={"cases": []},
        ), patch(
            "app.services.identity_reviewed_progress.resolved_material_continuity_observation_pairs",
            return_value=resolved_pairs or set(),
        ), patch(
            "app.services.identity_reviewed_progress.load_material_continuity_decisions",
            return_value={},
        ), patch(
            "app.services.identity_reviewed_progress.load_team_attribution_evidence",
            return_value=team_evidence or {},
        ):
            return materialize_reviewed_identity_units(root, {"teams": []})


def _unit_by_scope(units: list[dict], scope_kind: str) -> dict:
    return next(unit for unit in units if unit.get("scope_kind") == scope_kind)
