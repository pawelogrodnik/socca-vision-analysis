from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from app.services.identity_jersey_number_common import canonical_digest
from app.services.player_detection_quality_audit import (
    _build_manifest,
    build_player_observation_source_lineage,
    build_renderer_visible_observations,
)
from app.services.player_detection_quality_review import (
    analyze_player_detection_quality_review_files,
)


class PlayerObservationQaEndToEndTests(unittest.TestCase):
    def test_frozen_artifacts_to_review_report_is_fresh_safe_and_consistent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            match_path = root / "match"
            package = root / "qa"
            match_path.mkdir()
            package.mkdir()
            match_document = {"id": "match-e2e", "video_filename": "match.mp4"}
            analysis_report = {
                "run_id": "run-e2e",
                "video": {
                    "fps": 30.0,
                    "width": 1920,
                    "height": 1080,
                },
            }
            global_identity = {
                "slots": [
                    {
                        "slot_id": "A01",
                        "stable_player_id": "A01",
                        "stable_subject_id": "subject-a",
                        "team_label": "A",
                        "tracklet_ids": ["shown"],
                        "overlay_positions": [
                            {
                                "frame": 10,
                                "time_sec": 1 / 3,
                                "source": "detected",
                                "tracklet_id": "shown",
                                "bbox_xyxy": [10, 10, 30, 60],
                                "confidence": 0.95,
                            }
                        ],
                    }
                ],
                "unmatched_observations": [],
            }
            tracklets = {
                "tracklets": [
                    _tracklet("shown", "A", [10, 10, 30, 60]),
                    _tracklet("unrepresented", "A", [40, 10, 60, 60]),
                ],
                "rejected_tracklets": [
                    _tracklet("rejected", "A", [70, 10, 90, 60]),
                ],
            }
            raw_tracks = [
                {
                    "track_id": 501,
                    "positions": [
                        {
                            "frame": 10,
                            "bbox_xyxy": [100, 10, 120, 60],
                            "confidence": 0.8,
                        }
                    ],
                }
            ]
            observations = build_renderer_visible_observations(
                global_identity=global_identity,
                tracklets_document=tracklets,
                fps=30.0,
                width=1920,
                height=1080,
                pitch_config=None,
            )
            selected = [
                {
                    "frame": 10,
                    "observations": observations[10],
                    "filtered_count": len(observations[10]),
                    "filter_summary": {"excluded": 0},
                    "known_false": False,
                    "missing_from_typical": 0,
                }
            ]
            lineage = build_player_observation_source_lineage(
                match_document=match_document,
                analysis_report=analysis_report,
                global_identity=global_identity,
                tracklets_document=tracklets,
                raw_tracks_document=raw_tracks,
                visible_observations_by_frame=observations,
            )
            manifest = _build_manifest(
                match_document=match_document,
                analysis_report=analysis_report,
                global_identity=global_identity,
                tracklets_document=tracklets,
                selected_frames=selected,
                known_false=set(),
                fps=30.0,
                width=1920,
                height=1080,
                source_lineage=lineage,
            )
            reviewed = copy.deepcopy(manifest)
            reviewed["reviewed_at"] = "2026-07-30T12:00:00Z"
            reviewed["manual_review"] = {
                "detection_decisions": {},
                "missing_players": [
                    _missing("visible-duplicate", [40, 10, 60, 60]),
                    _missing("rejected", [70, 10, 90, 60]),
                    _missing("raw", [100, 10, 120, 60]),
                    _missing("none", [130, 10, 150, 60]),
                ],
                "frame_comments": [
                    {"frame_number": 10, "comment": "E2E operator note"}
                ],
            }
            _write(match_path / "match.json", match_document)
            _write(match_path / "analysis_report.json", analysis_report)
            _write(match_path / "global_identity.json", global_identity)
            _write(match_path / "tracklets.json", tracklets)
            _write(match_path / "tracks.json", raw_tracks)
            _write(package / "audit_manifest.json", manifest)
            _write(root / "reviewed.json", reviewed)
            source_digests_before = {
                name: canonical_digest(
                    json.loads((match_path / name).read_text(encoding="utf-8"))
                )
                for name in (
                    "match.json",
                    "analysis_report.json",
                    "global_identity.json",
                    "tracklets.json",
                    "tracks.json",
                )
            }

            report = analyze_player_detection_quality_review_files(
                reviewed_audit_path=root / "reviewed.json",
                audit_package_dir=package,
                match_path=match_path,
            )

            source_digests_after = {
                name: canonical_digest(
                    json.loads((match_path / name).read_text(encoding="utf-8"))
                )
                for name in source_digests_before
            }
            self.assertEqual(source_digests_after, source_digests_before)
            self.assertEqual(report["validation"]["manifest_lineage_matches"], True)
            self.assertEqual(
                report["missing_attribution"]["counts"],
                {
                    "already_visible_in_product_observation": 1,
                    "present_in_clean_tracklet_but_missing_from_product_observation": 0,
                    "present_only_in_rejected_tracklet": 1,
                    "present_in_raw_track_only": 1,
                    "no_matching_track": 1,
                    "ambiguous_match": 0,
                    "team_conflict": 0,
                },
            )
            self.assertEqual(
                report["projected_visual_recovery"][
                    "projected_observation_coverage"
                ],
                0.333333,
            )
            self.assertEqual(
                report["conclusion"]["primary_bottleneck"],
                "inconclusive",
            )
            self.assertEqual(
                report["safety"],
                {
                    "yolo_reruns": 0,
                    "tracking_reruns": 0,
                    "candidate_identity_mutations": 0,
                    "production_identity_mutations": 0,
                    "production_stats_mutations": 0,
                    "automatic_assignments": 0,
                },
            )
            self.assertTrue((package / "review_report.json").exists())
            self.assertTrue((package / "review_report.md").exists())


def _tracklet(
    tracklet_id: str,
    team_label: str,
    bbox: list[int],
) -> dict[str, object]:
    return {
        "tracklet_id": tracklet_id,
        "team_label": team_label,
        "positions_m": [
            {
                "frame": 10,
                "time_sec": 1 / 3,
                "bbox_xyxy": bbox,
                "confidence": 0.9,
                "play_area_status": "inside_play",
            }
        ],
    }


def _missing(annotation_id: str, bbox: list[int]) -> dict[str, object]:
    return {
        "manual_annotation_id": f"manual:{annotation_id}",
        "frame_number": 10,
        "team_label": "A",
        "bbox_xyxy": bbox,
    }


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
