from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from app.services.identity_stitching_audit import (
    build_stitching_audit_manifest,
    render_stitching_audit,
)


def _tracklet(tracklet_id: str, frame: int, time_sec: float, bbox: list[int]) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "source_tracker_id": int(tracklet_id.split(":")[0]),
        "team_label": "A",
        "role": "field_player",
        "positions_m": [
            {
                "frame": frame,
                "time_sec": time_sec,
                "bbox_xyxy": bbox,
                "pitch_m": [10.0, 20.0],
                "confidence": 0.9,
            }
        ],
    }


def _edge(source: str, target: str, *, recommended: bool = True) -> dict:
    return {
        "candidate_key": f"stitch:v1:{source}-{target}",
        "source_tracklet_id": source,
        "target_tracklet_id": target,
        "recommended": recommended,
        "source_quality_class": "recoverable",
        "target_quality_class": "trusted",
        "source_stable_subject_ids": ["slot-A01"],
        "target_stable_subject_ids": ["slot-A01"],
        "current_identity_relation": "same_subject",
        "gap_sec": 0.1,
        "distance_m": 0.3,
        "required_speed_mps": 3.0,
        "cost": 0.12,
        "base_confidence": 0.88,
        "recommendation_votes": 1,
        "recommendation_votes_required": 1,
        "feature_costs": {"gap": 0.1},
        "bonuses": {"same_raw_tracker": 0.08},
        "penalties": {},
        "evidence": ["same_raw_tracker"],
        "occlusion_event_ids": [],
    }


class IdentityStitchingAuditTests(unittest.TestCase):
    def test_manifest_contains_only_recommended_edges_and_maps_visual_clip_time(self) -> None:
        tracklets = {
            "tracklets": [
                _tracklet("1:1", 8100, 270.3, [10, 10, 30, 50]),
                _tracklet("1:2", 8104, 270.4, [12, 10, 32, 50]),
                _tracklet("2:1", 8110, 270.6, [40, 10, 60, 50]),
            ]
        }
        stitching = {
            "algorithm": {"name": "shadow", "version": "test"},
            "candidate_edges": [
                _edge("1:1", "1:2"),
                _edge("1:2", "2:1", recommended=False),
            ],
        }

        result = build_stitching_audit_manifest(
            stitching,
            tracklets,
            benchmark_id="hard",
            benchmark_label="hard3m",
            video_path="clip.mp4",
            video_time_offset_sec=270.0,
            generated_at="fixed",
        )

        self.assertEqual(result["summary"]["review_items"], 1)
        self.assertEqual(result["items"][0]["source"]["video_time_sec"], 0.3)
        self.assertEqual(result["items"][0]["target"]["video_time_sec"], 0.4)
        self.assertEqual(result["items"][0]["manual_review"]["status"], "pending")
        self.assertEqual(result["items"][0]["decision"]["current_identity_relation"], "same_subject")

    def test_manifest_reports_missing_tracklets_instead_of_guessing(self) -> None:
        result = build_stitching_audit_manifest(
            {"candidate_edges": [_edge("1:1", "missing:1")]},
            {"tracklets": [_tracklet("1:1", 0, 0.0, [10, 10, 30, 50])]},
            benchmark_id="easy",
            benchmark_label="easy90",
            video_path="clip.mp4",
            generated_at="fixed",
        )

        self.assertEqual(result["summary"]["review_items"], 0)
        self.assertEqual(result["summary"]["skipped"], 1)
        self.assertEqual(result["skipped"][0]["reason"], "missing_tracklet")

    def test_renderer_writes_card_manifest_html_and_contact_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video_path = root / "sample.avi"
            writer = cv2.VideoWriter(
                str(video_path),
                cv2.VideoWriter_fourcc(*"MJPG"),
                10.0,
                (80, 60),
            )
            self.assertTrue(writer.isOpened())
            for index in range(10):
                frame = np.full((60, 80, 3), (index * 10, 30, 60), dtype=np.uint8)
                writer.write(frame)
            writer.release()
            manifest = build_stitching_audit_manifest(
                {"candidate_edges": [_edge("1:1", "1:2")]},
                {
                    "tracklets": [
                        _tracklet("1:1", 2, 0.2, [10, 10, 30, 50]),
                        _tracklet("1:2", 4, 0.4, [12, 10, 32, 50]),
                    ]
                },
                benchmark_id="easy",
                benchmark_label="easy90",
                video_path=str(video_path),
                generated_at="fixed",
            )

            rendered = render_stitching_audit(
                manifest,
                video_path=video_path,
                output_dir=root / "audit",
                cards_per_sheet=4,
            )

            self.assertEqual(rendered["render"]["cards"], 1)
            card_path = root / "audit" / "cards" / manifest["items"][0]["card_filename"]
            self.assertTrue(card_path.exists())
            card = cv2.imread(str(card_path))
            self.assertEqual(card.shape[:2], (1080, 2400))
            self.assertTrue((root / "audit" / "contact_sheets" / "sheet-001.jpg").exists())
            self.assertTrue((root / "audit" / "index.html").exists())
            html = (root / "audit" / "index.html").read_text(encoding="utf-8")
            self.assertIn("Download reviewed manifest", html)
            self.assertIn("confirmed_same", html)
            self.assertIn('id="lightbox"', html)
            self.assertIn('class="card-image"', html)
            saved = json.loads((root / "audit" / "audit_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["summary"]["pending"], 1)


if __name__ == "__main__":
    unittest.main()
