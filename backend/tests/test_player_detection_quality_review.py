from __future__ import annotations

import unittest

from app.services.player_detection_quality_review import (
    analyze_player_detection_quality_review,
    render_player_detection_quality_review_markdown,
)


class PlayerDetectionQualityReviewTests(unittest.TestCase):
    def test_attributes_missing_boxes_without_reusing_displayed_tracklets(self) -> None:
        manifest = _manifest()
        reviewed = {
            **manifest,
            "reviewed_at": "2026-07-30T08:39:15Z",
            "manual_review": {
                "detection_decisions": {
                    "detection-a": "false_detection",
                },
                "missing_players": [
                    _missing([40, 10, 60, 60]),
                    _missing([70, 10, 90, 60]),
                    _missing([100, 10, 120, 60]),
                ],
                "frame_comments": [
                    {"frame_number": 10, "comment": "example"},
                ],
            },
        }
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
                    "tracklet_ids": ["clean-hidden"],
                    "overlay_positions": [],
                }
            ]
        }

        report = analyze_player_detection_quality_review(
            reviewed_audit=reviewed,
            expected_manifest=manifest,
            tracklets_document=tracklets,
            global_identity_document=global_identity,
            raw_tracks_document=[
                {
                    "track_id": 999,
                    "positions": [
                        {
                            "frame": 10,
                            "bbox_xyxy": [100, 10, 120, 60],
                            "confidence": 0.8,
                        }
                    ],
                }
            ],
        )

        self.assertEqual(
            report["missing_attribution"]["counts"],
            {
                "present_in_clean_tracklet_not_shown": 1,
                "present_in_rejected_tracklet": 1,
                "no_matching_frozen_tracklet": 1,
            },
        )
        self.assertEqual(report["summary"]["effective_false_detections"], 1)
        self.assertEqual(report["summary"]["missing_player_boxes"], 3)
        self.assertEqual(
            report["identity_layer_attribution"]["counts"][
                "slot_has_no_overlay_position"
            ],
            1,
        )
        self.assertEqual(
            report["identity_layer_attribution"]["counts"][
                "not_applicable_without_clean_tracklet"
            ],
            2,
        )
        self.assertEqual(
            report["raw_track_attribution"]["counts"],
            {
                "present_in_raw_tracks_but_not_tracklets": 1,
                "no_matching_raw_track": 0,
                "not_analyzed": 0,
            },
        )
        self.assertEqual(
            report["unresolved_overlay_projection"]["recoverable_missing_boxes"],
            1,
        )
        self.assertEqual(
            report["unresolved_overlay_projection"]["recovered_by_source"],
            {"unrepresented_tracklet": 1},
        )
        self.assertIn(
            "without using them for stats",
            render_player_detection_quality_review_markdown(report),
        )
        self.assertEqual(report["safety"]["yolo_reruns"], 0)

    def test_rejects_decision_for_unknown_detection(self) -> None:
        manifest = _manifest()
        reviewed = {
            **manifest,
            "manual_review": {
                "detection_decisions": {"unknown": "false_detection"},
                "missing_players": [],
                "frame_comments": [],
            },
        }

        with self.assertRaisesRegex(ValueError, "invalid detection decisions"):
            analyze_player_detection_quality_review(
                reviewed_audit=reviewed,
                expected_manifest=manifest,
                tracklets_document={"tracklets": [], "rejected_tracklets": []},
            )

    def test_accepts_json_equivalent_integer_and_float_coordinates(self) -> None:
        manifest = _manifest()
        manifest["items"][0]["detections"][0]["bbox_xyxy"] = [
            10.0,
            10.0,
            30.0,
            60.0,
        ]
        reviewed = {
            **manifest,
            "items": _manifest()["items"],
            "manual_review": {
                "detection_decisions": {},
                "missing_players": [],
                "frame_comments": [],
            },
        }

        report = analyze_player_detection_quality_review(
            reviewed_audit=reviewed,
            expected_manifest=manifest,
            tracklets_document={"tracklets": [], "rejected_tracklets": []},
        )

        self.assertEqual(report["validation"]["status"], "valid")

    def test_geometry_match_reports_tracklet_team_mismatch(self) -> None:
        manifest = _manifest()
        reviewed = {
            **manifest,
            "manual_review": {
                "detection_decisions": {},
                "missing_players": [_missing([40, 10, 60, 60])],
                "frame_comments": [],
            },
        }

        report = analyze_player_detection_quality_review(
            reviewed_audit=reviewed,
            expected_manifest=manifest,
            tracklets_document={
                "tracklets": [
                    {
                        **_tracklet("team-bbox", [40, 10, 60, 60]),
                        "team_label": "B",
                    }
                ],
                "rejected_tracklets": [],
            },
        )

        matched = report["missing_attribution"]["items"][0]["matched_tracklet"]
        self.assertEqual(matched["tracklet_id"], "team-bbox")
        self.assertFalse(matched["team_label_match"])
        self.assertEqual(report["summary"]["missing_tracklet_team_mismatches"], 1)


def _manifest() -> dict[str, object]:
    return {
        "schema_version": "0.1.0",
        "audit_kind": "player_detection_quality",
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


def _missing(bbox: list[int]) -> dict[str, object]:
    return {
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


if __name__ == "__main__":
    unittest.main()
