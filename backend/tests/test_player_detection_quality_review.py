from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from app.services.player_detection_quality_audit import (
    _observations_by_frame,
    build_player_observation_source_lineage,
)
from app.services.player_detection_quality_review import (
    PlayerObservationQaStaleSourceError,
    _build_conclusion,
    analyze_player_detection_quality_review,
    analyze_player_detection_quality_review_files,
    render_player_detection_quality_review_markdown,
)


class PlayerDetectionQualityReviewTests(unittest.TestCase):
    def test_builds_ordered_waterfall_and_projected_visual_recovery(self) -> None:
        manifest = _manifest()
        reviewed = _reviewed(
            manifest,
            [
                _missing([10, 10, 30, 60]),
                _missing([40, 10, 60, 60]),
                _missing([70, 10, 90, 60]),
                _missing([100, 10, 120, 60]),
                _missing([130, 10, 150, 60]),
            ],
        )
        tracklets = {
            "tracklets": [
                _tracklet("shown", [10, 10, 30, 60]),
                _tracklet("clean-hidden", [40, 10, 60, 60]),
            ],
            "rejected_tracklets": [
                _tracklet("rejected-hidden", [70, 10, 90, 60]),
            ],
        }
        global_identity = {
            "slots": [
                {
                    "slot_id": "A01",
                    "team_label": "A",
                    "tracklet_ids": ["shown"],
                    "overlay_positions": [
                        {
                            "frame": 10,
                            "source": "detected",
                            "tracklet_id": "shown",
                            "bbox_xyxy": [10, 10, 30, 60],
                        }
                    ],
                }
            ]
        }

        report = analyze_player_detection_quality_review(
            reviewed_audit=reviewed,
            expected_manifest=manifest,
            tracklets_document=tracklets,
            global_identity_document=global_identity,
            raw_tracks_document=[
                _raw_track(999, [100, 10, 120, 60]),
            ],
        )

        self.assertEqual(
            report["missing_attribution"]["counts"],
            {
                "already_visible_in_product_observation": 1,
                "present_in_clean_tracklet_but_missing_from_product_observation": 1,
                "present_only_in_rejected_tracklet": 1,
                "present_in_raw_track_only": 1,
                "no_matching_track": 1,
                "ambiguous_match": 0,
                "team_conflict": 0,
            },
        )
        self.assertEqual(
            [
                row["attribution"]
                for row in report["missing_attribution"]["items"]
            ],
            [
                "already_visible_in_product_observation",
                "present_in_clean_tracklet_but_missing_from_product_observation",
                "present_only_in_rejected_tracklet",
                "present_in_raw_track_only",
                "no_matching_track",
            ],
        )
        projection = report["projected_visual_recovery"]
        self.assertEqual(projection["recoverable_missing_boxes"], 1)
        self.assertEqual(
            projection["recovered_by_source"],
            {"unrepresented_tracklet": 1},
        )
        self.assertNotIn("unresolved_overlay_projection", report)
        markdown = render_player_detection_quality_review_markdown(report)
        self.assertIn("Projected, not rendered", markdown)
        self.assertIn("## Conclusion", markdown)
        self.assertEqual(
            set(report["conclusion"]),
            {
                "primary_bottleneck",
                "confidence",
                "evidence",
                "limitations",
                "recommended_next_step",
            },
        )

    def test_opposite_known_team_is_conflict_not_valid_match(self) -> None:
        manifest = _manifest()
        reviewed = _reviewed(manifest, [_missing([40, 10, 60, 60])])

        report = analyze_player_detection_quality_review(
            reviewed_audit=reviewed,
            expected_manifest=manifest,
            tracklets_document={
                "tracklets": [
                    {
                        **_tracklet("opposite", [40, 10, 60, 60]),
                        "team_label": "B",
                    }
                ],
                "rejected_tracklets": [],
            },
            raw_tracks_document=[],
        )

        row = report["missing_attribution"]["items"][0]
        self.assertEqual(row["attribution"], "team_conflict")
        self.assertEqual(row["matched_candidate"]["team_match"], "opposite_team")
        self.assertEqual(
            report["missing_attribution"]["counts"][
                "present_in_clean_tracklet_but_missing_from_product_observation"
            ],
            0,
        )

    def test_unknown_team_is_ambiguous_and_does_not_claim_clean_match(self) -> None:
        manifest = _manifest()
        reviewed = _reviewed(manifest, [_missing([40, 10, 60, 60])])

        report = analyze_player_detection_quality_review(
            reviewed_audit=reviewed,
            expected_manifest=manifest,
            tracklets_document={
                "tracklets": [
                    {
                        **_tracklet("unknown", [40, 10, 60, 60]),
                        "team_label": "U",
                    }
                ],
                "rejected_tracklets": [],
            },
            raw_tracks_document=[],
        )

        self.assertEqual(
            report["missing_attribution"]["items"][0]["attribution"],
            "ambiguous_match",
        )

    def test_one_to_one_matching_is_deterministic(self) -> None:
        manifest = _manifest()
        annotations = [
            _missing([40, 10, 60, 60]),
            _missing([41, 10, 61, 60]),
        ]
        tracklets = {
            "tracklets": [
                _tracklet("b", [41, 10, 61, 60]),
                _tracklet("a", [40, 10, 60, 60]),
            ],
            "rejected_tracklets": [],
        }

        first = analyze_player_detection_quality_review(
            reviewed_audit=_reviewed(manifest, annotations),
            expected_manifest=manifest,
            tracklets_document=tracklets,
            raw_tracks_document=[],
        )
        second = analyze_player_detection_quality_review(
            reviewed_audit=_reviewed(manifest, annotations),
            expected_manifest=manifest,
            tracklets_document=copy.deepcopy(tracklets),
            raw_tracks_document=[],
        )

        first_items = first["missing_attribution"]["items"]
        second_items = second["missing_attribution"]["items"]
        self.assertEqual(first_items, second_items)
        self.assertEqual(
            {
                row["matched_candidate"]["tracklet_id"]
                for row in first_items
            },
            {"a", "b"},
        )

    def test_conclusion_is_inconclusive_for_small_or_uncertain_sample(self) -> None:
        counts = {
            "already_visible_in_product_observation": 0,
            "present_in_clean_tracklet_but_missing_from_product_observation": 7,
            "present_only_in_rejected_tracklet": 0,
            "present_in_raw_track_only": 0,
            "no_matching_track": 0,
            "ambiguous_match": 1,
            "team_conflict": 0,
        }

        conclusion = _build_conclusion(
            counts,
            sample_size=8,
            raw_tracks_available=True,
        )

        self.assertEqual(conclusion["primary_bottleneck"], "inconclusive")
        self.assertEqual(conclusion["confidence"], "low")

    def test_rejects_decision_for_unknown_detection(self) -> None:
        manifest = _manifest()
        reviewed = _reviewed(manifest, [])
        reviewed["manual_review"]["detection_decisions"] = {
            "unknown": "false_detection"
        }

        with self.assertRaisesRegex(ValueError, "invalid detection decisions"):
            analyze_player_detection_quality_review(
                reviewed_audit=reviewed,
                expected_manifest=manifest,
                tracklets_document={"tracklets": [], "rejected_tracklets": []},
            )

    def test_stale_global_identity_does_not_overwrite_existing_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "package"
            match_path = root / "match"
            package.mkdir()
            match_path.mkdir()
            match_document = {"id": "match-1"}
            analysis_report = {"run_id": "run-1", "video": {}}
            tracklets = {"tracklets": [], "rejected_tracklets": []}
            global_identity = {"slots": [], "unmatched_observations": []}
            raw_tracks: list[dict[str, object]] = []
            observations = _observations_by_frame(
                tracklets,
                global_identity=global_identity,
            )
            manifest = {
                **_manifest(),
                "source": {
                    "match_id": "match-1",
                    "artifact_digests": build_player_observation_source_lineage(
                        match_document=match_document,
                        analysis_report=analysis_report,
                        global_identity=global_identity,
                        tracklets_document=tracklets,
                        raw_tracks_document=raw_tracks,
                        visible_observations_by_frame=observations,
                    ),
                },
            }
            reviewed = _reviewed(manifest, [])
            _write(package / "audit_manifest.json", manifest)
            _write(package / "review_report.json", {"sentinel": True})
            _write(root / "reviewed.json", reviewed)
            _write(match_path / "match.json", match_document)
            _write(match_path / "analysis_report.json", analysis_report)
            _write(match_path / "tracklets.json", tracklets)
            _write(match_path / "tracks.json", raw_tracks)
            _write(
                match_path / "global_identity.json",
                {"slots": [{"slot_id": "changed"}], "unmatched_observations": []},
            )

            with self.assertRaises(PlayerObservationQaStaleSourceError) as error:
                analyze_player_detection_quality_review_files(
                    reviewed_audit_path=root / "reviewed.json",
                    audit_package_dir=package,
                    match_path=match_path,
                )

            self.assertIn("global_identity", error.exception.changed_artifacts)
            self.assertEqual(
                error.exception.changed_artifacts,
                ["global_identity"],
            )
            self.assertEqual(
                json.loads((package / "review_report.json").read_text()),
                {"sentinel": True},
            )

    def test_stale_tracklets_identifies_exact_changed_artifacts(self) -> None:
        stored = {
            key: f"digest-{key}"
            for key in (
                "global_identity",
                "tracklets",
                "rejected_tracklets",
                "raw_tracks",
                "match_metadata",
                "analysis_metadata",
                "video_metadata",
                "visible_observation_projection",
            )
        }
        current = dict(stored)
        current["tracklets"] = "changed"
        current["visible_observation_projection"] = "changed-projection"
        from app.services.player_detection_quality_review import (
            _validate_source_freshness,
        )

        with self.assertRaises(PlayerObservationQaStaleSourceError) as error:
            _validate_source_freshness(
                {"source": {"artifact_digests": stored}},
                current,
            )

        self.assertEqual(
            error.exception.changed_artifacts,
            ["tracklets", "visible_observation_projection"],
        )


def _manifest() -> dict[str, object]:
    return {
        "schema_version": "0.2.0",
        "audit_kind": "player_observation_coverage_qa",
        "source": {"match_id": "match-1"},
        "video": {"width": 1920, "height": 1080},
        "items": [
            {
                "frame_number": 10,
                "detections": [
                    {
                        "detection_key": "detection-a",
                        "team_label": "A",
                        "bbox_xyxy": [10, 10, 30, 60],
                        "initial_review_status": "pending",
                        "provenance": {"tracklet_id": "shown"},
                    }
                ],
            }
        ],
    }


def _reviewed(
    manifest: dict[str, object],
    missing_players: list[dict[str, object]],
) -> dict[str, object]:
    return {
        **copy.deepcopy(manifest),
        "reviewed_at": "2026-07-30T08:39:15Z",
        "manual_review": {
            "detection_decisions": {},
            "missing_players": missing_players,
            "frame_comments": [],
        },
    }


def _missing(bbox: list[int]) -> dict[str, object]:
    return {
        "manual_annotation_id": f"manual:{bbox[0]}",
        "frame_number": 10,
        "team_label": "A",
        "bbox_xyxy": bbox,
    }


def _tracklet(tracklet_id: str, bbox: list[int]) -> dict[str, object]:
    return {
        "tracklet_id": tracklet_id,
        "team_label": "A",
        "positions_m": [{"frame": 10, "bbox_xyxy": bbox}],
    }


def _raw_track(track_id: int, bbox: list[int]) -> dict[str, object]:
    return {
        "track_id": track_id,
        "positions": [{"frame": 10, "bbox_xyxy": bbox, "confidence": 0.8}],
    }


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
