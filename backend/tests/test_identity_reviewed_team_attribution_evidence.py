from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.responses import FileResponse

from app.main import get_artifact
from app.services.identity_reviewed_team_attribution_evidence import (
    build_team_attribution_evidence,
    evidence_status_for_unit,
    materialize_team_attribution_evidence,
    visual_evidence_for_unit,
)


class TeamAttributionEvidenceTests(unittest.TestCase):
    def test_generated_team_attribution_crop_is_available_through_match_artifact_route(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative = Path("team_attribution_evidence") / "shadow-u-a1b2c3d4e5f60708" / "01_f000001.jpg"
            artifact = root / relative
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"jpeg")

            with patch("app.main.match_dir", return_value=root):
                response = get_artifact("match", str(relative))

            self.assertIsInstance(response, FileResponse)
            self.assertEqual(response.media_type, "image/jpeg")

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
        document = build_team_attribution_evidence(
            {"subjects": [{"candidate_subject_id": "b-cross", "team_label": "B", "tracklet_ids": ["t"]}]},
            {"tracklets": [{"tracklet_id": "t", "positions_m": [_position(frame) for frame in range(1, 5)]}]},
            {"cards": [{"candidate_subject_id": "b-cross", "review_status": "no_visual_evidence"}]},
            candidate_subject_ids={"b-cross"},
        )

        self.assertEqual(len(document["cases"]), 1)
        self.assertEqual(document["cases"][0]["source_team_label"], "B")
        self.assertEqual(document["cases"][0]["status"], "ready_for_team_attribution")

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
