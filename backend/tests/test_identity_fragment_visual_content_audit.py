from __future__ import annotations

import unittest

from app.services.identity_fragment_visual_content_audit import (
    build_identity_fragment_visual_content_audit_manifest,
)


class IdentityFragmentVisualContentAuditTests(unittest.TestCase):
    def test_selects_different_uncertain_and_strict_auto_accept_endpoints(self) -> None:
        different = _proposal("different", frame=10, strict=False)
        same_strict = _proposal("strict", frame=20, strict=True)
        same_manual = _proposal("manual", frame=30, strict=False)
        manifest = build_identity_fragment_visual_content_audit_manifest(
            {"algorithm": {"name": "candidate"}, "proposals": [different, same_strict, same_manual]},
            {
                "goldset_id": "gold",
                "version": "1",
                "items": [
                    _gold("different", "confirmed_different"),
                    _gold("strict", "confirmed_same"),
                    _gold("manual", "confirmed_same"),
                ],
            },
            benchmark_id="bench",
            benchmark_label="easy",
            video_path="video.mp4",
            generated_at="fixed",
        )

        self.assertEqual(manifest["audit_kind"], "fragment_endpoint_content")
        self.assertEqual(manifest["summary"]["review_items"], 4)
        reasons = {reason for item in manifest["items"] for reason in item["selection_reasons"]}
        self.assertIn("identity_goldset_confirmed_different", reasons)
        self.assertIn("strict_shadow_auto_accept_candidate", reasons)

    def test_deduplicates_shared_endpoint(self) -> None:
        first = _proposal("one", frame=10, strict=False)
        second = _proposal("two", frame=10, strict=False)
        second["source_candidate_subject_id"] = first["source_candidate_subject_id"]
        second["source_endpoint"] = first["source_endpoint"]
        manifest = build_identity_fragment_visual_content_audit_manifest(
            {"proposals": [first, second]},
            {
                "items": [
                    _gold("one", "uncertain"),
                    _gold("two", "uncertain"),
                ]
            },
            benchmark_id="bench",
            benchmark_label="hard",
            video_path="video.mp4",
        )

        self.assertEqual(manifest["summary"]["review_items"], 3)
        shared = next(item for item in manifest["items"] if len(item["proposal_keys"]) == 2)
        self.assertEqual(shared["proposal_keys"], ["one", "two"])


def _proposal(key: str, *, frame: int, strict: bool) -> dict:
    proposal = {
        "proposal_key": key,
        "decision": "recommended_review" if strict else "needs_review",
        "confidence": 0.9 if strict else 0.5,
        "gap_frames": 10,
        "gap_seconds": 0.4,
        "endpoint_distance_m": 0.8,
        "required_speed_mps": 2.0,
        "source_active_ratio": 0.95,
        "target_active_ratio": 0.95,
        "source_team_label": "A",
        "target_team_label": "A",
        "shared_production_anchor": "slot-A01",
        "reason_codes": [] if strict else ["review"],
        "overlap_frames": 0,
        "source_candidate_subject_id": f"source-{key}",
        "target_candidate_subject_id": f"target-{key}",
        "source_candidate_player_id": "A01",
        "target_candidate_player_id": "A01~2",
        "source_endpoint": {"frame": frame, "bbox_xyxy": [10, 10, 30, 70]},
        "target_endpoint": {"frame": frame + 1, "bbox_xyxy": [12, 10, 32, 70]},
    }
    return proposal


def _gold(key: str, status: str) -> dict:
    return {
        "benchmark_id": "bench",
        "candidate_key": key,
        "review_status": status,
    }


if __name__ == "__main__":
    unittest.main()
