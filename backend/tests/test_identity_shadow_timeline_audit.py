from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from app.services.identity_shadow_timeline import build_shadow_resolved_timeline
from app.services.identity_shadow_timeline_audit import (
    build_shadow_timeline_audit_manifest,
    render_shadow_timeline_audit,
)


FPS = 10.0


def _tracklet(tracklet_id: str, start: int, end: int) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "team_label": "A",
        "positions_m": [
            {
                "frame": frame,
                "time_sec": frame / FPS,
                "pitch_m": [float(frame), 5.0],
                "smoothed_pitch_m": [float(frame), 5.0],
                "bbox_xyxy": [10, 8, 30, 52],
                "confidence": 0.9,
                "play_area_status": "inside_play",
            }
            for frame in range(start, end + 1)
        ],
    }


def _offline(source: str, target: str) -> dict:
    return {
        "algorithm": {"name": "test", "version": "1"},
        "accepted_edges": [
            {
                "edge_key": f"edge-{source}-{target}",
                "source_tracklet_id": source,
                "target_tracklet_id": target,
                "confidence": 0.9,
                "recommendation_source": "stitching",
                "occlusion_event_ids": [],
                "current_source_subject_ids": ["slot-A01"],
                "current_target_subject_ids": ["slot-A02"],
                "current_identity_relation": "different_subjects",
            }
        ],
        "subjects": [
            {
                "shadow_subject_id": "shadow-a-1",
                "team_label": "A",
                "tracklet_ids": [source, target],
                "production_subject_ids": ["slot-A01", "slot-A02"],
                "quality_flags": ["merges_multiple_production_subjects"],
            }
        ],
    }


def _quality(*tracklet_ids: str) -> dict:
    return {
        "tracklets": [
            {
                "tracklet_id": tracklet_id,
                "quality_class": "trusted",
                "unreliable_footpoint_ranges": [],
                "unreliable_appearance_ranges": [],
            }
            for tracklet_id in tracklet_ids
        ]
    }


class IdentityShadowTimelineAuditTests(unittest.TestCase):
    def test_manifest_selects_non_direct_transition_and_maps_clip_offset(self) -> None:
        tracklets = [_tracklet("s1", 2700, 2704), _tracklet("t1", 2708, 2712)]
        timeline = build_shadow_resolved_timeline(
            _offline("s1", "t1"),
            tracklets,
            _quality("s1", "t1"),
            fps=FPS,
            generated_at="fixed",
        )
        timeline["transition_events"][0]["requires_review"] = False

        result = build_shadow_timeline_audit_manifest(
            timeline,
            {"tracklets": tracklets},
            benchmark_id="hard",
            benchmark_label="hard3m",
            video_path="clip.mp4",
            video_time_offset_sec=270.0,
            direct_control_limit=0,
            missing_control_limit=0,
            generated_at="fixed",
        )

        self.assertEqual(result["summary"]["review_items"], 1)
        self.assertEqual(result["items"][0]["timeline_state"]["status"], "predicted")
        self.assertEqual(result["items"][0]["source"]["video_time_sec"], 0.4)
        self.assertEqual(result["items"][0]["target"]["video_time_sec"], 0.8)
        self.assertEqual(result["items"][0]["manual_review"]["status"], "pending")

    def test_direct_transition_controls_are_bounded(self) -> None:
        tracklets = [_tracklet("s1", 0, 2), _tracklet("t1", 3, 5)]
        timeline = build_shadow_resolved_timeline(
            _offline("s1", "t1"),
            tracklets,
            _quality("s1", "t1"),
            fps=FPS,
            generated_at="fixed",
        )
        timeline["transition_events"][0]["requires_review"] = False

        without_controls = build_shadow_timeline_audit_manifest(
            timeline,
            {"tracklets": tracklets},
            benchmark_id="easy",
            benchmark_label="easy90",
            video_path="clip.mp4",
            direct_control_limit=0,
            missing_control_limit=0,
            generated_at="fixed",
        )
        with_controls = build_shadow_timeline_audit_manifest(
            timeline,
            {"tracklets": tracklets},
            benchmark_id="easy",
            benchmark_label="easy90",
            video_path="clip.mp4",
            direct_control_limit=1,
            missing_control_limit=0,
            generated_at="fixed",
        )

        self.assertEqual(without_controls["summary"]["review_items"], 0)
        self.assertEqual(with_controls["summary"]["review_items"], 1)
        self.assertEqual(with_controls["items"][0]["selection_reason"], "direct_transition_control")

    def test_renderer_writes_large_card_and_interactive_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video_path = root / "sample.avi"
            writer = cv2.VideoWriter(
                str(video_path),
                cv2.VideoWriter_fourcc(*"MJPG"),
                FPS,
                (80, 60),
            )
            self.assertTrue(writer.isOpened())
            for index in range(15):
                writer.write(np.full((60, 80, 3), (index * 8, 30, 60), dtype=np.uint8))
            writer.release()
            tracklets = [_tracklet("s1", 0, 4), _tracklet("t1", 8, 12)]
            timeline = build_shadow_resolved_timeline(
                _offline("s1", "t1"),
                tracklets,
                _quality("s1", "t1"),
                fps=FPS,
                generated_at="fixed",
            )
            manifest = build_shadow_timeline_audit_manifest(
                timeline,
                {"tracklets": tracklets},
                benchmark_id="easy",
                benchmark_label="easy90",
                video_path=str(video_path),
                direct_control_limit=0,
                missing_control_limit=0,
                generated_at="fixed",
            )

            rendered = render_shadow_timeline_audit(
                manifest,
                video_path=video_path,
                output_dir=root / "audit",
            )

            self.assertEqual(rendered["render"]["cards"], 1)
            card = cv2.imread(
                str(root / "audit" / "cards" / manifest["items"][0]["card_filename"])
            )
            self.assertEqual(card.shape[:2], (1080, 2400))
            html = (root / "audit" / "index.html").read_text(encoding="utf-8")
            self.assertIn("Same real person?", html)
            self.assertIn("Is timeline state correct?", html)
            self.assertIn("identity_link_invalid", html)
            self.assertIn('id="lightbox"', html)
            self.assertTrue((root / "audit" / "contact_sheets" / "sheet-001.jpg").exists())


if __name__ == "__main__":
    unittest.main()
