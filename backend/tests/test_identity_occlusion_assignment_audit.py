from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from app.services.identity_occlusion_assignment_audit import (
    build_joint_assignment_audit_manifest,
    render_joint_assignment_audit,
)


def _tracklet(tracklet_id: str, start_frame: int, end_frame: int) -> dict:
    return {
        "tracklet_id": tracklet_id,
        "team_label": "A",
        "positions": [
            {
                "frame": frame,
                "time_sec": frame / 10.0,
                "bbox_xyxy": [10 + frame, 10, 30 + frame, 50],
                "pitch_m": [10.0, 20.0],
                "confidence": 0.9,
            }
            for frame in range(start_frame, end_frame + 1)
        ],
    }


def _assignment(assignment_id: str, *, cost: float, current: bool | None) -> dict:
    return {
        "assignment_id": assignment_id,
        "mean_cost": cost,
        "matches_current_identity": current,
        "pairs": [],
        "edges": [],
    }


def _case(case_key: str, status: str, start_frame: int = 3, end_frame: int = 4) -> dict:
    return {
        "case_key": case_key,
        "team_label": "A",
        "start_frame": start_frame,
        "end_frame": end_frame,
        "start_time_sec": start_frame / 10.0,
        "end_time_sec": end_frame / 10.0,
        "source_tracklet_ids": ["1:1", "2:1"],
        "target_tracklet_ids": ["3:1", "4:1"],
        "occlusion_event_ids": ["occlusion-1"],
        "event_confidence": 0.9,
        "assignments": [
            _assignment("assignment_a", cost=0.2, current=True),
            _assignment("assignment_b", cost=0.5, current=False),
        ],
        "decision": {
            "status": status,
            "recommended_assignment_id": "assignment_a" if status != "ambiguous" else None,
            "best_assignment_id": "assignment_a",
            "margin": 0.3,
            "confidence": 0.8,
            "reasons": [],
        },
    }


def _tracklets_doc() -> dict:
    return {
        "tracklets": [
            _tracklet("1:1", 0, 3),
            _tracklet("2:1", 0, 3),
            _tracklet("3:1", 5, 8),
            _tracklet("4:1", 5, 8),
        ]
    }


class JointOcclusionAuditTests(unittest.TestCase):
    def test_manifest_includes_primary_cases_and_limited_controls(self) -> None:
        assignments = {
            "algorithm": {"name": "shadow", "version": "test"},
            "cases": [
                _case("ambiguous", "ambiguous"),
                _case("contradiction", "identity_contradiction"),
                _case("control-1", "keep_current"),
                _case("control-2", "keep_current"),
                _case("blocked", "blocked"),
            ],
        }
        manifest = build_joint_assignment_audit_manifest(
            assignments,
            _tracklets_doc(),
            benchmark_id="hard",
            benchmark_label="hard3m",
            video_path="clip.mp4",
            control_limit=1,
            generated_at="fixed",
        )

        self.assertEqual(manifest["summary"]["review_items"], 3)
        self.assertEqual(
            {item["case_key"] for item in manifest["items"]},
            {"ambiguous", "contradiction", "control-1"},
        )
        self.assertEqual(manifest["items"][0]["manual_review"]["status"], "pending")
        self.assertEqual(manifest["items"][0]["manual_review"]["confirmed_pairs"], [])
        self.assertEqual([row["side_index"] for row in manifest["items"][0]["sources"]], [1, 2])

    def test_renderer_writes_large_cards_and_assignment_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video_path = root / "sample.avi"
            writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (120, 80))
            self.assertTrue(writer.isOpened())
            for index in range(10):
                writer.write(np.full((80, 120, 3), (index * 10, 40, 80), dtype=np.uint8))
            writer.release()
            manifest = build_joint_assignment_audit_manifest(
                {"cases": [_case("ambiguous", "ambiguous")]},
                _tracklets_doc(),
                benchmark_id="hard",
                benchmark_label="hard3m",
                video_path=str(video_path),
                control_limit=0,
                generated_at="fixed",
            )
            rendered = render_joint_assignment_audit(
                manifest,
                video_path=video_path,
                output_dir=root / "audit",
            )

            card_path = root / "audit" / "cards" / rendered["items"][0]["card_filename"]
            card = cv2.imread(str(card_path))
            self.assertEqual(card.shape[:2], (1120, 2400))
            html = (root / "audit" / "index.html").read_text(encoding="utf-8")
            self.assertIn("Assignment A", html)
            self.assertIn("Assignment B", html)
            self.assertIn("Only S2→T1", html)
            self.assertIn('item.manual_review.confirmed_pairs=value==="partial"', html)
            self.assertIn("Download reviewed manifest", html)
            saved = json.loads((root / "audit" / "audit_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["render"]["cards"], 1)


if __name__ == "__main__":
    unittest.main()
